from __future__ import annotations

import csv
import json
import math
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from .target_maker_ebm_dataset import DEFAULT_SIGNAL_DB
from .target_maker_taker_link import DEFAULT_SHADOW_DB, TAKER_COHORT

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_taker_behavior_v1.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_taker_behavior_v1.meta.json"
DATASET_VERSION = "TARGET_TAKER_BEHAVIOR_V1_STRICT_PRE_EVENT_SECOND_BUCKET"
MAX_SIGNAL_AGE_MS = 2_000

RAW_PUBLIC_FEATURES = [
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
    "chainlink_price",
    "chainlink_minus_strike_bps",
    "spot_minus_chainlink_bps",
    "direction_score",
]
DERIVED_PUBLIC_FEATURES = [
    "predict_up_spread",
    "predict_down_spread",
    "predict_mid_sum",
    "predict_up_mid_edge",
    "abs_direction_score",
    "signal_age_ms",
]
SIDE_MODEL_FEATURES = RAW_PUBLIC_FEATURES + DERIVED_PUBLIC_FEATURES

# These are only legal after conditioning on the actual Taker side. They are used
# for sizing/execution research, never for predicting BUY_UP vs BUY_DOWN.
CHOSEN_SIDE_CONTEXT = [
    "chosen_predict_bid",
    "chosen_predict_ask",
    "chosen_predict_mid",
    "chosen_predict_spread",
    "opposite_predict_mid",
]
SIZE_MODEL_FEATURES = SIDE_MODEL_FEATURES + ["label_side_up"] + CHOSEN_SIDE_CONTEXT

METADATA_COLUMNS = [
    "dataset_version",
    "parent_id",
    "market_id",
    "order_hash",
    "side",
    "target_event_ms",
    "event_bucket_start_ms",
    "target_last_event_ms",
    "detected_at_ms",
    "detection_lag_ms",
    "signal_sampled_at_ms",
]
OUTCOME_COLUMNS = [
    "label_side_up",
    "target_average_price",
    "target_shares_at_detection",
    "target_latest_shares",
    "target_fill_legs",
    "target_notional_estimate",
    "target_log1p_latest_shares",
    "chosen_price_slippage_vs_pre_ask",
    "public_direction_side",
    "label_matches_public_direction",
]
OUTPUT_COLUMNS = METADATA_COLUMNS + SIDE_MODEL_FEATURES + CHOSEN_SIDE_CONTEXT + OUTCOME_COLUMNS


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


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


def _strict_pre_snapshot(
    db: sqlite3.Connection,
    *,
    market_id: int,
    event_bucket_start_ms: int,
    max_signal_age_ms: int,
) -> dict[str, Any] | None:
    # target_event_ms is second-quantized. The whole event second is forbidden.
    # Use the last snapshot strictly before that second starts.
    cutoff_ns = int(event_bucket_start_ms) * 1_000_000
    floor_ns = int(event_bucket_start_ms - max_signal_age_ms) * 1_000_000
    row = db.execute(
        """SELECT * FROM wallet_taker_signal_snapshots
             WHERE market_id=? AND timestamp_ns>=? AND timestamp_ns<?
             ORDER BY timestamp_ns DESC LIMIT 1""",
        (int(market_id), int(floor_ns), int(cutoff_ns)),
    ).fetchone()
    return dict(row) if row is not None else None


def _public_features(snapshot: dict[str, Any], *, event_bucket_start_ms: int) -> dict[str, Any]:
    result = {column: snapshot.get(column) for column in RAW_PUBLIC_FEATURES}
    up_mid = _number(snapshot.get("predict_up_mid"))
    down_mid = _number(snapshot.get("predict_down_mid"))
    direction = _number(snapshot.get("direction_score"))
    sampled = int(snapshot.get("sampled_at_ms") or 0)
    result.update(
        {
            "predict_up_spread": _spread(snapshot.get("predict_up_bid"), snapshot.get("predict_up_ask")),
            "predict_down_spread": _spread(snapshot.get("predict_down_bid"), snapshot.get("predict_down_ask")),
            "predict_mid_sum": (up_mid + down_mid) if up_mid is not None and down_mid is not None else None,
            "predict_up_mid_edge": up_mid - 0.5 if up_mid is not None else None,
            "abs_direction_score": abs(direction) if direction is not None else None,
            "signal_age_ms": int(event_bucket_start_ms) - sampled if sampled else None,
        }
    )
    return result


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


