from __future__ import annotations

import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAKER_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_SIGNAL_DB = ROOT / "data" / "wallet_taker_signals.db"
DEFAULT_HAZARD_OUTPUT = ROOT / "data" / "research" / "target_maker_direct_hazard_v1.csv"
DEFAULT_BEHAVIOR_OUTPUT = ROOT / "data" / "research" / "target_maker_direct_behavior_v1.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_maker_direct_placement_v1.meta.json"
DATASET_VERSION = "TARGET_MAKER_DIRECT_PLACEMENT_V1_STRICT_PRE_INFERRED_PLACEMENT"
MAX_SIGNAL_AGE_MS = 2_000
MIN_PARENT_CONFIDENCE = 0.70
MIN_PLACEMENT_COVERAGE = 0.80
MIN_FILL_COVERAGE = 0.80

PUBLIC_FEATURES = [
    "seconds_left",
    "predict_up_bid", "predict_up_ask", "predict_up_mid",
    "predict_down_bid", "predict_down_ask", "predict_down_mid",
    "predict_up_spread", "predict_down_spread", "predict_mid_sum", "predict_up_mid_edge",
    "spot_queue_imbalance", "spot_taker_imbalance_250ms", "spot_taker_imbalance_1s",
    "spot_return_250ms_bps", "spot_return_1s_bps", "spot_return_3s_bps", "spot_return_5s_bps",
    "futures_queue_imbalance", "futures_taker_imbalance_250ms", "futures_taker_imbalance_1s",
    "futures_return_250ms_bps", "futures_return_1s_bps", "futures_return_3s_bps", "futures_return_5s_bps",
    "perp_spot_basis_bps", "spot_minus_strike_bps", "chainlink_minus_strike_bps",
    "spot_minus_chainlink_bps", "direction_score", "abs_direction_score",
]

CHOSEN_SIDE_CONTEXT = [
    "chosen_predict_bid", "chosen_predict_ask", "chosen_predict_mid",
    "chosen_predict_spread", "opposite_predict_mid",
]
LEVEL_MODEL_FEATURES = PUBLIC_FEATURES + ["label_side_up"] + CHOSEN_SIDE_CONTEXT

HAZARD_METADATA = [
    "dataset_version", "market_id", "decision_sampled_at_ms", "decision_bucket_start_ms",
    "next_inferred_placement_ms", "signal_age_ms",
]
HAZARD_LABELS = [
    "label_next_inferred_placement_any_1s",
    "label_next_inferred_placement_any_2s",
    "label_next_inferred_placement_any_5s",
]
HAZARD_OUTPUT_COLUMNS = HAZARD_METADATA + PUBLIC_FEATURES + HAZARD_LABELS

BEHAVIOR_METADATA = [
    "dataset_version", "parent_id", "market_id", "order_hash", "target_side",
    "native_book_side", "placement_first_ms", "placement_last_ms", "first_fill_ms",
    "target_price", "native_price", "expected_parent_shares", "placement_coverage",
    "fill_allocation_coverage", "parent_confidence", "signal_sampled_at_ms", "signal_age_ms",
]
BEHAVIOR_OUTCOMES = [
    "label_side_up", "placement_ticks_from_pre_best_bid",
    "label_near_best_1tick", "label_near_best_2ticks", "label_at_or_improves_best_bid",
    "label_matches_public_direction",
]
BEHAVIOR_OUTPUT_COLUMNS = BEHAVIOR_METADATA + PUBLIC_FEATURES + CHOSEN_SIDE_CONTEXT + BEHAVIOR_OUTCOMES


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


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


def _spread(bid: Any, ask: Any) -> float | None:
    b, a = _number(bid), _number(ask)
    return a - b if b is not None and a is not None else None


