from __future__ import annotations

import bisect
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAKER_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_SIGNAL_DB = ROOT / "data" / "wallet_taker_signals.db"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_maker_ebm_dataset.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_maker_ebm_dataset.meta.json"

DATASET_VERSION = "TARGET_MAKER_EBM_DATASET_V1"
DECISION_POINT = "AFTER_PARENT_FILL_BEFORE_NEXT_PARENT"
KNOWN_POST_ACTIONS = {
    "NO_CONFIRMED_NEXT_PARENT_5S",
    "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT",
    "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT",
}

SIGNAL_COLUMNS = (
    "sampled_at_ms",
    "seconds_left",
    "strike_price",
    "predict_up_bid",
    "predict_up_ask",
    "predict_up_mid",
    "predict_down_bid",
    "predict_down_ask",
    "predict_down_mid",
    "spot_price",
    "spot_microprice",
    "spot_queue_imbalance",
    "spot_taker_imbalance_250ms",
    "spot_taker_imbalance_1s",
    "spot_return_250ms_bps",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "spot_return_5s_bps",
    "futures_price",
    "futures_microprice",
    "futures_queue_imbalance",
    "futures_taker_imbalance_250ms",
    "futures_taker_imbalance_1s",
    "futures_return_250ms_bps",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "futures_return_5s_bps",
    "perp_spot_basis_bps",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "spot_minus_chainlink_bps",
    "direction_score",
)

FEATURE_COLUMNS = [
    "seconds_left",
    "target_side_is_up",
    "target_price",
    "native_price",
    "resting_ms",
    "target_fill_count",
    "target_filled_shares",
    "expected_parent_shares",
    "placement_coverage",
    "fill_allocation_coverage",
    "parent_confidence",
    "multi_fill_parent",
    "observed_filled_near_18",
    "placement_supports_18",
    "prior_parent_count",
    "prior_maker_up_shares",
    "prior_maker_down_shares",
    "prior_maker_delta_shares",
    "prior_maker_imbalance_ratio",
    "prior_maker_paired_coverage",
    "side_aligned_prior_delta_shares",
    "predict_up_bid",
    "predict_up_ask",
    "predict_up_mid",
    "predict_down_bid",
    "predict_down_ask",
    "predict_down_mid",
    "predict_up_spread",
    "predict_down_spread",
    "side_predict_bid",
    "side_predict_ask",
    "side_predict_mid",
    "side_predict_spread",
    "opposite_predict_bid",
    "opposite_predict_ask",
    "opposite_predict_mid",
    "opposite_predict_spread",
    "strike_price",
    "spot_price",
    "spot_microprice",
    "spot_queue_imbalance",
    "spot_taker_imbalance_250ms",
    "spot_taker_imbalance_1s",
    "spot_return_250ms_bps",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "spot_return_5s_bps",
    "futures_price",
    "futures_microprice",
    "futures_queue_imbalance",
    "futures_taker_imbalance_250ms",
    "futures_taker_imbalance_1s",
    "futures_return_250ms_bps",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "futures_return_5s_bps",
    "perp_spot_basis_bps",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "spot_minus_chainlink_bps",
    "direction_score",
    "side_aligned_direction_score",
    "side_aligned_spot_queue_imbalance",
    "side_aligned_spot_taker_imbalance_250ms",
    "side_aligned_spot_taker_imbalance_1s",
    "side_aligned_spot_return_1s_bps",
    "side_aligned_spot_return_3s_bps",
    "side_aligned_futures_queue_imbalance",
    "side_aligned_futures_taker_imbalance_250ms",
    "side_aligned_futures_taker_imbalance_1s",
    "side_aligned_futures_return_1s_bps",
    "side_aligned_futures_return_3s_bps",
    "signal_age_ms",
]

LABEL_COLUMNS = [
    "label_reprice_1_3_ticks",
    "label_same_price_refill",
]

METADATA_COLUMNS = [
    "dataset_version",
    "decision_point",
    "parent_id",
    "market_id",
    "order_hash",
    "target_side",
    "first_target_ms",
    "last_target_ms",
    "post_action",
    "post_action_delay_ms",
    "post_action_native_price",
    "signal_sampled_at_ms",
]

