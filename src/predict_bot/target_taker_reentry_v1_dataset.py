from __future__ import annotations

import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .predict_wallet_target_taker_public_side_strategy_v1 import SIDE_EBM_EXPECTED_FEATURES
from .target_taker_behavior_v1 import RAW_PUBLIC_FEATURES, SIDE_MODEL_FEATURES


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OFFICIAL_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_LEGACY_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_SIGNAL_DB = ROOT / "data" / "wallet_taker_signals.db"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_taker_reentry_v1.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_taker_reentry_v1.meta.json"

DATASET_VERSION = "TARGET_TAKER_REENTRY_V1_RISKSET_MULTI_HORIZON_STRICT_CAUSAL"
LEGACY_TAKER_COHORT = "TARGET_TAKER_MIRROR_AUDIT_V1"
HORIZONS_MS = (500, 1_000, 2_000, 5_000)
MAX_PRE_ENTRY_SIGNAL_AGE_MS = 2_000
DEFAULT_MIN_SNAPSHOT_STEP_MS = 250

# Only public pre-decision values and actor-owned state are eligible model inputs.
# Target future actions are labels/audit fields only.  Historical Target actions may
# populate actor_* state because the runtime analogue is the strategy's own prior
# action state, never the live Target wallet.
ACTOR_STATE_FEATURES = [
    "actor_entry_count_so_far",
    "actor_ms_since_prev_entry_min",
    "actor_ms_since_prev_entry_max",
    "actor_prev_entry_side_up",
    "actor_prev_entry_price",
    "actor_current_side_bid",
    "actor_current_side_ask",
    "actor_current_side_mid",
    "actor_current_side_spread",
    "actor_opposite_current_mid",
    "actor_current_side_ask_minus_prev_entry_price",
]
DELTA_BASE_FEATURES = [
    "predict_up_mid",
    "predict_down_mid",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "direction_score",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
]
DELTA_FEATURES = [f"delta_{name}_since_prev_pre_entry" for name in DELTA_BASE_FEATURES]
REENTRY_MODEL_FEATURES = list(SIDE_MODEL_FEATURES) + ACTOR_STATE_FEATURES + DELTA_FEATURES

METADATA_COLUMNS = [
    "dataset_version",
    "row_id",
    "market_id",
    "decision_sampled_at_ms",
    "snapshot_timestamp_ns",
    "actor_prev_parent_id",
    "actor_prev_parent_source",
    "actor_prev_entry_side",
    "actor_prev_entry_event_lower_ms",
    "actor_prev_entry_event_upper_ms",
    "actor_prev_entry_timestamp_precision",
    "actor_prev_pre_entry_sampled_at_ms",
    "frozen16_available_count",
    "frozen16_complete",
    "audit_next_target_parent_id",
    "audit_next_target_parent_source",
    "audit_next_target_side",
    "audit_next_target_event_lower_ms",
    "audit_next_target_event_upper_ms",
    "audit_next_target_timestamp_precision",
    "audit_next_entry_same_side",
    "audit_time_to_next_entry_lower_ms",
    "audit_time_to_next_entry_upper_ms",
]
LABEL_COLUMNS = [
    f"label_{kind}_within_{horizon}ms"
    for horizon in HORIZONS_MS
    for kind in ("any_reentry", "same_side_reentry", "opposite_side_entry")
]
OUTPUT_COLUMNS = METADATA_COLUMNS + REENTRY_MODEL_FEATURES + LABEL_COLUMNS


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _spread(bid: Any, ask: Any) -> float | None:
    b = _number(bid)
    a = _number(ask)
    return a - b if b is not None and a is not None else None


