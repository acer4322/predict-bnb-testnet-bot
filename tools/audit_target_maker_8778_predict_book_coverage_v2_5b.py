from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import zlib
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

REPORT_VERSION = "TARGET_MAKER_8778_PREDICT_BOOK_COVERAGE_V2_5B"
DEFAULT_DB = "data/wallet_maker_book_inference.db"
DEFAULT_OUT = "target_maker_8778_predict_book_coverage_v2_5b.json"
DEFAULT_SPECIAL_START = "2026-08-16T00:00:00+08:00"
DEFAULT_SPECIAL_END = "2026-08-16T12:00:00+08:00"
BOOK_TABLE = "maker_book_inference_updates"
PARENT_TABLE = "maker_lifecycle_v2_1_events"
LEGACY_STALE_BOOK_DB_BASENAMES = {"wallet_taker_signals.db"}
DECIMAL_EPS = Decimal("1e-12")


@dataclass(frozen=True)
class Parent:
    market_id: int
    order_hash: str
    placement_first_ms: int
    target_side: str
    confidence_tier: str
    placement_coverage: str
    fill_allocation_tier: str
    fill_allocation_coverage: str
    include_master: bool
    eligibility_label: str
    filter_reasons: Tuple[str, ...]


@dataclass(frozen=True)
class BookUpdate:
    id: int
    market_id: int
    source_timestamp_ms: Optional[int]
    received_at_ms: Optional[int]
    is_checkpoint: bool
    native_bids_z: Optional[bytes]
    native_asks_z: Optional[bytes]
    changes_z: Optional[bytes]

    @property
    def checkpoint_available(self) -> bool:
        return bool(self.is_checkpoint and self.native_bids_z is not None and self.native_asks_z is not None)


@dataclass
class ReplayResult:
    mode: str
    cutoff_policy: str
    checkpoint_available: bool = False
    delta_chain_applied: bool = False
    final_book_valid: bool = False
    reconstructable: bool = False
    checkpoint_update_id: Optional[int] = None
    latest_update_id: Optional[int] = None
    checkpoint_timestamp_ms: Optional[int] = None
    latest_update_ms: Optional[int] = None
    checkpoint_age_ms: Optional[int] = None
    latest_age_ms: Optional[int] = None
    delta_count_applied: int = 0
    level_change_count_applied: int = 0
    mismatch_count: int = 0
    checkpoint_mismatch_count: int = 0
    decode_error_count: int = 0
    missing_change_payload_count: int = 0
    delta_field_mismatch_count: int = 0
    source_timestamp_regression_count: int = 0
    ordering_timestamp_regression_count: int = 0
    post_cutoff_regression_rows: int = 0
    bid_levels: int = 0
    ask_levels: int = 0
    best_bid: Optional[str] = None
    best_ask: Optional[str] = None
    crossed_book: bool = False
    error_samples: Optional[List[str]] = None

    def to_json(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["checkpointAvailable"] = payload.pop("checkpoint_available")
        payload["deltaChainApplied"] = payload.pop("delta_chain_applied")
        payload["finalBookValid"] = payload.pop("final_book_valid")
        payload["checkpointUpdateId"] = payload.pop("checkpoint_update_id")
        payload["latestUpdateId"] = payload.pop("latest_update_id")
        payload["checkpointTimestampMs"] = payload.pop("checkpoint_timestamp_ms")
        payload["latestUpdateMs"] = payload.pop("latest_update_ms")
        payload["checkpointAgeMs"] = payload.pop("checkpoint_age_ms")
        payload["latestAgeMs"] = payload.pop("latest_age_ms")
        payload["deltaCountApplied"] = payload.pop("delta_count_applied")
        payload["levelChangeCountApplied"] = payload.pop("level_change_count_applied")
        payload["mismatchCount"] = payload.pop("mismatch_count")
        payload["checkpointMismatchCount"] = payload.pop("checkpoint_mismatch_count")
        payload["decodeErrorCount"] = payload.pop("decode_error_count")
        payload["missingChangePayloadCount"] = payload.pop("missing_change_payload_count")
        payload["deltaFieldMismatchCount"] = payload.pop("delta_field_mismatch_count")
        payload["sourceTimestampRegressionCount"] = payload.pop("source_timestamp_regression_count")
        payload["orderingTimestampRegressionCount"] = payload.pop("ordering_timestamp_regression_count")
        payload["postCutoffRegressionRows"] = payload.pop("post_cutoff_regression_rows")
        payload["bidLevels"] = payload.pop("bid_levels")
        payload["askLevels"] = payload.pop("ask_levels")
        payload["bestBid"] = payload.pop("best_bid")
        payload["bestAsk"] = payload.pop("best_ask")
        payload["crossedBook"] = payload.pop("crossed_book")
        payload["errorSamples"] = payload.pop("error_samples") or []
        payload["cutoffPolicy"] = payload.pop("cutoff_policy")
        return payload


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Bounded V2.5b audit of Target Maker parents against the live 8778 Predict book source. "
            "Actually reconstructs strict-pre books from native checkpoints + changes_z."
        )
    )
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite DB containing lifecycle parents and 8778 book updates")
    p.add_argument("--book-db", default=None, help="Optional separate 8778 book DB; defaults to --db")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--special-start", default=DEFAULT_SPECIAL_START, help="Inclusive ISO-8601 start")
    p.add_argument("--special-end", default=DEFAULT_SPECIAL_END, help="Exclusive ISO-8601 end")
    return p.parse_args()


