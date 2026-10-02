from __future__ import annotations

import bisect
import csv
import json
import math
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from .target_maker_ebm_dataset import DEFAULT_SIGNAL_DB
from .target_maker_taker_link import DEFAULT_SHADOW_DB, TAKER_COHORT
from .target_taker_behavior_v1 import SIDE_MODEL_FEATURES, _public_features

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_taker_direct_eligibility_special_regime_v1.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_taker_direct_eligibility_special_regime_v1.meta.json"
DATASET_VERSION = "TARGET_TAKER_DIRECT_ELIGIBILITY_SPECIAL_REGIME_V1_STRICT_FUTURE_SECOND_BUCKET"
MAX_SIGNAL_AGE_MS = 2_000

# Same 16-feature public set used by the current compact Target Taker side EBM.
# signal_age_ms is 0 for a direct risk-set decision because the row itself is the public snapshot.
FROZEN16_FEATURES = [
    "seconds_left",
    "predict_up_mid",
    "predict_up_spread",
    "predict_down_spread",
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
    "signal_age_ms",
]

SPECIAL_REGIME_FEATURES = [
    "abs_spot_minus_strike_bps",
    "spot_near_strike_005_share_10s",
    "spot_near_strike_005_share_30s",
    "spot_near_strike_010_share_10s",
    "spot_near_strike_010_share_30s",
    "strike_cross_count_10s",
    "strike_cross_count_30s",
    "ms_since_last_strike_cross",
    "spot_realized_vol_3s_bps",
    "spot_realized_vol_10s_bps",
    "spot_realized_vol_30s_bps",
    "futures_realized_vol_3s_bps",
    "futures_realized_vol_10s_bps",
    "futures_realized_vol_30s_bps",
    "predict_up_mid_ema_5s",
    "predict_up_mid_ema_15s",
    "predict_up_mid_ema_30s",
    "predict_up_mid_slope_5s_per_s",
    "predict_up_mid_slope_15s_per_s",
    "predict_bias_persistence_10s",
    "predict_bias_persistence_30s",
    "predict_bias_edge_abs",
    "predict_up_mid_change_5s",
    "predict_up_mid_change_10s",
    "spot_return_window_5s_bps",
    "spot_return_window_10s_bps",
    "futures_return_window_5s_bps",
    "futures_return_window_10s_bps",
    "predict_vs_spot_direction_5s",
    "predict_vs_futures_direction_5s",
    "futures_minus_spot_return_1s_bps",
    "futures_minus_spot_return_3s_bps",
    "futures_minus_spot_return_5s_bps",
    "futures_minus_spot_price_bps",
    "chainlink_minus_spot_bps",
]