def _public_features(snapshot: dict[str, Any]) -> dict[str, Any]:
    up_mid = _number(snapshot.get("predict_up_mid"))
    down_mid = _number(snapshot.get("predict_down_mid"))
    direction = _number(snapshot.get("direction_score"))
    return {
        "seconds_left": snapshot.get("seconds_left"),
        "predict_up_bid": snapshot.get("predict_up_bid"),
        "predict_up_ask": snapshot.get("predict_up_ask"),
        "predict_up_mid": snapshot.get("predict_up_mid"),
        "predict_down_bid": snapshot.get("predict_down_bid"),
        "predict_down_ask": snapshot.get("predict_down_ask"),
        "predict_down_mid": snapshot.get("predict_down_mid"),
        "predict_up_spread": _spread(snapshot.get("predict_up_bid"), snapshot.get("predict_up_ask")),
        "predict_down_spread": _spread(snapshot.get("predict_down_bid"), snapshot.get("predict_down_ask")),
        "predict_mid_sum": up_mid + down_mid if up_mid is not None and down_mid is not None else None,
        "predict_up_mid_edge": up_mid - 0.5 if up_mid is not None else None,
        "spot_queue_imbalance": snapshot.get("spot_queue_imbalance"),
        "spot_taker_imbalance_250ms": snapshot.get("spot_taker_imbalance_250ms"),
        "spot_taker_imbalance_1s": snapshot.get("spot_taker_imbalance_1s"),
        "spot_return_250ms_bps": snapshot.get("spot_return_250ms_bps"),
        "spot_return_1s_bps": snapshot.get("spot_return_1s_bps"),
        "spot_return_3s_bps": snapshot.get("spot_return_3s_bps"),
        "spot_return_5s_bps": snapshot.get("spot_return_5s_bps"),
        "futures_queue_imbalance": snapshot.get("futures_queue_imbalance"),
        "futures_taker_imbalance_250ms": snapshot.get("futures_taker_imbalance_250ms"),
        "futures_taker_imbalance_1s": snapshot.get("futures_taker_imbalance_1s"),
        "futures_return_250ms_bps": snapshot.get("futures_return_250ms_bps"),
        "futures_return_1s_bps": snapshot.get("futures_return_1s_bps"),
        "futures_return_3s_bps": snapshot.get("futures_return_3s_bps"),
        "futures_return_5s_bps": snapshot.get("futures_return_5s_bps"),
        "perp_spot_basis_bps": snapshot.get("perp_spot_basis_bps"),
        "spot_minus_strike_bps": snapshot.get("spot_minus_strike_bps"),
        "chainlink_minus_strike_bps": snapshot.get("chainlink_minus_strike_bps"),
        "spot_minus_chainlink_bps": snapshot.get("spot_minus_chainlink_bps"),
        "direction_score": snapshot.get("direction_score"),
        "abs_direction_score": abs(direction) if direction is not None else None,
    }


def _chosen_side_context(snapshot: dict[str, Any], side: str) -> dict[str, Any]:
    chosen = "up" if side == "UP" else "down"
    opposite = "down" if side == "UP" else "up"
    bid = snapshot.get(f"predict_{chosen}_bid")
    ask = snapshot.get(f"predict_{chosen}_ask")
    return {
        "chosen_predict_bid": bid,
        "chosen_predict_ask": ask,
        "chosen_predict_mid": snapshot.get(f"predict_{chosen}_mid"),
        "chosen_predict_spread": _spread(bid, ask),
        "opposite_predict_mid": snapshot.get(f"predict_{opposite}_mid"),
    }


def _signal_deployed_at_ms(db: sqlite3.Connection) -> int:
    if not _has_table(db, "wallet_taker_signal_meta"):
        return 0
    row = db.execute("SELECT value FROM wallet_taker_signal_meta WHERE key='deployed_at_ms'").fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _maker_coverage(db: sqlite3.Connection) -> tuple[int, int | None]:
    row = db.execute(
        """SELECT deployed_at_ms,excluded_market_id FROM maker_book_inference_meta
            WHERE cohort='TARGET_MAKER_BOOK_INFERENCE_V1' LIMIT 1"""
    ).fetchone()
    if row is None:
        raise RuntimeError("missing BTC maker_book_inference_meta")
    return int(row["deployed_at_ms"]), int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None


