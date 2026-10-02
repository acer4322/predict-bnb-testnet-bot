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

from .target_maker_ebm_dataset import (
    DEFAULT_MAKER_DB,
    DEFAULT_OUTPUT as DEFAULT_V1_OUTPUT,
    DEFAULT_SIGNAL_DB,
    FEATURE_COLUMNS as V1_FEATURE_COLUMNS,
    SIGNAL_COLUMNS,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_maker_ebm_dataset_v3.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_maker_ebm_dataset_v3.meta.json"
DATASET_VERSION = "TARGET_MAKER_EBM_DATASET_V3_TRAJECTORY_FILL_PATH_SIGNED_REPRICE"
TRAJECTORY_HORIZONS_MS = (250, 500, 1000, 2000)
DELAY_THRESHOLDS_MS = (250, 500, 1000, 2000, 5000)

METADATA_COLUMNS = [
    "dataset_version",
    "parent_id",
    "market_id",
    "order_hash",
    "target_side",
    "native_book_side",
    "first_target_ms",
    "last_target_ms",
    "post_action",
    "post_action_delay_ms",
    "post_action_native_price",
]

FILL_PATH_FEATURES = [
    "path_fill_leg_count",
    "path_fill_duration_ms",
    "path_fill_burst_count_250ms",
    "path_median_inter_fill_gap_ms",
    "path_max_inter_fill_gap_ms",
    "path_peak_leg_shares",
    "path_first_leg_shares",
    "path_last_leg_shares",
    "path_weighted_mean_fill_age_ms",
]
for horizon in TRAJECTORY_HORIZONS_MS:
    FILL_PATH_FEATURES.extend([
        f"path_shares_last_{horizon}ms",
        f"path_fraction_last_{horizon}ms",
    ])

TRAJECTORY_BASES = (
    "side_predict_mid",
    "side_predict_bid",
    "side_predict_spread",
    "side_aligned_spot_queue",
    "side_aligned_futures_queue",
    "side_aligned_spot_taker_250",
    "side_aligned_futures_taker_250",
    "side_aligned_direction",
)
TRAJECTORY_FEATURES: list[str] = []
for horizon in TRAJECTORY_HORIZONS_MS:
    TRAJECTORY_FEATURES.extend(f"traj_{name}_delta_{horizon}ms" for name in TRAJECTORY_BASES)
    TRAJECTORY_FEATURES.extend([
        f"traj_spot_price_return_{horizon}ms_bps",
        f"traj_futures_price_return_{horizon}ms_bps",
    ])

LABEL_COLUMNS = [
    "label_continue",
    "label_refill_given_continue",
    "label_reprice_toward_touch",
    "label_reprice_ticks_abs",
    "label_reprice_signed_ticks_toward",
]
LABEL_COLUMNS.extend(f"label_continue_delay_le_{horizon}ms" for horizon in DELAY_THRESHOLDS_MS)

POLICY_FEATURE_COLUMNS = [
    column
    for column in V1_FEATURE_COLUMNS
    if column not in {
        "placement_coverage",
        "fill_allocation_coverage",
        "parent_confidence",
        "placement_supports_18",
        "signal_age_ms",
    }
] + FILL_PATH_FEATURES + TRAJECTORY_FEATURES
OUTPUT_COLUMNS = METADATA_COLUMNS + POLICY_FEATURE_COLUMNS + LABEL_COLUMNS


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


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _parent_id(row: dict[str, Any]) -> str:
    identity = str(row.get("order_hash") or row.get("leg_id") or "NO_ORDER")
    return f"{int(row['market_id'])}:{identity}:{row['side']}:{float(row['target_price']):.12g}"


def _load_v1_rows(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _target_fill_paths(db: sqlite3.Connection, market_id: int) -> dict[str, list[tuple[int, float]]]:
    rows = db.execute(
        """SELECT leg_id,order_hash,market_id,target_event_ms,side,target_price,target_shares
             FROM maker_book_inference_target_events
            WHERE market_id=? AND status='MATCHED'
            ORDER BY target_event_ms,leg_id""",
        (int(market_id),),
    )
    grouped: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        grouped[_parent_id(row)].append((int(row["target_event_ms"]), float(row["target_shares"])))
    return grouped


def _path_features(events: list[tuple[int, float]], anchor_ms: int, total_shares: float) -> dict[str, Any]:
    eligible = sorted((ms, shares) for ms, shares in events if ms <= anchor_ms)
    if not eligible:
        return {column: None for column in FILL_PATH_FEATURES}
    times = [ms for ms, _ in eligible]
    shares = [max(0.0, float(qty)) for _, qty in eligible]
    gaps = [right - left for left, right in zip(times, times[1:])]
    bursts = 1 + sum(int(gap > 250) for gap in gaps)
    denominator = max(1e-9, float(total_shares))
    weighted_age = sum((anchor_ms - ms) * qty for (ms, qty) in eligible) / max(1e-9, sum(shares))
    result: dict[str, Any] = {
        "path_fill_leg_count": len(eligible),
        "path_fill_duration_ms": max(times) - min(times),
        "path_fill_burst_count_250ms": bursts,
        "path_median_inter_fill_gap_ms": statistics.median(gaps) if gaps else 0.0,
        "path_max_inter_fill_gap_ms": max(gaps) if gaps else 0.0,
        "path_peak_leg_shares": max(shares),
        "path_first_leg_shares": shares[0],
        "path_last_leg_shares": shares[-1],
        "path_weighted_mean_fill_age_ms": weighted_age,
    }
    for horizon in TRAJECTORY_HORIZONS_MS:
        recent = sum(qty for ms, qty in eligible if anchor_ms - horizon <= ms <= anchor_ms)
        result[f"path_shares_last_{horizon}ms"] = recent
        result[f"path_fraction_last_{horizon}ms"] = min(1.0, recent / denominator)
    return result


def _load_signal_rows(
    db: sqlite3.Connection, market_id: int, start_ms: int, end_ms: int
) -> tuple[list[int], list[dict[str, Any]]]:
    columns = ",".join(SIGNAL_COLUMNS)
    rows = [dict(row) for row in db.execute(
        f"""SELECT {columns} FROM wallet_taker_signal_snapshots
             WHERE market_id=? AND sampled_at_ms BETWEEN ? AND ? ORDER BY sampled_at_ms""",
        (int(market_id), int(start_ms), int(end_ms)),
    )]
    return [int(row["sampled_at_ms"]) for row in rows], rows


def _asof(
    times: list[int], rows: list[dict[str, Any]], at_ms: int, max_age_ms: int
) -> dict[str, Any] | None:
    index = bisect.bisect_right(times, int(at_ms)) - 1
    if index < 0:
        return None
    age = int(at_ms) - times[index]
    return rows[index] if 0 <= age <= max_age_ms else None


def _spread(row: dict[str, Any], side: str) -> float | None:
    prefix = "predict_up" if side == "UP" else "predict_down"
    bid = _number(row.get(f"{prefix}_bid"))
    ask = _number(row.get(f"{prefix}_ask"))
    return ask - bid if bid is not None and ask is not None else None


def _side_value(row: dict[str, Any], side: str, suffix: str) -> float | None:
    prefix = "predict_up" if side == "UP" else "predict_down"
    return _number(row.get(f"{prefix}_{suffix}"))


def _aligned(row: dict[str, Any], side: str, field: str) -> float | None:
    value = _number(row.get(field))
    if value is None:
        return None
    return value if side == "UP" else -value


def _safe_delta(current: float | None, old: float | None) -> float | None:
    return current - old if current is not None and old is not None else None


def _price_return_bps(current: Any, old: Any) -> float | None:
    current_value = _number(current)
    old_value = _number(old)
    if current_value is None or old_value is None or abs(old_value) <= 1e-12:
        return None
    return (current_value / old_value - 1.0) * 10_000.0


def _snapshot_state(row: dict[str, Any], side: str) -> dict[str, float | None]:
    return {
        "side_predict_mid": _side_value(row, side, "mid"),
        "side_predict_bid": _side_value(row, side, "bid"),
        "side_predict_spread": _spread(row, side),
        "side_aligned_spot_queue": _aligned(row, side, "spot_queue_imbalance"),
        "side_aligned_futures_queue": _aligned(row, side, "futures_queue_imbalance"),
        "side_aligned_spot_taker_250": _aligned(row, side, "spot_taker_imbalance_250ms"),
        "side_aligned_futures_taker_250": _aligned(row, side, "futures_taker_imbalance_250ms"),
        "side_aligned_direction": _aligned(row, side, "direction_score"),
    }


def _trajectory_features(
    times: list[int], rows: list[dict[str, Any]], anchor_ms: int, side: str, max_age_ms: int
) -> dict[str, Any]:
    current = _asof(times, rows, anchor_ms, max_age_ms)
    output = {column: None for column in TRAJECTORY_FEATURES}
    if current is None:
        return output
    current_state = _snapshot_state(current, side)
    for horizon in TRAJECTORY_HORIZONS_MS:
        old = _asof(times, rows, anchor_ms - horizon, max_age_ms)
        if old is None:
            continue
        old_state = _snapshot_state(old, side)
        for name in TRAJECTORY_BASES:
            output[f"traj_{name}_delta_{horizon}ms"] = _safe_delta(current_state[name], old_state[name])
        output[f"traj_spot_price_return_{horizon}ms_bps"] = _price_return_bps(
            current.get("spot_price"), old.get("spot_price")
        )
        output[f"traj_futures_price_return_{horizon}ms_bps"] = _price_return_bps(
            current.get("futures_price"), old.get("futures_price")
        )
    return output


def _label_features(row: dict[str, Any], native_side: str) -> dict[str, Any]:
    action = str(row.get("post_action") or "")
    continue_action = action in {
        "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT",
        "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT",
    }
    result: dict[str, Any] = {
        "label_continue": int(continue_action),
        "label_refill_given_continue": None,
        "label_reprice_toward_touch": None,
        "label_reprice_ticks_abs": None,
        "label_reprice_signed_ticks_toward": None,
    }
    delay = _number(row.get("post_action_delay_ms"))
    for horizon in DELAY_THRESHOLDS_MS:
        result[f"label_continue_delay_le_{horizon}ms"] = (
            int(delay <= horizon) if continue_action and delay is not None else None
        )
    if action == "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT":
        result["label_refill_given_continue"] = 1
        return result
    if action != "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT":
        return result
    result["label_refill_given_continue"] = 0
    old_price = _number(row.get("native_price"))
    new_price = _number(row.get("post_action_native_price"))
    if old_price is None or new_price is None:
        return result
    native_ticks = (new_price - old_price) / 0.01
    signed_toward = native_ticks if native_side == "BID" else -native_ticks
    result["label_reprice_signed_ticks_toward"] = signed_toward
    result["label_reprice_ticks_abs"] = abs(native_ticks)
    result["label_reprice_toward_touch"] = int(signed_toward > 0)
    return result


def build_v3_dataset(
    *,
    v1_dataset_path: Path = DEFAULT_V1_OUTPUT,
    maker_db_path: Path = DEFAULT_MAKER_DB,
    signal_db_path: Path = DEFAULT_SIGNAL_DB,
    output_path: Path = DEFAULT_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
    trajectory_snapshot_max_age_ms: int = 600,
) -> dict[str, Any]:
    base_rows = _load_v1_rows(v1_dataset_path)
    maker = _connect_readonly(maker_db_path)
    signal = _connect_readonly(signal_db_path)
    try:
        for table, db in (
            ("maker_book_inference_v21_parent_lifecycles", maker),
            ("maker_book_inference_target_events", maker),
            ("wallet_taker_signal_snapshots", signal),
        ):
            if not _has_table(db, table):
                raise RuntimeError(f"required table is missing: {table}")

        parents = {
            str(row["parent_id"]): dict(row)
            for row in maker.execute(
                """SELECT parent_id,native_book_side FROM maker_book_inference_v21_parent_lifecycles"""
            )
        }
        grouped_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in base_rows:
            grouped_rows[int(row["market_id"])].append(row)

        output_rows: list[dict[str, Any]] = []
        missing_parent = 0
        for market_id, market_rows in sorted(grouped_rows.items()):
            paths = _target_fill_paths(maker, market_id)
            anchors = [int(float(row["last_target_ms"])) for row in market_rows]
            signal_times, signal_rows = _load_signal_rows(
                signal,
                market_id,
                min(anchors) - max(TRAJECTORY_HORIZONS_MS) - trajectory_snapshot_max_age_ms,
                max(anchors),
            )
            for base_row in market_rows:
                parent_id = str(base_row["parent_id"])
                parent = parents.get(parent_id)
                if parent is None:
                    missing_parent += 1
                    continue
                side = str(base_row["target_side"])
                native_side = str(parent["native_book_side"])
                anchor = int(float(base_row["last_target_ms"]))
                total_shares = float(base_row.get("target_filled_shares") or 0.0)
                row = {column: base_row.get(column) for column in V1_FEATURE_COLUMNS}
                row.update({
                    "dataset_version": DATASET_VERSION,
                    "parent_id": parent_id,
                    "market_id": market_id,
                    "order_hash": base_row.get("order_hash"),
                    "target_side": side,
                    "native_book_side": native_side,
                    "first_target_ms": base_row.get("first_target_ms"),
                    "last_target_ms": base_row.get("last_target_ms"),
                    "post_action": base_row.get("post_action"),
                    "post_action_delay_ms": base_row.get("post_action_delay_ms"),
                    "post_action_native_price": base_row.get("post_action_native_price"),
                })
                row.update(_path_features(paths.get(parent_id, []), anchor, total_shares))
                row.update(_trajectory_features(
                    signal_times,
                    signal_rows,
                    anchor,
                    side,
                    max(0, int(trajectory_snapshot_max_age_ms)),
                ))
                row.update(_label_features(row, native_side))
                output_rows.append(row)

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

        signed = [
            row for row in output_rows
            if row.get("label_reprice_signed_ticks_toward") is not None
        ]
        meta = {
            "datasetVersion": DATASET_VERSION,
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "rows": len(output_rows),
            "markets": len({int(row["market_id"]) for row in output_rows}),
            "sourceV1Rows": len(base_rows),
            "missingParentRows": missing_parent,
            "trajectoryHorizonsMs": list(TRAJECTORY_HORIZONS_MS),
            "trajectorySnapshotMaxAgeMs": int(trajectory_snapshot_max_age_ms),
            "fillPathBasis": "known target Maker fill legs at or before parent last_target_ms; no future fill legs are used",
            "signedRepriceBasis": "positive signed ticks means toward touch: higher native BID or lower native ASK",
            "signedRepriceRows": len(signed),
            "policyFeatureColumns": POLICY_FEATURE_COLUMNS,
            "fillPathFeatureColumns": FILL_PATH_FEATURES,
            "trajectoryFeatureColumns": TRAJECTORY_FEATURES,
            "labelColumns": LABEL_COLUMNS,
            "sources": {
                "v1Dataset": str(v1_dataset_path),
                "makerDb": str(maker_db_path),
                "signalDb": str(signal_db_path),
            },
            "causalityBoundary": (
                "trajectory snapshots are strict as-of at each historical offset; fill-path features use only target events "
                "known by the parent decision anchor. Post-action price/delay are labels only and never policy features."
            ),
            "output": str(output_path),
        }
        meta_temp = meta_output_path.with_suffix(meta_output_path.suffix + ".tmp")
        meta_temp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_temp.replace(meta_output_path)
        return meta
    finally:
        maker.close()
        signal.close()