METADATA_COLUMNS = [
    "dataset_version",
    "market_id",
    "decision_sampled_at_ms",
    "decision_bucket_start_ms",
    "next_target_taker_event_bucket_ms",
    "ms_to_next_target_taker_bucket",
]
LABEL_COLUMNS = [
    "label_next_target_taker_any_1s",
    "label_next_target_taker_any_2s",
    "label_next_target_taker_any_5s",
]
OUTPUT_COLUMNS = METADATA_COLUMNS + list(SIDE_MODEL_FEATURES) + SPECIAL_REGIME_FEATURES + LABEL_COLUMNS


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
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _signal_deployed_at_ms(db: sqlite3.Connection) -> int:
    if not _has_table(db, "wallet_taker_signal_meta"):
        return 0
    row = db.execute(
        "SELECT value FROM wallet_taker_signal_meta WHERE key='deployed_at_ms'"
    ).fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def _target_mirror_coverage(db: sqlite3.Connection) -> tuple[int, int | None]:
    row = db.execute(
        """SELECT deployed_at_ms,excluded_market_id
             FROM wallet_target_taker_mirror_meta
            WHERE cohort=? LIMIT 1""",
        (TAKER_COHORT,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing target Taker mirror meta for cohort {TAKER_COHORT}")
    return (
        int(row["deployed_at_ms"]),
        int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None,
    )


def _latest_snapshot_per_second(
    db: sqlite3.Connection, market_id: int, *, coverage_start_ms: int
) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in db.execute(
            """SELECT *
                 FROM wallet_taker_signal_snapshots
                WHERE market_id=? AND sampled_at_ms>=?
                ORDER BY sampled_at_ms,timestamp_ns""",
            (int(market_id), int(coverage_start_ms)),
        )
    ]
    latest: dict[int, dict[str, Any]] = {}
    for row in rows:
        bucket = int(row["sampled_at_ms"]) // 1000
        latest[bucket] = row
    return [latest[key] for key in sorted(latest)]


def _window(history: list[dict[str, Any]], index: int, seconds: int) -> list[dict[str, Any]]:
    now = int(history[index]["sampled_at_ms"])
    floor = now - int(seconds) * 1000
    start = index
    while start > 0 and int(history[start - 1]["sampled_at_ms"]) >= floor:
        start -= 1
    return history[start : index + 1]


def _first_last_numeric(
    rows: list[dict[str, Any]], key: str
) -> tuple[tuple[int, float] | None, tuple[int, float] | None]:
    values: list[tuple[int, float]] = []
    for row in rows:
        value = _number(row.get(key))
        if value is not None:
            values.append((int(row["sampled_at_ms"]), value))
    return (values[0], values[-1]) if values else (None, None)


def _window_return_bps(rows: list[dict[str, Any]], key: str) -> float | None:
    first, last = _first_last_numeric(rows, key)
    if first is None or last is None or first[1] <= 0 or last[1] <= 0:
        return None
    return math.log(last[1] / first[1]) * 10_000.0


def _realized_vol_bps(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_number(row.get(key)) for row in rows]
    prices = [value for value in values if value is not None and value > 0]
    if len(prices) < 3:
        return None
    returns = [
        math.log(prices[i] / prices[i - 1]) * 10_000.0
        for i in range(1, len(prices))
        if prices[i - 1] > 0 and prices[i] > 0
    ]
    return float(statistics.pstdev(returns)) if len(returns) >= 2 else None


def _ema(rows: list[dict[str, Any]], key: str, span: int) -> float | None:
    values = [_number(row.get(key)) for row in rows]
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    alpha = 2.0 / (max(1, int(span)) + 1.0)
    result = clean[0]
    for value in clean[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return float(result)


def _slope_per_second(rows: list[dict[str, Any]], key: str) -> float | None:
    first, last = _first_last_numeric(rows, key)
    if first is None or last is None:
        return None
    elapsed = (last[0] - first[0]) / 1000.0
    if elapsed <= 0:
        return None
    return (last[1] - first[1]) / elapsed


def _near_strike_share(rows: list[dict[str, Any]], threshold_bps: float) -> float | None:
    values = [_number(row.get("spot_minus_strike_bps")) for row in rows]
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(abs(value) <= threshold_bps for value in clean) / len(clean)


def _sign(value: float | None, eps: float = 1e-9) -> int:
    if value is None or abs(value) <= eps:
        return 0
    return 1 if value > 0 else -1


def _cross_timestamps(rows: list[dict[str, Any]]) -> list[int]:
    result: list[int] = []
    previous_sign = 0
    for row in rows:
        sign = _sign(_number(row.get("spot_minus_strike_bps")))
        if sign == 0:
            continue
        if previous_sign and sign != previous_sign:
            result.append(int(row["sampled_at_ms"]))
        previous_sign = sign
    return result


def _bias_persistence(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    current = _number(rows[-1].get("predict_up_mid"))
    if current is None:
        return None
    current_sign = _sign(current - 0.5, eps=0.01)
    if current_sign == 0:
        return 0.0
    values = []
    for row in rows:
        mid = _number(row.get("predict_up_mid"))
        if mid is None:
            continue
        edge = mid - 0.5
        values.append(int(_sign(edge, eps=0.05) == current_sign))
    return sum(values) / len(values) if values else None


def _direction_match(a: float | None, b: float | None) -> int | None:
    sa, sb = _sign(a), _sign(b)
    if sa == 0 or sb == 0:
        return 0
    return 1 if sa == sb else -1


def _bps(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b <= 0:
        return None
    return (a / b - 1.0) * 10_000.0


def _special_features(
    history: list[dict[str, Any]], index: int, *, last_cross_ms: int | None = None
) -> dict[str, Any]:
    current = history[index]
    w3 = _window(history, index, 3)
    w5 = _window(history, index, 5)
    w10 = _window(history, index, 10)
    w15 = _window(history, index, 15)
    w30 = _window(history, index, 30)

    spot_distance = _number(current.get("spot_minus_strike_bps"))
    up_mid = _number(current.get("predict_up_mid"))
    spot5 = _window_return_bps(w5, "spot_price")
    spot10 = _window_return_bps(w10, "spot_price")
    fut5 = _window_return_bps(w5, "futures_price")
    fut10 = _window_return_bps(w10, "futures_price")
    pred_first_5, pred_last_5 = _first_last_numeric(w5, "predict_up_mid")
    pred_first_10, pred_last_10 = _first_last_numeric(w10, "predict_up_mid")
    pred_change_5 = (
        pred_last_5[1] - pred_first_5[1]
        if pred_first_5 is not None and pred_last_5 is not None
        else None
    )
    pred_change_10 = (
        pred_last_10[1] - pred_first_10[1]
        if pred_first_10 is not None and pred_last_10 is not None
        else None
    )

    crosses10 = _cross_timestamps(w10)
    crosses30 = _cross_timestamps(w30)
    now = int(current["sampled_at_ms"])
    if last_cross_ms is None:
        recent_crosses = _cross_timestamps(w30)
        last_cross_ms = recent_crosses[-1] if recent_crosses else None

    spot_price = _number(current.get("spot_price"))
    futures_price = _number(current.get("futures_price"))
    chainlink_price = _number(current.get("chainlink_price"))
    spot_r1 = _number(current.get("spot_return_1s_bps"))
    spot_r3 = _number(current.get("spot_return_3s_bps"))
    fut_r1 = _number(current.get("futures_return_1s_bps"))
    fut_r3 = _number(current.get("futures_return_3s_bps"))
    spot_r5 = _number(current.get("spot_return_5s_bps"))
    fut_r5 = _number(current.get("futures_return_5s_bps"))

    return {
        "abs_spot_minus_strike_bps": abs(spot_distance) if spot_distance is not None else None,
        "spot_near_strike_005_share_10s": _near_strike_share(w10, 0.05),
        "spot_near_strike_005_share_30s": _near_strike_share(w30, 0.05),
        "spot_near_strike_010_share_10s": _near_strike_share(w10, 0.10),
        "spot_near_strike_010_share_30s": _near_strike_share(w30, 0.10),
        "strike_cross_count_10s": len(crosses10),
        "strike_cross_count_30s": len(crosses30),
        "ms_since_last_strike_cross": now - last_cross_ms if last_cross_ms is not None else None,
        "spot_realized_vol_3s_bps": _realized_vol_bps(w3, "spot_price"),
        "spot_realized_vol_10s_bps": _realized_vol_bps(w10, "spot_price"),
        "spot_realized_vol_30s_bps": _realized_vol_bps(w30, "spot_price"),
        "futures_realized_vol_3s_bps": _realized_vol_bps(w3, "futures_price"),
        "futures_realized_vol_10s_bps": _realized_vol_bps(w10, "futures_price"),
        "futures_realized_vol_30s_bps": _realized_vol_bps(w30, "futures_price"),
        "predict_up_mid_ema_5s": _ema(w5, "predict_up_mid", 5),
        "predict_up_mid_ema_15s": _ema(w15, "predict_up_mid", 15),
        "predict_up_mid_ema_30s": _ema(w30, "predict_up_mid", 30),
        "predict_up_mid_slope_5s_per_s": _slope_per_second(w5, "predict_up_mid"),
        "predict_up_mid_slope_15s_per_s": _slope_per_second(w15, "predict_up_mid"),
        "predict_bias_persistence_10s": _bias_persistence(w10),
        "predict_bias_persistence_30s": _bias_persistence(w30),
        "predict_bias_edge_abs": abs(up_mid - 0.5) if up_mid is not None else None,
        "predict_up_mid_change_5s": pred_change_5,
        "predict_up_mid_change_10s": pred_change_10,
        "spot_return_window_5s_bps": spot5,
        "spot_return_window_10s_bps": spot10,
        "futures_return_window_5s_bps": fut5,
        "futures_return_window_10s_bps": fut10,
        "predict_vs_spot_direction_5s": _direction_match(pred_change_5, spot5),
        "predict_vs_futures_direction_5s": _direction_match(pred_change_5, fut5),
        "futures_minus_spot_return_1s_bps": (
            fut_r1 - spot_r1 if fut_r1 is not None and spot_r1 is not None else None
        ),
        "futures_minus_spot_return_3s_bps": (
            fut_r3 - spot_r3 if fut_r3 is not None and spot_r3 is not None else None
        ),
        "futures_minus_spot_return_5s_bps": (
            fut_r5 - spot_r5 if fut_r5 is not None and spot_r5 is not None else None
        ),
        "futures_minus_spot_price_bps": _bps(futures_price, spot_price),
        "chainlink_minus_spot_bps": _bps(chainlink_price, spot_price),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in OUTPUT_COLUMNS})
    temp.replace(resolved)


def build_target_taker_direct_eligibility_special_regime_dataset(
    *,
    shadow_db_path: Path = DEFAULT_SHADOW_DB,
    signal_db_path: Path = DEFAULT_SIGNAL_DB,
    output_path: Path = DEFAULT_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
) -> dict[str, Any]:
    shadow = _connect_readonly(shadow_db_path)
    signal = _connect_readonly(signal_db_path)
    try:
        for table in ("wallet_target_taker_mirror_meta", "wallet_target_taker_mirror_parents"):
            if not _has_table(shadow, table):
                raise RuntimeError(f"required target Taker mirror table missing: {table}")
        if not _has_table(signal, "wallet_taker_signal_snapshots"):
            raise RuntimeError("wallet_taker_signal_snapshots table is missing")

        mirror_deployed, excluded_market_id = _target_mirror_coverage(shadow)
        signal_deployed = _signal_deployed_at_ms(signal)
        coverage_start_ms = max(mirror_deployed, signal_deployed)

        event_buckets_by_market: dict[int, list[int]] = {}
        parent_count = 0
        for row in shadow.execute(
            """SELECT market_id,target_event_ms
                 FROM wallet_target_taker_mirror_parents
                WHERE cohort=? AND target_event_ms>=?
                ORDER BY target_event_ms""",
            (TAKER_COHORT, int(coverage_start_ms)),
        ):
            market_id = int(row["market_id"])
            if excluded_market_id is not None and market_id == excluded_market_id:
                continue
            event_ms = int(row["target_event_ms"])
            bucket = (event_ms // 1000) * 1000
            event_buckets_by_market.setdefault(market_id, []).append(bucket)
            parent_count += 1
        for market_id, values in list(event_buckets_by_market.items()):
            event_buckets_by_market[market_id] = sorted(set(values))

        covered_markets = [
            int(row[0])
            for row in signal.execute(
                """SELECT DISTINCT market_id
                     FROM wallet_taker_signal_snapshots
                    WHERE sampled_at_ms>=? AND market_id IS NOT NULL
                    ORDER BY market_id""",
                (int(coverage_start_ms),),
            )
            if excluded_market_id is None or int(row[0]) != excluded_market_id
        ]

        output_rows: list[dict[str, Any]] = []
        zero_taker_markets = 0
        for market_id in covered_markets:
            events = event_buckets_by_market.get(market_id, [])
            if not events:
                zero_taker_markets += 1
            snapshots = _latest_snapshot_per_second(
                signal, market_id, coverage_start_ms=coverage_start_ms
            )
            previous_nonzero_sign = 0
            last_cross_ms: int | None = None
            for index, snapshot in enumerate(snapshots):
                sampled = int(snapshot["sampled_at_ms"])
                current_sign = _sign(_number(snapshot.get("spot_minus_strike_bps")))
                if current_sign:
                    if previous_nonzero_sign and current_sign != previous_nonzero_sign:
                        last_cross_ms = sampled
                    previous_nonzero_sign = current_sign
                decision_bucket = (sampled // 1000) * 1000

                # target_event_ms is second-quantized. Never label an event in the
                # same second as this decision row. The first legal event bucket
                # must be strictly later than decision_bucket.
                event_index = bisect.bisect_right(events, decision_bucket)
                next_bucket = events[event_index] if event_index < len(events) else None
                delta = next_bucket - decision_bucket if next_bucket is not None else None

                row: dict[str, Any] = {
                    "dataset_version": DATASET_VERSION,
                    "market_id": market_id,
                    "decision_sampled_at_ms": sampled,
                    "decision_bucket_start_ms": decision_bucket,
                    "next_target_taker_event_bucket_ms": next_bucket,
                    "ms_to_next_target_taker_bucket": delta,
                }
                row.update(_public_features(snapshot, event_bucket_start_ms=sampled))
                row["signal_age_ms"] = 0
                row.update(_special_features(snapshots, index, last_cross_ms=last_cross_ms))
                for horizon in (1, 2, 5):
                    row[f"label_next_target_taker_any_{horizon}s"] = int(
                        delta is not None and 0 < delta <= horizon * 1000
                    )
                output_rows.append(row)

        _write_csv(output_path, output_rows)

        meta = {
            "datasetVersion": DATASET_VERSION,
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "coverageStartMs": coverage_start_ms,
            "mirrorDeployedAtMs": mirror_deployed,
            "signalDeployedAtMs": signal_deployed,
            "excludedDeploymentMarketId": excluded_market_id,
            "rows": len(output_rows),
            "markets": len(covered_markets),
            "targetTakerParents": parent_count,
            "zeroTakerMarkets": zero_taker_markets,
            "positiveRates": {
                f"next{horizon}s": (
                    sum(
                        int(row[f"label_next_target_taker_any_{horizon}s"])
                        for row in output_rows
                    )
                    / len(output_rows)
                    if output_rows
                    else None
                )
                for horizon in (1, 2, 5)
            },
            "sampling": (
                "Latest wallet_taker_signal_snapshots row per wall-clock second across every "
                "signal-covered market after joint mirror/signal deployment, including markets "
                "with zero Target Taker parents."
            ),
            "timestampBoundary": (
                "Target Taker event timestamps are second-quantized. Same-second target events "
                "are excluded from positive labels. A positive requires the next target event "
                "bucket to be strictly after decision_bucket_start_ms."
            ),
            "featureFamilies": {
                "frozen16": FROZEN16_FEATURES,
                "specialRegime": SPECIAL_REGIME_FEATURES,
            },
        }
        resolved_meta = meta_output_path.expanduser().resolve()
        resolved_meta.parent.mkdir(parents=True, exist_ok=True)
        temp_meta = resolved_meta.with_suffix(resolved_meta.suffix + ".tmp")
        temp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_meta.replace(resolved_meta)
        return meta
    finally:
        shadow.close()
        signal.close()