def _eligible_parents(
    db: sqlite3.Connection,
    *,
    coverage_start_ms: int,
    excluded_market_id: int | None,
    min_parent_confidence: float,
    min_placement_coverage: float,
    min_fill_coverage: float,
) -> list[dict[str, Any]]:
    rows = []
    for raw in db.execute(
        """SELECT parent_id,market_id,order_hash,target_side,native_book_side,target_price,native_price,
                  first_target_ms,last_target_ms,placement_first_ms,placement_last_ms,resting_ms,
                  expected_parent_shares,placement_coverage,fill_allocation_coverage,confidence
             FROM maker_book_inference_v21_parent_lifecycles
            WHERE placement_first_ms IS NOT NULL AND placement_first_ms>=?
            ORDER BY placement_first_ms,parent_id""",
        (int(coverage_start_ms),),
    ):
        row = dict(raw)
        if excluded_market_id is not None and int(row["market_id"]) == excluded_market_id:
            continue
        if float(row.get("confidence") or 0.0) < min_parent_confidence:
            continue
        if float(row.get("placement_coverage") or 0.0) < min_placement_coverage:
            continue
        if float(row.get("fill_allocation_coverage") or 0.0) < min_fill_coverage:
            continue
        rows.append(row)
    return rows


def _strict_pre_snapshot(db: sqlite3.Connection, *, market_id: int, placement_ms: int, max_age_ms: int) -> dict[str, Any] | None:
    row = db.execute(
        """SELECT * FROM wallet_taker_signal_snapshots
             WHERE market_id=? AND sampled_at_ms>=? AND sampled_at_ms<?
             ORDER BY sampled_at_ms DESC,timestamp_ns DESC LIMIT 1""",
        (int(market_id), int(placement_ms - max_age_ms), int(placement_ms)),
    ).fetchone()
    return dict(row) if row is not None else None


def _latest_snapshot_per_second(db: sqlite3.Connection, market_id: int) -> list[dict[str, Any]]:
    rows = [dict(row) for row in db.execute(
        """SELECT * FROM wallet_taker_signal_snapshots WHERE market_id=? ORDER BY sampled_at_ms,timestamp_ns""",
        (int(market_id),),
    )]
    latest: dict[int, dict[str, Any]] = {}
    for row in rows:
        bucket = int(row["sampled_at_ms"]) // 1000
        latest[bucket] = row
    return [latest[key] for key in sorted(latest)]


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})
    temp.replace(resolved)


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "p25": None, "median": None, "p75": None, "p90": None}
    ordered = sorted(float(value) for value in values)
    def pick(q: float) -> float:
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
        return ordered[index]
    return {"p10": pick(.10), "p25": pick(.25), "median": float(statistics.median(ordered)), "p75": pick(.75), "p90": pick(.90)}