OUTPUT_COLUMNS = METADATA_COLUMNS + FEATURE_COLUMNS + LABEL_COLUMNS


def _connect_readonly(path: Path) -> sqlite3.Connection:
    path = path.expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _has_table(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or abs(number) == float("inf"):
        return None
    return number


def _spread(bid: Any, ask: Any) -> float | None:
    bid_value = _number(bid)
    ask_value = _number(ask)
    if bid_value is None or ask_value is None:
        return None
    return ask_value - bid_value


def _aligned(value: Any, sign: float) -> float | None:
    number = _number(value)
    return number * sign if number is not None else None


def _inventory_features(up_shares: float, down_shares: float, parent_count: int, side_sign: float) -> dict[str, Any]:
    total = up_shares + down_shares
    delta = up_shares - down_shares
    if total > 1e-9:
        imbalance = abs(delta) / total
        paired = 1.0 - imbalance
    else:
        imbalance = None
        paired = None
    return {
        "prior_parent_count": int(parent_count),
        "prior_maker_up_shares": up_shares,
        "prior_maker_down_shares": down_shares,
        "prior_maker_delta_shares": delta,
        "prior_maker_imbalance_ratio": imbalance,
        "prior_maker_paired_coverage": paired,
        "side_aligned_prior_delta_shares": delta * side_sign,
    }


def _signal_features(signal: dict[str, Any], target_side: str, signal_age_ms: int) -> dict[str, Any]:
    side_is_up = target_side == "UP"
    sign = 1.0 if side_is_up else -1.0
    up = {
        "bid": signal.get("predict_up_bid"),
        "ask": signal.get("predict_up_ask"),
        "mid": signal.get("predict_up_mid"),
    }
    down = {
        "bid": signal.get("predict_down_bid"),
        "ask": signal.get("predict_down_ask"),
        "mid": signal.get("predict_down_mid"),
    }
    side = up if side_is_up else down
    opposite = down if side_is_up else up
    result = {key: signal.get(key) for key in SIGNAL_COLUMNS if key != "sampled_at_ms"}
    result.update(
        target_side_is_up=int(side_is_up),
        predict_up_spread=_spread(up["bid"], up["ask"]),
        predict_down_spread=_spread(down["bid"], down["ask"]),
        side_predict_bid=side["bid"],
        side_predict_ask=side["ask"],
        side_predict_mid=side["mid"],
        side_predict_spread=_spread(side["bid"], side["ask"]),
        opposite_predict_bid=opposite["bid"],
        opposite_predict_ask=opposite["ask"],
        opposite_predict_mid=opposite["mid"],
        opposite_predict_spread=_spread(opposite["bid"], opposite["ask"]),
        side_aligned_direction_score=_aligned(signal.get("direction_score"), sign),
        side_aligned_spot_queue_imbalance=_aligned(signal.get("spot_queue_imbalance"), sign),
        side_aligned_spot_taker_imbalance_250ms=_aligned(signal.get("spot_taker_imbalance_250ms"), sign),
        side_aligned_spot_taker_imbalance_1s=_aligned(signal.get("spot_taker_imbalance_1s"), sign),
        side_aligned_spot_return_1s_bps=_aligned(signal.get("spot_return_1s_bps"), sign),
        side_aligned_spot_return_3s_bps=_aligned(signal.get("spot_return_3s_bps"), sign),
        side_aligned_futures_queue_imbalance=_aligned(signal.get("futures_queue_imbalance"), sign),
        side_aligned_futures_taker_imbalance_250ms=_aligned(signal.get("futures_taker_imbalance_250ms"), sign),
        side_aligned_futures_taker_imbalance_1s=_aligned(signal.get("futures_taker_imbalance_1s"), sign),
        side_aligned_futures_return_1s_bps=_aligned(signal.get("futures_return_1s_bps"), sign),
        side_aligned_futures_return_3s_bps=_aligned(signal.get("futures_return_3s_bps"), sign),
        signal_age_ms=signal_age_ms,
    )
    return result


def _load_signal_rows(
    connection: sqlite3.Connection,
    market_id: int,
    start_ms: int,
    end_ms: int,
) -> tuple[list[int], list[dict[str, Any]]]:
    columns = ",".join(SIGNAL_COLUMNS)
    rows = [
        dict(row)
        for row in connection.execute(
            f"""SELECT {columns}
                  FROM wallet_taker_signal_snapshots
                 WHERE market_id=? AND sampled_at_ms BETWEEN ? AND ?
                 ORDER BY sampled_at_ms""",
            (int(market_id), int(start_ms), int(end_ms)),
        )
    ]
    return [int(row["sampled_at_ms"]) for row in rows], rows


def _asof_signal(times: list[int], rows: list[dict[str, Any]], at_ms: int, max_age_ms: int) -> tuple[dict[str, Any] | None, int | None]:
    index = bisect.bisect_right(times, int(at_ms)) - 1
    if index < 0:
        return None, None
    sampled_at_ms = times[index]
    age = int(at_ms) - sampled_at_ms
    if age < 0 or age > max_age_ms:
        return None, age
    return rows[index], age


def _iter_parent_rows(connection: sqlite3.Connection) -> Iterable[dict[str, Any]]:
    query = """
        SELECT parent_id,market_id,order_hash,target_side,native_book_side,target_price,native_price,
               first_target_ms,last_target_ms,target_fill_count,target_filled_shares,expected_parent_shares,
               allocated_fill_shares,fill_allocation_coverage,placement_allocated_shares,placement_coverage,
               placement_first_ms,placement_last_ms,resting_ms,post_action,post_action_delay_ms,
               post_action_native_price,multi_fill_parent,observed_filled_near_18,placement_supports_18,
               confidence
          FROM maker_book_inference_v21_parent_lifecycles
         ORDER BY market_id,last_target_ms,parent_id
    """
    for row in connection.execute(query):
        yield dict(row)


def build_dataset(
    *,
    maker_db_path: Path = DEFAULT_MAKER_DB,
    signal_db_path: Path = DEFAULT_SIGNAL_DB,
    output_path: Path = DEFAULT_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
    max_signal_age_ms: int = 2_000,
    min_parent_confidence: float = 0.70,
    min_placement_coverage: float = 0.80,
    min_fill_allocation_coverage: float = 0.80,
) -> dict[str, Any]:
    maker = _connect_readonly(maker_db_path)
    signal = _connect_readonly(signal_db_path)
    try:
        if not _has_table(maker, "maker_book_inference_v21_parent_lifecycles"):
            raise RuntimeError("8778 V2.1 parent lifecycle table is missing; run the V2.1 collector first")
        if not _has_table(signal, "wallet_taker_signal_snapshots"):
            raise RuntimeError("8777 signal snapshot table is missing; run the Taker signal collector first")

        parents_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
        source_parent_count = 0
        for parent in _iter_parent_rows(maker):
            source_parent_count += 1
            parents_by_market[int(parent["market_id"])].append(parent)

        counts = defaultdict(int)
        output_rows: list[dict[str, Any]] = []
        for market_id in sorted(parents_by_market):
            parents = sorted(parents_by_market[market_id], key=lambda row: (int(row["last_target_ms"]), str(row["parent_id"])))
            if not parents:
                continue
            minimum_ms = min(int(row["last_target_ms"]) for row in parents) - max_signal_age_ms
            maximum_ms = max(int(row["last_target_ms"]) for row in parents)
            signal_times, signal_rows = _load_signal_rows(signal, market_id, minimum_ms, maximum_ms)
            prior_up = 0.0
            prior_down = 0.0
            prior_count = 0

            for parent in parents:
                side = str(parent["target_side"])
                side_sign = 1.0 if side == "UP" else -1.0
                target_filled = float(parent["target_filled_shares"])
                inventory = _inventory_features(prior_up, prior_down, prior_count, side_sign)

                reason: str | None = None
                if str(parent.get("post_action")) not in KNOWN_POST_ACTIONS:
                    reason = "unknown_post_action"
                elif float(parent.get("confidence") or 0.0) < min_parent_confidence:
                    reason = "low_parent_confidence"
                elif float(parent.get("placement_coverage") or 0.0) < min_placement_coverage:
                    reason = "low_placement_coverage"
                elif float(parent.get("fill_allocation_coverage") or 0.0) < min_fill_allocation_coverage:
                    reason = "low_fill_allocation_coverage"

                at_ms = int(parent["last_target_ms"])
                asof, signal_age = _asof_signal(signal_times, signal_rows, at_ms, max_signal_age_ms)
                if reason is None and asof is None:
                    reason = "missing_fresh_8777_signal"

                if reason is not None:
                    counts[f"skipped_{reason}"] += 1
                else:
                    assert asof is not None and signal_age is not None
                    action = str(parent["post_action"])
                    row: dict[str, Any] = {
                        "dataset_version": DATASET_VERSION,
                        "decision_point": DECISION_POINT,
                        "parent_id": parent["parent_id"],
                        "market_id": market_id,
                        "order_hash": parent.get("order_hash"),
                        "target_side": side,
                        "first_target_ms": int(parent["first_target_ms"]),
                        "last_target_ms": at_ms,
                        "post_action": action,
                        "post_action_delay_ms": parent.get("post_action_delay_ms"),
                        "post_action_native_price": parent.get("post_action_native_price"),
                        "signal_sampled_at_ms": int(asof["sampled_at_ms"]),
                        "target_price": parent.get("target_price"),
                        "native_price": parent.get("native_price"),
                        "resting_ms": parent.get("resting_ms"),
                        "target_fill_count": parent.get("target_fill_count"),
                        "target_filled_shares": target_filled,
                        "expected_parent_shares": parent.get("expected_parent_shares"),
                        "placement_coverage": parent.get("placement_coverage"),
                        "fill_allocation_coverage": parent.get("fill_allocation_coverage"),
                        "parent_confidence": parent.get("confidence"),
                        "multi_fill_parent": parent.get("multi_fill_parent"),
                        "observed_filled_near_18": parent.get("observed_filled_near_18"),
                        "placement_supports_18": parent.get("placement_supports_18"),
                        "label_reprice_1_3_ticks": int(action == "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT"),
                        "label_same_price_refill": int(action == "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT"),
                    }
                    row.update(inventory)
                    row.update(_signal_features(asof, side, signal_age))
                    output_rows.append(row)
                    counts["included"] += 1
                    counts[f"label_{action}"] += 1

                if side == "UP":
                    prior_up += target_filled
                else:
                    prior_down += target_filled
                prior_count += 1

        output_path = output_path.expanduser().resolve()
        meta_output_path = meta_output_path.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        temp = output_path.with_suffix(output_path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in output_rows:
                writer.writerow({column: row.get(column) for column in OUTPUT_COLUMNS})
        temp.replace(output_path)

        market_count = len({int(row["market_id"]) for row in output_rows})
        meta = {
            "datasetVersion": DATASET_VERSION,
            "decisionPoint": DECISION_POINT,
            "paperResearchOnly": True,
            "targetEventsDriveTrading": False,
            "source": {
                "makerDb": str(Path(maker_db_path)),
                "makerTable": "maker_book_inference_v21_parent_lifecycles",
                "signalDb": str(Path(signal_db_path)),
                "signalTable": "wallet_taker_signal_snapshots",
            },
            "qualityFilters": {
                "maxSignalAgeMs": int(max_signal_age_ms),
                "minParentConfidence": float(min_parent_confidence),
                "minPlacementCoverage": float(min_placement_coverage),
                "minFillAllocationCoverage": float(min_fill_allocation_coverage),
            },
            "rows": len(output_rows),
            "markets": market_count,
            "sourceParents": source_parent_count,
            "counts": dict(sorted(counts.items())),
            "featureColumns": FEATURE_COLUMNS,
            "labelColumns": LABEL_COLUMNS,
            "labelSemantics": {
                "label_reprice_1_3_ticks": "next quantity-allocated target parent placement on the same native side is 1-3 ticks away within 5 seconds",
                "label_same_price_refill": "next quantity-allocated target parent placement on the same native side is at the same price within 5 seconds",
            },
            "causality": "8777 features are strict as-of snapshots at or before the target parent's last fill timestamp; prior inventory uses only earlier target parents in the same market",
            "identityBoundary": "target fills/order hashes are known; 8778 placement ownership remains probabilistic and is filtered by V2.1 consumable-allocation confidence/coverage",
            "output": str(output_path),
        }
        meta_temp = meta_output_path.with_suffix(meta_output_path.suffix + ".tmp")
        meta_temp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_temp.replace(meta_output_path)
        return meta
    finally:
        maker.close()
        signal.close()