def _direction_side(direction_score: Any, *, threshold: float = 0.05) -> str | None:
    value = _number(direction_score)
    if value is None or abs(value) < threshold:
        return None
    return "UP" if value > 0 else "DOWN"


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "p25": None, "median": None, "p75": None, "p90": None}
    ordered = sorted(values)
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


def build_target_taker_behavior_dataset(
    *,
    shadow_db_path: Path = DEFAULT_SHADOW_DB,
    signal_db_path: Path = DEFAULT_SIGNAL_DB,
    output_path: Path = DEFAULT_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
    max_signal_age_ms: int = MAX_SIGNAL_AGE_MS,
) -> dict[str, Any]:
    shadow = _connect_readonly(shadow_db_path)
    signal = _connect_readonly(signal_db_path)
    try:
        for table in ("wallet_target_taker_mirror_meta", "wallet_target_taker_mirror_parents"):
            if not _has_table(shadow, table):
                raise RuntimeError(f"required target Taker mirror table missing: {table}")
        if not _has_table(signal, "wallet_taker_signal_snapshots"):
            raise RuntimeError("8777 wallet_taker_signal_snapshots table is missing")

        meta = shadow.execute(
            "SELECT deployed_at_ms,excluded_market_id FROM wallet_target_taker_mirror_meta WHERE cohort=?",
            (TAKER_COHORT,),
        ).fetchone()
        if meta is None:
            raise RuntimeError(f"missing target Taker mirror meta for cohort {TAKER_COHORT}")
        mirror_deployed = int(meta["deployed_at_ms"])
        signal_deployed = _signal_deployed_at_ms(signal)
        coverage_start_ms = max(mirror_deployed, signal_deployed)
        excluded_market_id = int(meta["excluded_market_id"]) if meta["excluded_market_id"] is not None else None

        parents = [
            dict(row)
            for row in shadow.execute(
                """SELECT parent_id,market_id,order_hash,side,target_event_ms,target_last_event_ms,
                          detected_at_ms,detection_lag_ms,target_average_price,
                          target_shares_at_detection,target_latest_shares,target_fill_legs
                     FROM wallet_target_taker_mirror_parents
                    WHERE cohort=? AND target_event_ms>=?
                    ORDER BY target_event_ms,parent_id""",
                (TAKER_COHORT, coverage_start_ms),
            )
            if excluded_market_id is None or int(row["market_id"]) != excluded_market_id
        ]

        output_rows: list[dict[str, Any]] = []
        missing_snapshot = 0
        for parent in parents:
            event_ms = int(parent["target_event_ms"])
            bucket_start = (event_ms // 1000) * 1000
            snapshot = _strict_pre_snapshot(
                signal,
                market_id=int(parent["market_id"]),
                event_bucket_start_ms=bucket_start,
                max_signal_age_ms=max(250, int(max_signal_age_ms)),
            )
            if snapshot is None:
                missing_snapshot += 1
                continue
            side = str(parent["side"])
            if side not in {"UP", "DOWN"}:
                continue
            row: dict[str, Any] = {
                "dataset_version": DATASET_VERSION,
                "parent_id": parent["parent_id"],
                "market_id": int(parent["market_id"]),
                "order_hash": parent.get("order_hash"),
                "side": side,
                "target_event_ms": event_ms,
                "event_bucket_start_ms": bucket_start,
                "target_last_event_ms": int(parent["target_last_event_ms"]),
                "detected_at_ms": int(parent["detected_at_ms"]),
                "detection_lag_ms": int(parent["detection_lag_ms"]),
                "signal_sampled_at_ms": int(snapshot["sampled_at_ms"]),
                "label_side_up": int(side == "UP"),
                "target_average_price": parent.get("target_average_price"),
                "target_shares_at_detection": parent.get("target_shares_at_detection"),
                "target_latest_shares": parent.get("target_latest_shares"),
                "target_fill_legs": parent.get("target_fill_legs"),
            }
            row.update(_public_features(snapshot, event_bucket_start_ms=bucket_start))
            row.update(_chosen_side_context(snapshot, side))
            latest_shares = _number(parent.get("target_latest_shares"))
            average_price = _number(parent.get("target_average_price"))
            chosen_ask = _number(row.get("chosen_predict_ask"))
            row["target_notional_estimate"] = (
                latest_shares * average_price
                if latest_shares is not None and average_price is not None
                else None
            )
            row["target_log1p_latest_shares"] = (
                math.log1p(max(0.0, latest_shares)) if latest_shares is not None else None
            )
            row["chosen_price_slippage_vs_pre_ask"] = (
                average_price - chosen_ask
                if average_price is not None and chosen_ask is not None
                else None
            )
            public_side = _direction_side(snapshot.get("direction_score"))
            row["public_direction_side"] = public_side
            row["label_matches_public_direction"] = (
                int(side == public_side) if public_side is not None else None
            )
            output_rows.append(row)

        output_path = output_path.expanduser().resolve()
        meta_output_path = meta_output_path.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp = output_path.with_suffix(output_path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in output_rows:
                writer.writerow({column: row.get(column) for column in OUTPUT_COLUMNS})
        temp.replace(output_path)

        leads = [float(row["signal_age_ms"]) for row in output_rows if _number(row.get("signal_age_ms")) is not None]
        shares = [float(row["target_latest_shares"]) for row in output_rows if _number(row.get("target_latest_shares")) is not None]
        prices = [float(row["target_average_price"]) for row in output_rows if _number(row.get("target_average_price")) is not None]
        up_count = sum(int(row["label_side_up"]) for row in output_rows)
        direction_known = [row for row in output_rows if row.get("label_matches_public_direction") is not None]
        report = {
            "datasetVersion": DATASET_VERSION,
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "rows": len(output_rows),
            "markets": len({int(row["market_id"]) for row in output_rows}),
            "eligibleTargetTakerParents": len(parents),
            "matchedStrictPreEventParents": len(output_rows),
            "pairingRate": len(output_rows) / len(parents) if parents else None,
            "missingStrictPreSnapshot": missing_snapshot,
            "coverageStartMs": coverage_start_ms,
            "mirrorDeployedAtMs": mirror_deployed,
            "signalDeployedAtMs": signal_deployed,
            "excludedDeploymentMarketId": excluded_market_id,
            "sideRates": {
                "up": up_count / len(output_rows) if output_rows else None,
                "down": (len(output_rows) - up_count) / len(output_rows) if output_rows else None,
            },
            "publicDirectionDiagnostic": {
                "knownRows": len(direction_known),
                "matchRate": (
                    sum(int(row["label_matches_public_direction"]) for row in direction_known) / len(direction_known)
                    if direction_known else None
                ),
                "thresholdAbsDirectionScore": 0.05,
            },
            "snapshotLeadMs": _quantiles(leads),
            "targetLatestShares": _quantiles(shares),
            "targetAveragePrice": _quantiles(prices),
            "timestampBoundary": (
                "target_event_ms is second-quantized. Side/size features use the latest 8777 same-market snapshot "
                "strictly before floor(target_event_ms/1000)*1000; the entire reported event second is forbidden."
            ),
            "sideLeakageBoundary": (
                "BUY_UP/BUY_DOWN models may use only SIDE_MODEL_FEATURES. chosen_* quote fields and label_side_up "
                "are conditional-on-side context and are reserved for size/execution research."
            ),
            "sizeBoundary": (
                "target_latest_shares is the mirror's latest observed parent total and may include post-detection parent accumulation; "
                "target_shares_at_detection is retained separately for observer-artifact diagnostics."
            ),
            "sideModelFeatures": SIDE_MODEL_FEATURES,
            "sizeModelFeatures": SIZE_MODEL_FEATURES,
            "output": str(output_path),
            "sources": {"shadowDb": str(shadow_db_path), "signalDb": str(signal_db_path)},
        }
        meta_temp = meta_output_path.with_suffix(meta_output_path.suffix + ".tmp")
        meta_temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_temp.replace(meta_output_path)
        return report
    finally:
        shadow.close()
        signal.close()