def iso_to_ms(value: str) -> int:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp must include timezone offset: {value}")
    return int(dt.timestamp() * 1000)


def ms_to_iso(ms: Optional[int], tz: timezone = timezone(timedelta(hours=8))) -> Optional[str]:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=tz).isoformat(timespec="milliseconds")


def hour_key(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone(timedelta(hours=8)))
    return dt.replace(minute=0, second=0, microsecond=0).isoformat()


def assert_not_legacy_book_db(book_db: str) -> None:
    base = os.path.basename(os.path.abspath(book_db)).lower()
    if base in LEGACY_STALE_BOOK_DB_BASENAMES:
        raise SystemExit(
            "REFUSING STALE PUBLIC MARKET SOURCE: V2.5b must use the live 8778 "
            f"{BOOK_TABLE} checkpoint+changes_z source, not {base}."
        )


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def require_table(conn: sqlite3.Connection, table: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone()
    if row is None:
        raise SystemExit(f"required table missing: {table}")


def row_text(row: Mapping[str, Any], key: str) -> str:
    value = row[key] if key in row.keys() else None
    return "" if value is None else str(value)


def classify_parent(row: sqlite3.Row) -> Parent:
    confidence = row_text(row, "confidence_tier").upper()
    placement_coverage = row_text(row, "placement_coverage").upper()
    fill_tier = row_text(row, "fill_allocation_tier").upper()
    fill_coverage = row_text(row, "fill_allocation_coverage").upper()

    confidence_pass = confidence in {"HIGH", "MEDIUM"}
    placement_pass = placement_coverage == "FULL"
    fill_pass = fill_tier == "HIGH" and fill_coverage == "FULL"

    reasons: List[str] = []
    if not confidence_pass:
        reasons.append("LOW_CONFIDENCE_CHAIN_LINK")
    if not placement_pass:
        reasons.append(f"PLACEMENT_COVERAGE_{placement_coverage or 'MISSING'}")
    if not fill_pass:
        reasons.append(f"FILL_ALLOCATION_{fill_tier or 'MISSING'}_{fill_coverage or 'MISSING'}")

    eligible = confidence_pass and placement_pass and fill_pass
    side = (
        row_text(row, "target_side")
        or row_text(row, "placement_side")
        or row_text(row, "side")
    ).upper()
    return Parent(
        market_id=int(row["market_id"]),
        order_hash=row_text(row, "order_hash"),
        placement_first_ms=int(row["placement_first_ms"]),
        target_side=side,
        confidence_tier=confidence,
        placement_coverage=placement_coverage,
        fill_allocation_tier=fill_tier,
        fill_allocation_coverage=fill_coverage,
        include_master=eligible,
        eligibility_label="CLEAN_TARGET" if eligible else "FILTERED",
        filter_reasons=tuple(reasons),
    )


def load_parents(conn: sqlite3.Connection, start_ms: int, end_ms: int) -> List[Parent]:
    rows = conn.execute(
        f"""
        SELECT *
        FROM {PARENT_TABLE}
        WHERE event_type = 'PLACEMENT'
          AND placement_first_ms >= ?
          AND placement_first_ms < ?
        ORDER BY placement_first_ms, market_id, order_hash
        """,
        (start_ms, end_ms),
    ).fetchall()
    return [classify_parent(r) for r in rows]


def to_blob(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    raise TypeError(f"unexpected SQLite blob type: {type(value).__name__}")


def load_updates(conn: sqlite3.Connection, market_ids: Sequence[int]) -> Dict[int, List[BookUpdate]]:
    out: Dict[int, List[BookUpdate]] = {int(m): [] for m in market_ids}
    if not market_ids:
        return out
    chunk_size = 400
    for offset in range(0, len(market_ids), chunk_size):
        chunk = list(market_ids[offset : offset + chunk_size])
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT id, market_id, source_timestamp_ms, received_at_ms,
                   is_checkpoint, native_bids_z, native_asks_z, changes_z
            FROM {BOOK_TABLE}
            WHERE market_id IN ({marks})
            ORDER BY market_id, id
            """,
            chunk,
        ).fetchall()
        for r in rows:
            market_id = int(r["market_id"])
            out.setdefault(market_id, []).append(
                BookUpdate(
                    id=int(r["id"]),
                    market_id=market_id,
                    source_timestamp_ms=int(r["source_timestamp_ms"]) if r["source_timestamp_ms"] is not None else None,
                    received_at_ms=int(r["received_at_ms"]) if r["received_at_ms"] is not None else None,
                    is_checkpoint=bool(r["is_checkpoint"]),
                    native_bids_z=to_blob(r["native_bids_z"]),
                    native_asks_z=to_blob(r["native_asks_z"]),
                    changes_z=to_blob(r["changes_z"]),
                )
            )
    return out


def decode_zlib_json(blob: bytes) -> Any:
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or isinstance(value, bool):
        raise InvalidOperation(f"not a numeric value: {value!r}")
    d = Decimal(str(value))
    if not d.is_finite():
        raise InvalidOperation(f"non-finite numeric value: {value!r}")
    if d == 0:
        return Decimal(0)
    return d


def close_dec(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= DECIMAL_EPS


def extract_level_size(item: Mapping[str, Any]) -> Any:
    for key in ("size", "qty", "quantity", "amount", "volume"):
        if key in item:
            return item[key]
    raise ValueError(f"book level missing size/qty field: keys={sorted(item.keys())}")


def normalize_book_side(raw: Any, side_name: str) -> Dict[Decimal, Decimal]:
    if isinstance(raw, Mapping):
        for key in (side_name, side_name.rstrip("s")):
            if key in raw and len(raw) == 1:
                return normalize_book_side(raw[key], side_name)
        result: Dict[Decimal, Decimal] = {}
        for price, size in raw.items():
            p = dec(price)
            s = dec(size)
            if s < 0:
                raise ValueError(f"negative {side_name} size at {p}: {s}")
            if s != 0:
                result[p] = s
        return result

    if isinstance(raw, list):
        result = {}
        for item in raw:
            if isinstance(item, Mapping):
                if "price" not in item:
                    raise ValueError(f"book level missing price: keys={sorted(item.keys())}")
                p = dec(item["price"])
                s = dec(extract_level_size(item))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                p = dec(item[0])
                s = dec(item[1])
            else:
                raise ValueError(f"unsupported {side_name} book level shape: {item!r}")
            if s < 0:
                raise ValueError(f"negative {side_name} size at {p}: {s}")
            if s != 0:
                result[p] = s
        return result

    raise ValueError(f"unsupported {side_name} native book shape: {type(raw).__name__}")


def decode_checkpoint(update: BookUpdate) -> Tuple[Dict[Decimal, Decimal], Dict[Decimal, Decimal]]:
    if not update.checkpoint_available:
        raise ValueError("checkpoint blobs unavailable")
    bids_raw = decode_zlib_json(update.native_bids_z or b"")
    asks_raw = decode_zlib_json(update.native_asks_z or b"")
    return normalize_book_side(bids_raw, "bids"), normalize_book_side(asks_raw, "asks")


def normalize_changes(raw: Any) -> Dict[str, List[Mapping[str, Any]]]:
    result: Dict[str, List[Mapping[str, Any]]] = {"bids": [], "asks": []}
    if raw in ({}, [], None):
        return result

    if isinstance(raw, Mapping) and "changes" in raw and len(raw) == 1:
        return normalize_changes(raw["changes"])

    if isinstance(raw, Mapping):
        recognized = False
        side_aliases = {
            "bids": ("bids", "bid", "buy", "buys"),
            "asks": ("asks", "ask", "sell", "sells"),
        }
        for canonical, aliases in side_aliases.items():
            for alias in aliases:
                if alias in raw:
                    recognized = True
                    values = raw[alias]
                    if values is None:
                        continue
                    if not isinstance(values, list):
                        raise ValueError(f"changes[{alias}] must be a list")
                    for item in values:
                        if not isinstance(item, Mapping):
                            raise ValueError(f"change item must be object, got {item!r}")
                        result[canonical].append(item)
        if recognized:
            return result

        price_keyed = True
        for price, item in raw.items():
            if not isinstance(item, Mapping) or "side" not in item:
                price_keyed = False
                break
            merged = dict(item)
            merged.setdefault("price", price)
            side = str(merged.get("side", "")).upper()
            if side in {"BID", "BIDS", "BUY"}:
                result["bids"].append(merged)
            elif side in {"ASK", "ASKS", "SELL"}:
                result["asks"].append(merged)
            else:
                raise ValueError(f"unsupported change side: {side!r}")
        if price_keyed:
            return result

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, Mapping):
                raise ValueError(f"change item must be object, got {item!r}")
            side = str(item.get("side", "")).upper()
            if side in {"BID", "BIDS", "BUY"}:
                result["bids"].append(item)
            elif side in {"ASK", "ASKS", "SELL"}:
                result["asks"].append(item)
            else:
                raise ValueError(f"list change missing/unsupported side: {side!r}")
        return result

    raise ValueError(f"unsupported changes_z JSON shape: {type(raw).__name__}")


def apply_change_item(book: Dict[Decimal, Decimal], item: Mapping[str, Any]) -> Tuple[bool, bool]:
    for required in ("price", "before", "after"):
        if required not in item:
            raise ValueError(f"change missing {required}: keys={sorted(item.keys())}")
    price = dec(item["price"])
    before = dec(item["before"])
    after = dec(item["after"])
    if before < 0 or after < 0:
        raise ValueError(f"negative before/after at {price}: {before}->{after}")

    current = book.get(price, Decimal(0))
    before_ok = close_dec(current, before)

    delta_ok = True
    if "delta" in item and item["delta"] is not None:
        supplied = dec(item["delta"])
        delta_ok = close_dec(supplied, after - before)

    if after == 0:
        book.pop(price, None)
    else:
        book[price] = after
    return before_ok, delta_ok


def books_equal(a: Dict[Decimal, Decimal], b: Dict[Decimal, Decimal]) -> bool:
    keys = set(a) | set(b)
    return all(close_dec(a.get(k, Decimal(0)), b.get(k, Decimal(0))) for k in keys)


def append_error(result: ReplayResult, message: str) -> None:
    if result.error_samples is None:
        result.error_samples = []
    if len(result.error_samples) < 5:
        result.error_samples.append(message)


def timestamp_for(update: BookUpdate, mode: str) -> Optional[int]:
    if mode == "receivedStrict":
        return update.received_at_ms
    if mode == "sourceStrict":
        return update.source_timestamp_ms
    raise ValueError(f"unknown mode: {mode}")


def count_regressions(values: Iterable[Optional[int]]) -> int:
    count = 0
    prev: Optional[int] = None
    for value in values:
        if value is None:
            continue
        if prev is not None and value < prev:
            count += 1
        prev = value
    return count


def replay_strict_pre(updates: Sequence[BookUpdate], placement_ms: int, mode: str) -> ReplayResult:
    policy = (
        "contiguous_id_prefix_before_first_received_cutoff"
        if mode == "receivedStrict"
        else "conservative_contiguous_id_prefix_before_first_source_cutoff"
    )
    result = ReplayResult(mode=mode, cutoff_policy=policy)
    if not updates:
        append_error(result, "no retained 8778 updates for market")
        return result

    boundary = len(updates)
    for idx, update in enumerate(updates):
        ts = timestamp_for(update, mode)
        if ts is None or ts >= placement_ms:
            boundary = idx
            break

    prefix = updates[:boundary]
    suffix = updates[boundary:]
    result.post_cutoff_regression_rows = sum(
        1
        for u in suffix[1:]
        if (timestamp_for(u, mode) is not None and timestamp_for(u, mode) < placement_ms)
    ) if suffix else 0

    result.ordering_timestamp_regression_count = count_regressions(timestamp_for(u, mode) for u in prefix)
    result.source_timestamp_regression_count = count_regressions(u.source_timestamp_ms for u in prefix)

    checkpoint_idx: Optional[int] = None
    for idx in range(len(prefix) - 1, -1, -1):
        if prefix[idx].checkpoint_available:
            checkpoint_idx = idx
            break

    if checkpoint_idx is None:
        append_error(result, "no complete checkpoint in strict-pre contiguous id prefix")
        return result

    checkpoint = prefix[checkpoint_idx]
    result.checkpoint_available = True
    result.checkpoint_update_id = checkpoint.id
    result.checkpoint_timestamp_ms = timestamp_for(checkpoint, mode)
    if result.checkpoint_timestamp_ms is not None:
        result.checkpoint_age_ms = placement_ms - result.checkpoint_timestamp_ms

    try:
        bids, asks = decode_checkpoint(checkpoint)
    except Exception as exc:
        result.decode_error_count += 1
        append_error(result, f"checkpoint decode id={checkpoint.id}: {exc}")
        return result

    chain_ok = True
    for update in prefix[checkpoint_idx + 1 :]:
        if update.changes_z is None:
            result.missing_change_payload_count += 1
            chain_ok = False
            append_error(result, f"missing changes_z id={update.id}")
            continue
        try:
            raw_changes = decode_zlib_json(update.changes_z)
            changes = normalize_changes(raw_changes)
        except Exception as exc:
            result.decode_error_count += 1
            chain_ok = False
            append_error(result, f"changes decode id={update.id}: {exc}")
            continue

        row_levels = 0
        for side_name, book in (("bids", bids), ("asks", asks)):
            for item in changes[side_name]:
                row_levels += 1
                try:
                    before_ok, delta_ok = apply_change_item(book, item)
                except Exception as exc:
                    result.decode_error_count += 1
                    chain_ok = False
                    append_error(result, f"apply {side_name} id={update.id}: {exc}")
                    continue
                if not before_ok:
                    result.mismatch_count += 1
                    chain_ok = False
                    append_error(result, f"before mismatch {side_name} id={update.id}")
                if not delta_ok:
                    result.delta_field_mismatch_count += 1
        result.delta_count_applied += 1
        result.level_change_count_applied += row_levels

        if update.checkpoint_available:
            try:
                checkpoint_bids, checkpoint_asks = decode_checkpoint(update)
                if not books_equal(bids, checkpoint_bids) or not books_equal(asks, checkpoint_asks):
                    result.checkpoint_mismatch_count += 1
                    chain_ok = False
                    append_error(result, f"checkpoint cross-check mismatch id={update.id}")
            except Exception as exc:
                result.decode_error_count += 1
                chain_ok = False
                append_error(result, f"checkpoint cross-check decode id={update.id}: {exc}")

    result.delta_chain_applied = result.delta_count_applied > 0
    last = prefix[-1]
    result.latest_update_id = last.id
    result.latest_update_ms = timestamp_for(last, mode)
    if result.latest_update_ms is not None:
        result.latest_age_ms = placement_ms - result.latest_update_ms

    result.bid_levels = len(bids)
    result.ask_levels = len(asks)
    if bids:
        best_bid = max(bids)
        result.best_bid = format(best_bid, "f")
    else:
        best_bid = None
    if asks:
        best_ask = min(asks)
        result.best_ask = format(best_ask, "f")
    else:
        best_ask = None
    result.crossed_book = bool(best_bid is not None and best_ask is not None and best_bid >= best_ask)

    sane_levels = all(price >= 0 and size > 0 for price, size in list(bids.items()) + list(asks.items()))
    nonempty = bool(bids or asks)
    result.final_book_valid = bool(
        chain_ok
        and nonempty
        and sane_levels
        and result.decode_error_count == 0
        and result.mismatch_count == 0
        and result.checkpoint_mismatch_count == 0
        and result.missing_change_payload_count == 0
    )
    result.reconstructable = bool(result.checkpoint_available and result.final_book_valid)
    return result


def quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(ordered[lo])
    weight = pos - lo
    return float(ordered[lo] * (1 - weight) + ordered[hi] * weight)


def summarize_replays(records: Sequence[Tuple[Parent, ReplayResult]], clean_only: bool = False) -> Dict[str, Any]:
    chosen = [(p, r) for p, r in records if (p.include_master or not clean_only)]
    n = len(chosen)
    checkpoint = sum(1 for _, r in chosen if r.checkpoint_available)
    final_valid = sum(1 for _, r in chosen if r.final_book_valid)
    recon = sum(1 for _, r in chosen if r.reconstructable)
    latest_ages = [float(r.latest_age_ms) for _, r in chosen if r.latest_age_ms is not None]
    checkpoint_ages = [float(r.checkpoint_age_ms) for _, r in chosen if r.checkpoint_age_ms is not None]
    return {
        "parents": n,
        "checkpointAvailable": checkpoint,
        "checkpointAvailableRate": checkpoint / n if n else None,
        "finalBookValid": final_valid,
        "finalBookValidRate": final_valid / n if n else None,
        "reconstructable": recon,
        "reconstructableRate": recon / n if n else None,
        "latestAgeMs": {
            "p50": quantile(latest_ages, 0.50),
            "p90": quantile(latest_ages, 0.90),
            "p99": quantile(latest_ages, 0.99),
            "lte2sRate": sum(1 for x in latest_ages if x <= 2000) / len(latest_ages) if latest_ages else None,
            "lte5sRate": sum(1 for x in latest_ages if x <= 5000) / len(latest_ages) if latest_ages else None,
        },
        "checkpointAgeMs": {
            "p50": quantile(checkpoint_ages, 0.50),
            "p90": quantile(checkpoint_ages, 0.90),
            "p99": quantile(checkpoint_ages, 0.99),
        },
        "deltaCountApplied": sum(r.delta_count_applied for _, r in chosen),
        "levelChangeCountApplied": sum(r.level_change_count_applied for _, r in chosen),
        "mismatchCount": sum(r.mismatch_count for _, r in chosen),
        "checkpointMismatchCount": sum(r.checkpoint_mismatch_count for _, r in chosen),
        "decodeErrorCount": sum(r.decode_error_count for _, r in chosen),
        "missingChangePayloadCount": sum(r.missing_change_payload_count for _, r in chosen),
        "deltaFieldMismatchCount": sum(r.delta_field_mismatch_count for _, r in chosen),
        "sourceTimestampRegressionCount": sum(r.source_timestamp_regression_count for _, r in chosen),
        "postCutoffRegressionRows": sum(r.post_cutoff_regression_rows for _, r in chosen),
    }


def funnel_counts(parents: Sequence[Parent]) -> Dict[str, int]:
    raw = len(parents)
    confidence = [p for p in parents if p.confidence_tier in {"HIGH", "MEDIUM"}]
    placement = [p for p in confidence if p.placement_coverage == "FULL"]
    fill = [
        p
        for p in placement
        if p.fill_allocation_tier == "HIGH" and p.fill_allocation_coverage == "FULL"
    ]
    return {
        "RAW_PARENT": raw,
        "CONFIDENCE_PASS": len(confidence),
        "PLACEMENT_COVERAGE_PASS": len(placement),
        "FILL_ALLOCATION_PASS": len(fill),
        "CLEAN_TARGET": sum(1 for p in parents if p.include_master),
    }


def build_hourly_funnel(parents: Sequence[Parent], start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Parent]] = defaultdict(list)
    for parent in parents:
        grouped[hour_key(parent.placement_first_ms)].append(parent)
    rows: List[Dict[str, Any]] = []
    cursor = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone(timedelta(hours=8))).replace(
        minute=0, second=0, microsecond=0
    )
    end = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone(timedelta(hours=8)))
    while cursor < end:
        key = cursor.isoformat()
        ps = grouped.get(key, [])
        rows.append({"hour": key, **funnel_counts(ps), "markets": sorted({p.market_id for p in ps})})
        cursor += timedelta(hours=1)
    return rows


def retained_range(updates: Sequence[BookUpdate], attr: str) -> Tuple[Optional[int], Optional[int]]:
    values = [getattr(u, attr) for u in updates if getattr(u, attr) is not None]
    return (min(values), max(values)) if values else (None, None)


def build_raw_market_coverage(
    parents: Sequence[Parent],
    updates_by_market: Mapping[int, Sequence[BookUpdate]],
    start_ms: int,
    end_ms: int,
) -> List[Dict[str, Any]]:
    parents_by_market: Dict[int, List[Parent]] = defaultdict(list)
    for parent in parents:
        parents_by_market[parent.market_id].append(parent)

    rows = []
    for market_id in sorted(parents_by_market):
        updates = list(updates_by_market.get(market_id, []))
        first_source, last_source = retained_range(updates, "source_timestamp_ms")
        first_received, last_received = retained_range(updates, "received_at_ms")
        source_window = [u for u in updates if u.source_timestamp_ms is not None and start_ms <= u.source_timestamp_ms < end_ms]
        received_window = [u for u in updates if u.received_at_ms is not None and start_ms <= u.received_at_ms < end_ms]
        ps = parents_by_market[market_id]
        rows.append(
            {
                "marketId": market_id,
                "parentsRaw": len(ps),
                "parentsClean": sum(1 for p in ps if p.include_master),
                "retained8778Updates": len(updates),
                "retained8778Checkpoints": sum(1 for u in updates if u.checkpoint_available),
                "hasAnyRetained8778Data": bool(updates),
                "firstSourceMs": first_source,
                "firstSourceIso": ms_to_iso(first_source),
                "lastSourceMs": last_source,
                "lastSourceIso": ms_to_iso(last_source),
                "firstReceivedMs": first_received,
                "firstReceivedIso": ms_to_iso(first_received),
                "lastReceivedMs": last_received,
                "lastReceivedIso": ms_to_iso(last_received),
                "windowSourceUpdates": len(source_window),
                "windowSourceCheckpoints": sum(1 for u in source_window if u.checkpoint_available),
                "windowReceivedUpdates": len(received_window),
                "windowReceivedCheckpoints": sum(1 for u in received_window if u.checkpoint_available),
            }
        )
    return rows


def by_hour_reconstruction(
    parents: Sequence[Parent],
    received_records: Sequence[Tuple[Parent, ReplayResult]],
    source_records: Sequence[Tuple[Parent, ReplayResult]],
) -> List[Dict[str, Any]]:
    received_map = {id(p): r for p, r in received_records}
    source_map = {id(p): r for p, r in source_records}
    grouped: Dict[str, List[Parent]] = defaultdict(list)
    for p in parents:
        grouped[hour_key(p.placement_first_ms)].append(p)
    rows = []
    for hour in sorted(grouped):
        ps = grouped[hour]
        rec = [(p, received_map[id(p)]) for p in ps]
        src = [(p, source_map[id(p)]) for p in ps]
        rows.append(
            {
                "hour": hour,
                **funnel_counts(ps),
                "receivedStrict": {
                    "ALL_PARENTS": summarize_replays(rec, clean_only=False),
                    "CLEAN_TARGET": summarize_replays(rec, clean_only=True),
                },
                "sourceStrict": {
                    "ALL_PARENTS": summarize_replays(src, clean_only=False),
                    "CLEAN_TARGET": summarize_replays(src, clean_only=True),
                },
            }
        )
    return rows


def gate(received_clean: Dict[str, Any]) -> Dict[str, Any]:
    n = int(received_clean.get("parents") or 0)
    rate = received_clean.get("reconstructableRate")
    if n == 0:
        status = "STOP_NO_CLEAN_TARGET"
        reason = "No CLEAN_TARGET parents in bounded discovery window."
    elif rate is not None and rate >= 0.90:
        status = "PASS_SPECIAL_8778_RECONSTRUCTION"
        reason = "Bounded CLEAN_TARGET receivedStrict true reconstruction rate is >= 90%."
    elif rate is not None and rate >= 0.70:
        status = "PARTIAL_SPECIAL_8778_RECONSTRUCTION"
        reason = "Bounded CLEAN_TARGET receivedStrict true reconstruction rate is 70-90%; inspect gaps before stress test."
    else:
        status = "STOP_SPECIAL_8778_RECONSTRUCTION"
        reason = "Bounded CLEAN_TARGET receivedStrict true reconstruction rate is below 70%."
    return {
        "status": status,
        "primaryMode": "receivedStrict",
        "thresholdPass": 0.90,
        "thresholdPartial": 0.70,
        "cleanTargetParents": n,
        "cleanTargetReconstructableRate": rate,
        "reason": reason,
    }


def main() -> None:
    args = parse_args()
    db_path = args.db
    book_db_path = args.book_db or args.db
    assert_not_legacy_book_db(book_db_path)

    start_ms = iso_to_ms(args.special_start)
    end_ms = iso_to_ms(args.special_end)
    if end_ms <= start_ms:
        raise SystemExit("--special-end must be strictly after --special-start")

    lifecycle_conn = connect(db_path)
    try:
        require_table(lifecycle_conn, PARENT_TABLE)
        parents = load_parents(lifecycle_conn, start_ms, end_ms)
    finally:
        lifecycle_conn.close()

    market_ids = sorted({p.market_id for p in parents})
    book_conn = connect(book_db_path)
    try:
        require_table(book_conn, BOOK_TABLE)
        updates_by_market = load_updates(book_conn, market_ids)
    finally:
        book_conn.close()

    received_records: List[Tuple[Parent, ReplayResult]] = []
    source_records: List[Tuple[Parent, ReplayResult]] = []
    parent_rows: List[Dict[str, Any]] = []

    for parent in parents:
        updates = updates_by_market.get(parent.market_id, [])
        received = replay_strict_pre(updates, parent.placement_first_ms, "receivedStrict")
        source = replay_strict_pre(updates, parent.placement_first_ms, "sourceStrict")
        received_records.append((parent, received))
        source_records.append((parent, source))
        parent_rows.append(
            {
                "marketId": parent.market_id,
                "orderHash": parent.order_hash,
                "placementFirstMs": parent.placement_first_ms,
                "placementFirstIso": ms_to_iso(parent.placement_first_ms),
                "targetSide": parent.target_side,
                "confidenceTier": parent.confidence_tier,
                "placementCoverage": parent.placement_coverage,
                "fillAllocationTier": parent.fill_allocation_tier,
                "fillAllocationCoverage": parent.fill_allocation_coverage,
                "eligibilityLabel": parent.eligibility_label,
                "filterReasons": list(parent.filter_reasons),
                "receivedStrict": received.to_json(),
                "sourceStrict": source.to_json(),
            }
        )

    received_all = summarize_replays(received_records, clean_only=False)
    received_clean = summarize_replays(received_records, clean_only=True)
    source_all = summarize_replays(source_records, clean_only=False)
    source_clean = summarize_replays(source_records, clean_only=True)

    report = {
        "version": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceContract": {
            "bookSource": BOOK_TABLE,
            "bookDb": book_db_path,
            "lifecycleDb": db_path,
            "legacySnapshotSourceUsed": False,
            "forbiddenLegacyBookDbBasenames": sorted(LEGACY_STALE_BOOK_DB_BASENAMES),
            "replayOrder": "maker_book_inference_updates.id ASC (collector insertion/arrival sequence)",
            "receivedStrictPolicy": "contiguous id prefix ending before first received_at_ms >= placement_first_ms",
            "sourceStrictPolicy": "conservative contiguous id prefix ending before first source_timestamp_ms >= placement_first_ms; later timestamp regressions are excluded",
            "note": "V2.5b never reads wallet_taker_signal_snapshots and never source-sorts changes_z rows.",
        },
        "window": {
            "specialStart": args.special_start,
            "specialEnd": args.special_end,
            "startInclusive": True,
            "endExclusive": True,
            "startMs": start_ms,
            "endMs": end_ms,
            "purpose": "pre-noon bounded discovery window; not a claim that every hour is special",
        },
        "parentFunnel": funnel_counts(parents),
        "parentFunnelByHour": build_hourly_funnel(parents, start_ms, end_ms),
        "reconstructionSummary": {
            "receivedStrict": {"ALL_PARENTS": received_all, "CLEAN_TARGET": received_clean},
            "sourceStrict": {"ALL_PARENTS": source_all, "CLEAN_TARGET": source_clean},
        },
        "reconstructionByHour": by_hour_reconstruction(parents, received_records, source_records),
        "raw8778CoverageByParentMarket": build_raw_market_coverage(parents, updates_by_market, start_ms, end_ms),
        "parentReplay": parent_rows,
        "gate": gate(received_clean),
        "notes": [
            "This audit performs actual zlib+JSON checkpoint decoding and changes_z replay; checkpoint presence alone is not counted as reconstruction.",
            "before/after fields are authoritative for level transitions; delta is checked only as a diagnostic because float subtraction can round.",
            "receivedStrict is the primary live-causal gate. sourceStrict is a conservative diagnostic and never reorders rows by source timestamp.",
            "The bounded end prevents later Aug-17 cohorts from washing out Aug-16 pre-noon coverage.",
            "No EBM fitting, refit, retune, Binance, Chainlink, or legacy wallet_taker_signal_snapshots data is used here.",
        ],
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "version": REPORT_VERSION,
        "out": str(out_path),
        "window": [args.special_start, args.special_end],
        "parents": len(parents),
        "cleanTarget": funnel_counts(parents)["CLEAN_TARGET"],
        "receivedStrictCleanRate": received_clean.get("reconstructableRate"),
        "sourceStrictCleanRate": source_clean.get("reconstructableRate"),
        "gate": report["gate"],
        "legacySnapshotSourceUsed": False,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