def _connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def _quantiles(values: Iterable[float]) -> dict[str, float | None]:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return {"p10": None, "p25": None, "median": None, "p75": None, "p90": None}

    def pick(q: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
        return float(ordered[index])

    return {
        "p10": pick(0.10),
        "p25": pick(0.25),
        "median": float(statistics.median(ordered)),
        "p75": pick(0.75),
        "p90": pick(0.90),
    }


def _event_interval(event_ms: int) -> tuple[int, int, str]:
    # Historical Target Taker timestamps were often whole-second values.  A whole
    # second is therefore treated as an uncertainty bucket instead of pretending
    # the action happened exactly at xx:xx:xx.000.  Fractional-ms timestamps keep
    # their exact causal ordering.
    value = int(event_ms)
    if value % 1_000 == 0:
        return value, value + 999, "SECOND_BUCKET"
    return value, value, "MILLISECOND"


def _parent_key(parent: dict[str, Any]) -> tuple[Any, ...]:
    order_hash = str(parent.get("order_hash") or "").strip().lower()
    if order_hash:
        return ("ORDER_HASH", int(parent["market_id"]), order_hash)
    # Never fuzzy-dedupe no-hash parents: two same-second same-price Target orders
    # can be genuinely distinct entries.
    return ("SOURCE_PARENT", str(parent["source"]), str(parent["parent_id"]))


def _normalize_parent(
    *,
    source: str,
    parent_id: Any,
    market_id: Any,
    side: Any,
    event_ms: Any,
    last_event_ms: Any,
    order_hash: Any,
    average_price: Any,
    shares: Any,
    fill_legs: Any,
) -> dict[str, Any] | None:
    market = _int(market_id)
    event = _int(event_ms)
    normalized_side = str(side or "").strip().upper()
    if market is None or market <= 0 or event is None or event <= 0 or normalized_side not in {"UP", "DOWN"}:
        return None
    lower, upper, precision = _event_interval(event)
    return {
        "source": source,
        "parent_id": str(parent_id),
        "market_id": int(market),
        "side": normalized_side,
        "event_ms": int(event),
        "event_lower_ms": int(lower),
        "event_upper_ms": int(upper),
        "timestamp_precision": precision,
        "last_event_ms": _int(last_event_ms) or int(event),
        "order_hash": str(order_hash or "").strip() or None,
        "average_price": _number(average_price),
        "shares": _number(shares),
        "fill_legs": _int(fill_legs),
    }


def _load_legacy_parents(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return [], {"available": False, "rows": 0, "reason": "FILE_MISSING"}
    db = _connect_readonly(resolved)
    try:
        if not _has_table(db, "wallet_target_taker_mirror_parents"):
            return [], {"available": False, "rows": 0, "reason": "TABLE_MISSING"}
        rows = db.execute(
            """SELECT parent_id,market_id,order_hash,side,target_event_ms,target_last_event_ms,
                      target_average_price,target_latest_shares,target_fill_legs
                 FROM wallet_target_taker_mirror_parents
                WHERE cohort=?
                ORDER BY market_id,target_event_ms,parent_id""",
            (LEGACY_TAKER_COHORT,),
        )
        parents: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            parent = _normalize_parent(
                source="LEGACY_TAKER_MIRROR",
                parent_id=row.get("parent_id"),
                market_id=row.get("market_id"),
                side=row.get("side"),
                event_ms=row.get("target_event_ms"),
                last_event_ms=row.get("target_last_event_ms"),
                order_hash=row.get("order_hash"),
                average_price=row.get("target_average_price"),
                shares=row.get("target_latest_shares"),
                fill_legs=row.get("target_fill_legs"),
            )
            if parent is not None:
                parents.append(parent)
        return parents, {
            "available": True,
            "rows": len(parents),
            "role": "historical read-only Taker mirror; BID-entry semantics inherited from the original mirror cohort",
        }
    finally:
        db.close()


def _load_official_parents(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return [], {"available": False, "rows": 0, "reason": "FILE_MISSING"}
    db = _connect_readonly(resolved)
    try:
        if not _has_table(db, "target_parent_orders"):
            return [], {"available": False, "rows": 0, "reason": "TABLE_MISSING"}
        raw_rows = [
            dict(row)
            for row in db.execute(
                """SELECT parent_id,market_id,asset,role,side,quote_type,order_hash,
                          first_event_ms,last_event_ms,average_price,shares,fill_legs
                     FROM target_parent_orders
                    WHERE asset='BTC' AND role='TAKER'
                    ORDER BY market_id,first_event_ms,parent_id"""
            )
        ]
        bid_rows = [row for row in raw_rows if str(row.get("quote_type") or "").upper() == "BID"]
        excluded_ask = sum(str(row.get("quote_type") or "").upper() == "ASK" for row in raw_rows)
        excluded_unknown = len(raw_rows) - len(bid_rows) - excluded_ask
        parents: list[dict[str, Any]] = []
        for row in bid_rows:
            parent = _normalize_parent(
                source="TARGET_WALLET_OFFICIAL",
                parent_id=row.get("parent_id"),
                market_id=row.get("market_id"),
                side=row.get("side"),
                event_ms=row.get("first_event_ms"),
                last_event_ms=row.get("last_event_ms"),
                order_hash=row.get("order_hash"),
                average_price=row.get("average_price"),
                shares=row.get("shares"),
                fill_legs=row.get("fill_legs"),
            )
            if parent is not None:
                parents.append(parent)
        return parents, {
            "available": True,
            "rows": len(parents),
            "allTakerParents": len(raw_rows),
            "excludedAskParents": int(excluded_ask),
            "excludedUnknownQuoteTypeParents": int(excluded_unknown),
            "role": "current official read-only Taker BID parent orders",
        }
    finally:
        db.close()


def _merge_parents(
    legacy: list[dict[str, Any]], official: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for parent in legacy:
        merged[_parent_key(parent)] = parent
    overridden = 0
    for parent in official:
        key = _parent_key(parent)
        if key in merged:
            overridden += 1
        merged[key] = parent
    result = sorted(
        merged.values(),
        key=lambda row: (int(row["market_id"]), int(row["event_lower_ms"]), str(row["parent_id"])),
    )
    return result, overridden


def _load_market_snapshots(
    signal: sqlite3.Connection,
    market_id: int,
    *,
    min_snapshot_step_ms: int,
) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in signal.execute(
            """SELECT * FROM wallet_taker_signal_snapshots
                WHERE market_id=? ORDER BY sampled_at_ms,timestamp_ns""",
            (int(market_id),),
        )
    ]
    if min_snapshot_step_ms <= 0:
        return rows
    result: list[dict[str, Any]] = []
    last_kept: int | None = None
    for row in rows:
        sampled = _int(row.get("sampled_at_ms"))
        if sampled is None:
            timestamp_ns = _int(row.get("timestamp_ns"))
            sampled = timestamp_ns // 1_000_000 if timestamp_ns is not None else None
        if sampled is None:
            continue
        row["sampled_at_ms"] = int(sampled)
        if last_kept is not None and sampled - last_kept < min_snapshot_step_ms:
            continue
        result.append(row)
        last_kept = int(sampled)
    return result


def _public_features(snapshot: dict[str, Any], *, decision_ms: int) -> dict[str, Any]:
    result = {column: snapshot.get(column) for column in RAW_PUBLIC_FEATURES}
    up_bid = _number(snapshot.get("predict_up_bid"))
    up_ask = _number(snapshot.get("predict_up_ask"))
    down_bid = _number(snapshot.get("predict_down_bid"))
    down_ask = _number(snapshot.get("predict_down_ask"))
    up_mid = _number(snapshot.get("predict_up_mid"))
    down_mid = _number(snapshot.get("predict_down_mid"))
    direction = _number(snapshot.get("direction_score"))
    sampled = _int(snapshot.get("sampled_at_ms"))
    result.update(
        {
            "predict_up_spread": up_ask - up_bid if up_ask is not None and up_bid is not None else None,
            "predict_down_spread": down_ask - down_bid if down_ask is not None and down_bid is not None else None,
            "predict_mid_sum": up_mid + down_mid if up_mid is not None and down_mid is not None else None,
            "predict_up_mid_edge": up_mid - 0.5 if up_mid is not None else None,
            "abs_direction_score": abs(direction) if direction is not None else None,
            "signal_age_ms": max(0, int(decision_ms) - int(sampled)) if sampled is not None else None,
        }
    )
    return result


def _strict_pre_entry_snapshot(
    snapshots: list[dict[str, Any]], parent: dict[str, Any]
) -> dict[str, Any] | None:
    cutoff = int(parent["event_lower_ms"])
    floor = cutoff - MAX_PRE_ENTRY_SIGNAL_AGE_MS
    candidate: dict[str, Any] | None = None
    for row in snapshots:
        sampled = int(row["sampled_at_ms"])
        if sampled >= cutoff:
            break
        if sampled >= floor:
            candidate = row
    return candidate


def _actor_side_context(snapshot: dict[str, Any], side: str) -> dict[str, Any]:
    chosen = "up" if side == "UP" else "down"
    opposite = "down" if side == "UP" else "up"
    bid = _number(snapshot.get(f"predict_{chosen}_bid"))
    ask = _number(snapshot.get(f"predict_{chosen}_ask"))
    return {
        "bid": bid,
        "ask": ask,
        "mid": _number(snapshot.get(f"predict_{chosen}_mid")),
        "spread": ask - bid if ask is not None and bid is not None else None,
        "opposite_mid": _number(snapshot.get(f"predict_{opposite}_mid")),
    }


def _bounded_label(
    next_parent: dict[str, Any] | None,
    *,
    sampled_at_ms: int,
    horizon_ms: int,
    required_relation: str | None,
    previous_side: str,
) -> int | None:
    if next_parent is None:
        return 0
    relation = "SAME" if str(next_parent["side"]) == str(previous_side) else "OPPOSITE"
    if required_relation is not None and relation != required_relation:
        return 0
    lower = int(next_parent["event_lower_ms"]) - int(sampled_at_ms)
    upper = int(next_parent["event_upper_ms"]) - int(sampled_at_ms)
    if upper <= int(horizon_ms):
        return 1
    if lower > int(horizon_ms):
        return 0
    # The event's reported whole-second timestamp straddles this horizon.
    return None


def _label_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in LABEL_COLUMNS:
        values = [row.get(column) for row in rows]
        known = [int(value) for value in values if value in {0, 1}]
        positives = sum(known)
        output[column] = {
            "known": len(known),
            "positive": positives,
            "negative": len(known) - positives,
            "ambiguous": len(values) - len(known),
            "positiveRate": positives / len(known) if known else None,
        }
    return output


def build_target_taker_reentry_v1_dataset(
    *,
    official_db_path: Path = DEFAULT_OFFICIAL_DB,
    legacy_db_path: Path = DEFAULT_LEGACY_DB,
    signal_db_path: Path = DEFAULT_SIGNAL_DB,
    output_path: Path = DEFAULT_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
    min_snapshot_step_ms: int = DEFAULT_MIN_SNAPSHOT_STEP_MS,
) -> dict[str, Any]:
    legacy, legacy_meta = _load_legacy_parents(legacy_db_path)
    official, official_meta = _load_official_parents(official_db_path)
    parents, overridden = _merge_parents(legacy, official)

    signal = _connect_readonly(signal_db_path)
    try:
        if not _has_table(signal, "wallet_taker_signal_snapshots"):
            raise RuntimeError("required public snapshot table missing: wallet_taker_signal_snapshots")

        by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for parent in parents:
            by_market[int(parent["market_id"])].append(parent)

        output_rows: list[dict[str, Any]] = []
        markets_without_snapshots = 0
        ambiguous_event_overlap_snapshots = 0
        skipped_before_first_known_entry = 0
        missing_prev_pre_entry_snapshot = 0
        rows_with_complete_frozen16 = 0
        row_counter = 0

        for market_id, entries in sorted(by_market.items()):
            entries.sort(key=lambda row: (int(row["event_lower_ms"]), str(row["parent_id"])))
            snapshots = _load_market_snapshots(
                signal,
                market_id,
                min_snapshot_step_ms=max(0, int(min_snapshot_step_ms)),
            )
            if not snapshots:
                markets_without_snapshots += 1
                continue
            pre_entry_cache = {
                str(parent["parent_id"]): _strict_pre_entry_snapshot(snapshots, parent)
                for parent in entries
            }

            for snapshot in snapshots:
                sampled = int(snapshot["sampled_at_ms"])
                # If the observation falls inside a whole-second Target event
                # uncertainty interval, action ordering is unknowable.  Skip it.
                if any(
                    int(parent["event_lower_ms"]) <= sampled <= int(parent["event_upper_ms"])
                    for parent in entries
                ):
                    ambiguous_event_overlap_snapshots += 1
                    continue

                past = [parent for parent in entries if int(parent["event_upper_ms"]) < sampled]
                if not past:
                    skipped_before_first_known_entry += 1
                    continue
                previous = past[-1]
                next_parent = next(
                    (parent for parent in entries if int(parent["event_lower_ms"]) > sampled),
                    None,
                )
                previous_pre_snapshot = pre_entry_cache.get(str(previous["parent_id"]))
                if previous_pre_snapshot is None:
                    missing_prev_pre_entry_snapshot += 1

                public = _public_features(snapshot, decision_ms=sampled)
                previous_public = (
                    _public_features(
                        previous_pre_snapshot,
                        decision_ms=int(previous["event_lower_ms"]),
                    )
                    if previous_pre_snapshot is not None
                    else {}
                )
                actor_side = _actor_side_context(snapshot, str(previous["side"]))
                previous_price = _number(previous.get("average_price"))
                available16 = sum(public.get(name) is not None for name in SIDE_EBM_EXPECTED_FEATURES)
                frozen_complete = available16 == len(SIDE_EBM_EXPECTED_FEATURES)
                rows_with_complete_frozen16 += int(frozen_complete)

                row_counter += 1
                row: dict[str, Any] = {
                    "dataset_version": DATASET_VERSION,
                    "row_id": row_counter,
                    "market_id": market_id,
                    "decision_sampled_at_ms": sampled,
                    "snapshot_timestamp_ns": _int(snapshot.get("timestamp_ns")),
                    "actor_prev_parent_id": previous["parent_id"],
                    "actor_prev_parent_source": previous["source"],
                    "actor_prev_entry_side": previous["side"],
                    "actor_prev_entry_event_lower_ms": previous["event_lower_ms"],
                    "actor_prev_entry_event_upper_ms": previous["event_upper_ms"],
                    "actor_prev_entry_timestamp_precision": previous["timestamp_precision"],
                    "actor_prev_pre_entry_sampled_at_ms": (
                        _int(previous_pre_snapshot.get("sampled_at_ms"))
                        if previous_pre_snapshot is not None
                        else None
                    ),
                    "frozen16_available_count": available16,
                    "frozen16_complete": int(frozen_complete),
                    "audit_next_target_parent_id": next_parent.get("parent_id") if next_parent else None,
                    "audit_next_target_parent_source": next_parent.get("source") if next_parent else None,
                    "audit_next_target_side": next_parent.get("side") if next_parent else None,
                    "audit_next_target_event_lower_ms": next_parent.get("event_lower_ms") if next_parent else None,
                    "audit_next_target_event_upper_ms": next_parent.get("event_upper_ms") if next_parent else None,
                    "audit_next_target_timestamp_precision": next_parent.get("timestamp_precision") if next_parent else None,
                    "audit_next_entry_same_side": (
                        int(str(next_parent["side"]) == str(previous["side"])) if next_parent else None
                    ),
                    "audit_time_to_next_entry_lower_ms": (
                        int(next_parent["event_lower_ms"]) - sampled if next_parent else None
                    ),
                    "audit_time_to_next_entry_upper_ms": (
                        int(next_parent["event_upper_ms"]) - sampled if next_parent else None
                    ),
                    "actor_entry_count_so_far": len(past),
                    "actor_ms_since_prev_entry_min": sampled - int(previous["event_upper_ms"]),
                    "actor_ms_since_prev_entry_max": sampled - int(previous["event_lower_ms"]),
                    "actor_prev_entry_side_up": int(str(previous["side"]) == "UP"),
                    "actor_prev_entry_price": previous_price,
                    "actor_current_side_bid": actor_side["bid"],
                    "actor_current_side_ask": actor_side["ask"],
                    "actor_current_side_mid": actor_side["mid"],
                    "actor_current_side_spread": actor_side["spread"],
                    "actor_opposite_current_mid": actor_side["opposite_mid"],
                    "actor_current_side_ask_minus_prev_entry_price": (
                        actor_side["ask"] - previous_price
                        if actor_side["ask"] is not None and previous_price is not None
                        else None
                    ),
                }
                row.update(public)
                for feature in DELTA_BASE_FEATURES:
                    current = _number(public.get(feature))
                    prior = _number(previous_public.get(feature))
                    row[f"delta_{feature}_since_prev_pre_entry"] = (
                        current - prior if current is not None and prior is not None else None
                    )
                for horizon in HORIZONS_MS:
                    row[f"label_any_reentry_within_{horizon}ms"] = _bounded_label(
                        next_parent,
                        sampled_at_ms=sampled,
                        horizon_ms=horizon,
                        required_relation=None,
                        previous_side=str(previous["side"]),
                    )
                    row[f"label_same_side_reentry_within_{horizon}ms"] = _bounded_label(
                        next_parent,
                        sampled_at_ms=sampled,
                        horizon_ms=horizon,
                        required_relation="SAME",
                        previous_side=str(previous["side"]),
                    )
                    row[f"label_opposite_side_entry_within_{horizon}ms"] = _bounded_label(
                        next_parent,
                        sampled_at_ms=sampled,
                        horizon_ms=horizon,
                        required_relation="OPPOSITE",
                        previous_side=str(previous["side"]),
                    )
                output_rows.append(row)

        output_path = output_path.expanduser().resolve()
        meta_output_path = meta_output_path.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output_path.with_suffix(output_path.suffix + ".tmp")
        with temp_output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in output_rows:
                writer.writerow({column: row.get(column) for column in OUTPUT_COLUMNS})
        temp_output.replace(output_path)

        entry_counts = [len(entries) for entries in by_market.values()]
        reported_gaps: list[float] = []
        conservative_gap_min: list[float] = []
        same_side_pairs = 0
        opposite_side_pairs = 0
        same_second_pairs = 0
        total_reentry_pairs = 0
        for entries in by_market.values():
            entries = sorted(entries, key=lambda row: (int(row["event_lower_ms"]), str(row["parent_id"])))
            for previous, current in zip(entries, entries[1:]):
                total_reentry_pairs += 1
                same_side_pairs += int(str(previous["side"]) == str(current["side"]))
                opposite_side_pairs += int(str(previous["side"]) != str(current["side"]))
                same_second_pairs += int(
                    int(previous["event_lower_ms"]) // 1_000 == int(current["event_lower_ms"]) // 1_000
                )
                reported_gaps.append(float(int(current["event_ms"]) - int(previous["event_ms"])))
                conservative_gap_min.append(
                    float(max(0, int(current["event_lower_ms"]) - int(previous["event_upper_ms"])))
                )

        missing_rates: dict[str, float | None] = {}
        for feature in REENTRY_MODEL_FEATURES:
            missing = sum(row.get(feature) is None for row in output_rows)
            missing_rates[feature] = missing / len(output_rows) if output_rows else None

        report = {
            "datasetVersion": DATASET_VERSION,
            "researchOnly": True,
            "automaticStrategyPromotion": False,
            "modelTrained": False,
            "rows": len(output_rows),
            "markets": len({int(row["market_id"]) for row in output_rows}),
            "targetMarketsWithAnyRetainedEntry": len(by_market),
            "targetBidParentsAfterMerge": len(parents),
            "sourceParents": {
                "legacy": legacy_meta,
                "official": official_meta,
                "officialOverridesLegacyByOrderHash": overridden,
            },
            "sequence": {
                "marketEntryCount": _quantiles(float(value) for value in entry_counts),
                "reentryPairs": total_reentry_pairs,
                "sameSidePairs": same_side_pairs,
                "oppositeSidePairs": opposite_side_pairs,
                "sameSecondPairs": same_second_pairs,
                "sameSidePairRate": same_side_pairs / total_reentry_pairs if total_reentry_pairs else None,
                "reportedTimestampGapMs": _quantiles(reported_gaps),
                "conservativeKnownGapMinMs": _quantiles(conservative_gap_min),
            },
            "riskSet": {
                "minSnapshotStepMs": max(0, int(min_snapshot_step_ms)),
                "marketsWithoutPublicSnapshots": markets_without_snapshots,
                "skippedSnapshotsInsideTargetTimestampUncertainty": ambiguous_event_overlap_snapshots,
                "skippedSnapshotsBeforeFirstKnownEntry": skipped_before_first_known_entry,
                "rowsMissingPreviousPreEntrySnapshot": missing_prev_pre_entry_snapshot,
                "rowsFrozen16Complete": rows_with_complete_frozen16,
                "frozen16CompleteRate": (
                    rows_with_complete_frozen16 / len(output_rows) if output_rows else None
                ),
            },
            "labels": _label_stats(output_rows),
            "modelFeatureMissingRate": missing_rates,
            "horizonsMs": list(HORIZONS_MS),
            "reentryModelFeatures": REENTRY_MODEL_FEATURES,
            "frozenSideEbmFeatures": list(SIDE_EBM_EXPECTED_FEATURES),
            "timestampBoundary": (
                "Whole-second Target timestamps are treated as [event_ms,event_ms+999] uncertainty buckets. "
                "Snapshots inside any Target event bucket are excluded. A horizon label is NULL when the event "
                "uncertainty interval straddles that horizon; no timing guess is made."
            ),
            "labelBoundary": (
                "audit_next_target_* and label_* are future Target facts and must never enter model features. "
                "ASK Taker parents from the clean Official DB are excluded because REENTRY_V1 studies entries, not exits."
            ),
            "actorStateBoundary": (
                "Historical Target actions are used only to instantiate actor_* prior-action state during training. "
                "At runtime every actor_* value must come from this strategy's own prior entries; live Target fills, "
                "inventory and parent orders are forbidden runtime inputs."
            ),
            "publicFeatureBoundary": (
                "SIDE_MODEL_FEATURES and delta_* values are derived from public pre-decision snapshots only. "
                "The previous-entry delta anchor uses the latest same-market public snapshot strictly before the "
                "previous Target entry's lower timestamp bound."
            ),
            "retentionCaveat": (
                "The dataset can only reconstruct Target actions and public snapshots still physically retained in "
                "the supplied SQLite sources. Missing historical rows are not fabricated."
            ),
            "output": str(output_path),
            "metaOutput": str(meta_output_path),
            "sources": {
                "officialDb": str(official_db_path),
                "legacyDb": str(legacy_db_path),
                "signalDb": str(signal_db_path),
                "readOnly": True,
            },
        }
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_meta = meta_output_path.with_suffix(meta_output_path.suffix + ".tmp")
        temp_meta.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_meta.replace(meta_output_path)
        return report
    finally:
        signal.close()