def build_datasets(
    *,
    maker_db_path: Path = DEFAULT_MAKER_DB,
    signal_db_path: Path = DEFAULT_SIGNAL_DB,
    hazard_output_path: Path = DEFAULT_HAZARD_OUTPUT,
    behavior_output_path: Path = DEFAULT_BEHAVIOR_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
    max_signal_age_ms: int = MAX_SIGNAL_AGE_MS,
    min_parent_confidence: float = MIN_PARENT_CONFIDENCE,
    min_placement_coverage: float = MIN_PLACEMENT_COVERAGE,
    min_fill_coverage: float = MIN_FILL_COVERAGE,
) -> dict[str, Any]:
    maker = _connect_readonly(maker_db_path)
    signal = _connect_readonly(signal_db_path)
    try:
        for table in ("maker_book_inference_meta", "maker_book_inference_markets", "maker_book_inference_v21_parent_lifecycles"):
            if not _has_table(maker, table):
                raise RuntimeError(f"required Maker table missing: {table}")
        if not _has_table(signal, "wallet_taker_signal_snapshots"):
            raise RuntimeError("8777 wallet_taker_signal_snapshots table is missing")

        maker_deployed, excluded_market_id = _maker_coverage(maker)
        signal_deployed = _signal_deployed_at_ms(signal)
        coverage_start_ms = max(maker_deployed, signal_deployed)
        parents = _eligible_parents(
            maker,
            coverage_start_ms=coverage_start_ms,
            excluded_market_id=excluded_market_id,
            min_parent_confidence=min_parent_confidence,
            min_placement_coverage=min_placement_coverage,
            min_fill_coverage=min_fill_coverage,
        )
        placements_by_market: dict[int, list[int]] = defaultdict(list)
        for parent in parents:
            placements_by_market[int(parent["market_id"])].append(int(parent["placement_first_ms"]))
        for values in placements_by_market.values():
            values.sort()

        maker_markets = {
            int(row[0]) for row in maker.execute(
                "SELECT market_id FROM maker_book_inference_markets WHERE first_seen_ms>=?",
                (int(coverage_start_ms),),
            )
            if excluded_market_id is None or int(row[0]) != excluded_market_id
        }
        signal_markets = {
            int(row[0]) for row in signal.execute(
                "SELECT DISTINCT market_id FROM wallet_taker_signal_snapshots WHERE sampled_at_ms>=? AND market_id IS NOT NULL",
                (int(coverage_start_ms),),
            )
        }
        covered_markets = sorted(maker_markets & signal_markets)

        hazard_rows: list[dict[str, Any]] = []
        for market_id in covered_markets:
            placements = placements_by_market.get(market_id, [])
            for snapshot in _latest_snapshot_per_second(signal, market_id):
                sampled = int(snapshot["sampled_at_ms"])
                if sampled < coverage_start_ms:
                    continue
                index = bisect.bisect_right(placements, sampled)
                next_ms = placements[index] if index < len(placements) else None
                row = {
                    "dataset_version": DATASET_VERSION,
                    "market_id": market_id,
                    "decision_sampled_at_ms": sampled,
                    "decision_bucket_start_ms": (sampled // 1000) * 1000,
                    "next_inferred_placement_ms": next_ms,
                    "signal_age_ms": 0,
                }
                row.update(_public_features(snapshot))
                for horizon in (1, 2, 5):
                    row[f"label_next_inferred_placement_any_{horizon}s"] = int(
                        next_ms is not None and sampled < next_ms <= sampled + horizon * 1000
                    )
                hazard_rows.append(row)

        behavior_rows: list[dict[str, Any]] = []
        missing_pre_snapshot = 0
        invalid_level = 0
        for parent in parents:
            placement_ms = int(parent["placement_first_ms"])
            market_id = int(parent["market_id"])
            snapshot = _strict_pre_snapshot(
                signal, market_id=market_id, placement_ms=placement_ms,
                max_age_ms=max(250, int(max_signal_age_ms)),
            )
            if snapshot is None:
                missing_pre_snapshot += 1
                continue
            side = str(parent["target_side"]).upper()
            if side not in {"UP", "DOWN"}:
                continue
            sampled = int(snapshot["sampled_at_ms"])
            context = _chosen_side_context(snapshot, side)
            best_bid = _number(context.get("chosen_predict_bid"))
            target_price = _number(parent.get("target_price"))
            ticks = (best_bid - target_price) / 0.01 if best_bid is not None and target_price is not None else None
            if ticks is not None and not -20 <= ticks <= 100:
                invalid_level += 1
                ticks = None
            direction = _number(snapshot.get("direction_score"))
            public_direction = None if direction is None or abs(direction) < 0.05 else ("UP" if direction > 0 else "DOWN")
            row = {
                "dataset_version": DATASET_VERSION,
                "parent_id": parent["parent_id"],
                "market_id": market_id,
                "order_hash": parent.get("order_hash"),
                "target_side": side,
                "native_book_side": parent.get("native_book_side"),
                "placement_first_ms": placement_ms,
                "placement_last_ms": parent.get("placement_last_ms"),
                "first_fill_ms": parent.get("first_target_ms"),
                "target_price": parent.get("target_price"),
                "native_price": parent.get("native_price"),
                "expected_parent_shares": parent.get("expected_parent_shares"),
                "placement_coverage": parent.get("placement_coverage"),
                "fill_allocation_coverage": parent.get("fill_allocation_coverage"),
                "parent_confidence": parent.get("confidence"),
                "signal_sampled_at_ms": sampled,
                "signal_age_ms": placement_ms - sampled,
                "label_side_up": int(side == "UP"),
                "placement_ticks_from_pre_best_bid": ticks,
                "label_near_best_1tick": int(ticks <= 1.25) if ticks is not None else None,
                "label_near_best_2ticks": int(ticks <= 2.25) if ticks is not None else None,
                "label_at_or_improves_best_bid": int(ticks <= 0.25) if ticks is not None else None,
                "label_matches_public_direction": int(side == public_direction) if public_direction is not None else None,
            }
            row.update(_public_features(snapshot))
            row.update(context)
            behavior_rows.append(row)

        _write_csv(hazard_output_path, HAZARD_OUTPUT_COLUMNS, hazard_rows)
        _write_csv(behavior_output_path, BEHAVIOR_OUTPUT_COLUMNS, behavior_rows)

        level_values = [float(row["placement_ticks_from_pre_best_bid"]) for row in behavior_rows if _number(row.get("placement_ticks_from_pre_best_bid")) is not None]
        side_up = sum(int(row["label_side_up"]) for row in behavior_rows)
        placement_markets = len({int(row["market_id"]) for row in behavior_rows})
        zero_placement_markets = sum(int(not placements_by_market.get(market_id)) for market_id in covered_markets)
        meta = {
            "datasetVersion": DATASET_VERSION,
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "labelIdentity": "INFERRED_TARGET_MAKER_PLACEMENT_FROM_ANONYMOUS_PUBLIC_BOOK_PLUS_KNOWN_TARGET_MAKER_FILLS",
            "coverageStartMs": coverage_start_ms,
            "excludedDeploymentMarketId": excluded_market_id,
            "thresholds": {
                "minParentConfidence": min_parent_confidence,
                "minPlacementCoverage": min_placement_coverage,
                "minFillAllocationCoverage": min_fill_coverage,
                "maxSignalAgeMs": max_signal_age_ms,
            },
            "hazard": {
                "rows": len(hazard_rows),
                "markets": len(covered_markets),
                "zeroPlacementMarkets": zero_placement_markets,
                "eligibleInferredPlacements": len(parents),
                "positiveRates": {
                    f"next{horizon}s": (sum(int(row[f"label_next_inferred_placement_any_{horizon}s"]) for row in hazard_rows) / len(hazard_rows) if hazard_rows else None)
                    for horizon in (1, 2, 5)
                },
                "sampling": "latest 8777 public snapshot per wall-clock second across every jointly covered market, including zero-placement markets",
            },
            "behavior": {
                "rows": len(behavior_rows),
                "markets": placement_markets,
                "missingStrictPrePlacementSnapshot": missing_pre_snapshot,
                "sideUpRate": side_up / len(behavior_rows) if behavior_rows else None,
                "levelTicksFromPreBestBidQuantiles": _quantiles(level_values),
                "invalidLevelRows": invalid_level,
            },
            "timestampBoundary": "Behavior features use the latest 8777 snapshot with sampled_at_ms strictly less than inferred placement_first_ms. Hazard labels require placement_ms > decision_sampled_at_ms.",
            "evidenceBoundary": "Placement identity is probabilistic because public CLOB levels are anonymous. V2.1 consumable allocation prevents one public depth change from being reused beyond its observed quantity, but does not prove private order ownership.",
        }
        resolved_meta = meta_output_path.expanduser().resolve()
        resolved_meta.parent.mkdir(parents=True, exist_ok=True)
        temp_meta = resolved_meta.with_suffix(resolved_meta.suffix + ".tmp")
        temp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_meta.replace(resolved_meta)
        return meta
    finally:
        maker.close()
        signal.close()
