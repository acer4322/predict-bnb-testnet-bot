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
DEFAULT_ETH_TARGET_DB = ROOT / "data" / "wallet_maker_book_inference_eth5m.db"
DEFAULT_PREDICT_DB = ROOT / "data" / "predict_fun_observer.db"
DEFAULT_ETH_SIGNAL_DB = ROOT / "data" / "wallet_eth_taker_signals.db"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_eth_taker_behavior_v1.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_eth_taker_behavior_v1.meta.json"
DATASET_VERSION = "TARGET_ETH_TAKER_BEHAVIOR_V1_STRICT_PRE_EVENT"
ETH_COHORT = "TARGET_MAKER_BOOK_INFERENCE_ETH_5M_V1"
MAX_SIGNAL_AGE_MS = 2_500
TRAJECTORY_HORIZONS_MS = (1_000, 3_000, 5_000)

PREDICT_FEATURES = [
    "seconds_left",
    "predict_up_bid",
    "predict_up_ask",
    "predict_up_mid",
    "predict_down_bid",
    "predict_down_ask",
    "predict_down_mid",
    "predict_up_spread",
    "predict_down_spread",
    "predict_up_mid_edge",
    "predict_mid_sum",
    "signal_age_ms",
]
for horizon in TRAJECTORY_HORIZONS_MS:
    seconds = horizon // 1000
    PREDICT_FEATURES.extend(
        [
            f"predict_up_mid_delta_{seconds}s",
            f"predict_up_spread_delta_{seconds}s",
        ]
    )

MICRO_FEATURES = [
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
    "direction_score",
    "abs_direction_score",
]
SIDE_MODEL_FEATURES = list(dict.fromkeys(PREDICT_FEATURES + MICRO_FEATURES))

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
    "signal_source",
    "parent_id",
    "market_id",
    "order_hash",
    "side",
    "target_event_ms",
    "event_bucket_start_ms",
    "target_last_event_ms",
    "target_first_observed_ms",
    "signal_sampled_at_ms",
]
OUTCOME_COLUMNS = [
    "label_side_up",
    "target_average_price",
    "target_latest_shares",
    "target_fill_legs",
    "target_notional_estimate",
    "target_log1p_latest_shares",
    "chosen_price_slippage_vs_pre_ask",
    "prediction_side",
    "label_matches_prediction_side",
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
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "p25": None, "median": None, "p75": None, "p90": None}
    ordered = sorted(values)

    def pick(q: float) -> float:
        return float(ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))])

    return {
        "p10": pick(0.10),
        "p25": pick(0.25),
        "median": float(statistics.median(ordered)),
        "p75": pick(0.75),
        "p90": pick(0.90),
    }


def _target_meta(db: sqlite3.Connection) -> tuple[int, int | None]:
    if not _has_table(db, "maker_book_inference_meta"):
        raise RuntimeError("8779 maker_book_inference_meta is missing")
    row = db.execute(
        "SELECT deployed_at_ms,excluded_market_id FROM maker_book_inference_meta WHERE cohort=?",
        (ETH_COHORT,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing ETH collector meta for {ETH_COHORT}")
    return int(row["deployed_at_ms"]), int(row["excluded_market_id"]) if row["excluded_market_id"] is not None else None


def _activity_summary(db: sqlite3.Connection, deployed_at_ms: int, excluded_market_id: int | None) -> dict[str, Any]:
    where = "event_ms>=?"
    args: list[Any] = [int(deployed_at_ms)]
    if excluded_market_id is not None:
        where += " AND market_id<>?"
        args.append(int(excluded_market_id))
    rows = db.execute(
        f"""SELECT role,quote_type,COUNT(*) legs,COUNT(DISTINCT market_id) markets,
                   COALESCE(SUM(shares),0) shares
              FROM maker_book_inference_wallet_events
             WHERE {where}
             GROUP BY role,quote_type""",
        args,
    ).fetchall()
    output = {f"{row['role']}_{row['quote_type']}": {"legs": int(row["legs"]), "markets": int(row["markets"]), "shares": float(row["shares"])} for row in rows}
    maker_bid = int(output.get("MAKER_BID", {}).get("legs", 0))
    taker_bid = int(output.get("TAKER_BID", {}).get("legs", 0))
    output["makerBidLegShareOfBuyActivity"] = maker_bid / (maker_bid + taker_bid) if maker_bid + taker_bid else None
    return output


def _target_parents(db: sqlite3.Connection, deployed_at_ms: int, excluded_market_id: int | None) -> list[dict[str, Any]]:
    where = "role='TAKER' AND quote_type='BID' AND side IN ('UP','DOWN') AND event_ms>=?"
    args: list[Any] = [int(deployed_at_ms)]
    if excluded_market_id is not None:
        where += " AND market_id<>?"
        args.append(int(excluded_market_id))
    rows = db.execute(
        f"""SELECT COALESCE(NULLIF(order_hash,''),source_leg_id) parent_id,
                   market_id,MAX(NULLIF(order_hash,'')) order_hash,side,
                   MIN(event_ms) target_event_ms,MAX(event_ms) target_last_event_ms,
                   MIN(observed_at_ms) target_first_observed_ms,
                   SUM(shares) target_latest_shares,COUNT(*) target_fill_legs,
                   CASE WHEN SUM(shares)>0 THEN SUM(price*shares)/SUM(shares) END target_average_price
              FROM maker_book_inference_wallet_events
             WHERE {where}
             GROUP BY market_id,COALESCE(NULLIF(order_hash,''),source_leg_id),side
             ORDER BY target_event_ms,parent_id""",
        args,
    ).fetchall()
    return [dict(row) for row in rows]


def _load_predict_market(db: sqlite3.Connection, market_id: int, start_ms: int, end_ms: int) -> tuple[list[int], list[dict[str, Any]]]:
    rows = [
        dict(row)
        for row in db.execute(
            """SELECT sampled_at_ms,seconds_left,up_mid,down_mid,up_bid,up_ask,down_bid,down_ask,
                      source_age_ms,receipt_age_ms
                 FROM predict_fun_trajectory
                WHERE asset='ETH' AND market_id=? AND sampled_at_ms BETWEEN ? AND ?
                ORDER BY sampled_at_ms""",
            (int(market_id), int(start_ms), int(end_ms)),
        )
    ]
    return [int(row["sampled_at_ms"]) for row in rows], rows


def _asof(times: list[int], rows: list[dict[str, Any]], at_ms: int, max_age_ms: int) -> tuple[dict[str, Any] | None, int | None]:
    index = bisect.bisect_left(times, int(at_ms)) - 1
    if index < 0:
        return None, None
    age = int(at_ms) - int(times[index])
    if age <= 0 or age > int(max_age_ms):
        return None, age
    return rows[index], age


def _prior_predict_value(times: list[int], rows: list[dict[str, Any]], sampled_ms: int, horizon_ms: int, field: str) -> float | None:
    index = bisect.bisect_right(times, int(sampled_ms - horizon_ms)) - 1
    if index < 0:
        return None
    return _number(rows[index].get(field))


def _predict_features(snapshot: dict[str, Any], *, times: list[int], rows: list[dict[str, Any]], event_bucket_start_ms: int) -> dict[str, Any]:
    up_bid = _number(snapshot.get("up_bid"))
    up_ask = _number(snapshot.get("up_ask"))
    up_mid = _number(snapshot.get("up_mid"))
    down_bid = _number(snapshot.get("down_bid"))
    down_ask = _number(snapshot.get("down_ask"))
    down_mid = _number(snapshot.get("down_mid"))
    sampled = int(snapshot.get("sampled_at_ms") or 0)
    up_spread = _spread(up_bid, up_ask)
    result: dict[str, Any] = {
        "seconds_left": snapshot.get("seconds_left"),
        "predict_up_bid": up_bid,
        "predict_up_ask": up_ask,
        "predict_up_mid": up_mid,
        "predict_down_bid": down_bid,
        "predict_down_ask": down_ask,
        "predict_down_mid": down_mid,
        "predict_up_spread": up_spread,
        "predict_down_spread": _spread(down_bid, down_ask),
        "predict_up_mid_edge": up_mid - 0.5 if up_mid is not None else None,
        "predict_mid_sum": up_mid + down_mid if up_mid is not None and down_mid is not None else None,
        "signal_age_ms": int(event_bucket_start_ms) - sampled if sampled else None,
    }
    for horizon in TRAJECTORY_HORIZONS_MS:
        seconds = horizon // 1000
        prior_mid = _prior_predict_value(times, rows, sampled, horizon, "up_mid")
        prior_bid = _prior_predict_value(times, rows, sampled, horizon, "up_bid")
        prior_ask = _prior_predict_value(times, rows, sampled, horizon, "up_ask")
        prior_spread = _spread(prior_bid, prior_ask)
        result[f"predict_up_mid_delta_{seconds}s"] = up_mid - prior_mid if up_mid is not None and prior_mid is not None else None
        result[f"predict_up_spread_delta_{seconds}s"] = up_spread - prior_spread if up_spread is not None and prior_spread is not None else None
    return result


def _enriched_features(snapshot: dict[str, Any], event_bucket_start_ms: int) -> dict[str, Any]:
    mapping = {
        "strike_price": "strike_price",
        "spot_price": "spot_price",
        "spot_microprice": "spot_microprice",
        "spot_queue_imbalance": "spot_queue_imbalance",
        "spot_taker_imbalance_250ms": "spot_taker_imbalance_250ms",
        "spot_taker_imbalance_1s": "spot_taker_imbalance_1s",
        "spot_return_250ms_bps": "spot_return_250ms_bps",
        "spot_return_1s_bps": "spot_return_1s_bps",
        "spot_return_3s_bps": "spot_return_3s_bps",
        "spot_return_5s_bps": "spot_return_5s_bps",
        "futures_price": "futures_price",
        "futures_microprice": "futures_microprice",
        "futures_queue_imbalance": "futures_queue_imbalance",
        "futures_taker_imbalance_250ms": "futures_taker_imbalance_250ms",
        "futures_taker_imbalance_1s": "futures_taker_imbalance_1s",
        "futures_return_250ms_bps": "futures_return_250ms_bps",
        "futures_return_1s_bps": "futures_return_1s_bps",
        "futures_return_3s_bps": "futures_return_3s_bps",
        "futures_return_5s_bps": "futures_return_5s_bps",
        "perp_spot_basis_bps": "perp_spot_basis_bps",
        "spot_minus_strike_bps": "spot_minus_strike_bps",
        "direction_score": "direction_score",
    }
    result = {target: snapshot.get(source) for target, source in mapping.items()}
    direction = _number(result.get("direction_score"))
    result["abs_direction_score"] = abs(direction) if direction is not None else None
    sampled = int(snapshot.get("sampled_at_ms") or 0)
    result["signal_age_ms"] = int(event_bucket_start_ms) - sampled if sampled else None
    return result


def _chosen_context(row: dict[str, Any], side: str) -> dict[str, Any]:
    chosen = "up" if side == "UP" else "down"
    opposite = "down" if side == "UP" else "up"
    bid = row.get(f"predict_{chosen}_bid")
    ask = row.get(f"predict_{chosen}_ask")
    return {
        "chosen_predict_bid": bid,
        "chosen_predict_ask": ask,
        "chosen_predict_mid": row.get(f"predict_{chosen}_mid"),
        "chosen_predict_spread": _spread(bid, ask),
        "opposite_predict_mid": row.get(f"predict_{opposite}_mid"),
    }


def _sign_side(value: Any, threshold: float) -> str | None:
    number = _number(value)
    if number is None or abs(number) < threshold:
        return None
    return "UP" if number > 0 else "DOWN"


def build_target_eth_taker_dataset(
    *,
    target_db_path: Path = DEFAULT_ETH_TARGET_DB,
    predict_db_path: Path = DEFAULT_PREDICT_DB,
    eth_signal_db_path: Path = DEFAULT_ETH_SIGNAL_DB,
    signal_source: str = "predict",
    output_path: Path = DEFAULT_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
    max_signal_age_ms: int = MAX_SIGNAL_AGE_MS,
) -> dict[str, Any]:
    source = str(signal_source).strip().lower()
    if source not in {"predict", "enriched"}:
        raise ValueError("signal_source must be predict or enriched")
    target = _connect_readonly(target_db_path)
    predict = _connect_readonly(predict_db_path)
    enriched: sqlite3.Connection | None = None
    try:
        if not _has_table(target, "maker_book_inference_wallet_events"):
            raise RuntimeError("8779 maker_book_inference_wallet_events is missing")
        if not _has_table(predict, "predict_fun_trajectory"):
            raise RuntimeError("8771 predict_fun_trajectory is missing")
        if source == "enriched":
            enriched = _connect_readonly(eth_signal_db_path)
            if not _has_table(enriched, "wallet_taker_signal_snapshots"):
                raise RuntimeError("8780 ETH wallet_taker_signal_snapshots is missing")

        deployed_at_ms, excluded_market_id = _target_meta(target)
        parents = _target_parents(target, deployed_at_ms, excluded_market_id)
        activity = _activity_summary(target, deployed_at_ms, excluded_market_id)

        grouped_parents: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for parent in parents:
            grouped_parents[int(parent["market_id"])].append(parent)

        output_rows: list[dict[str, Any]] = []
        missing_snapshot = 0
        for market_id, market_parents in grouped_parents.items():
            min_ms = min((int(row["target_event_ms"]) // 1000) * 1000 for row in market_parents) - 8_000
            max_ms = max((int(row["target_last_event_ms"]) // 1000) * 1000 for row in market_parents) + 1_000
            times, predict_rows = _load_predict_market(predict, market_id, min_ms, max_ms)
            for parent in market_parents:
                event_ms = int(parent["target_event_ms"])
                bucket_start = (event_ms // 1000) * 1000
                predict_snapshot, predict_age = _asof(times, predict_rows, bucket_start, max_signal_age_ms)
                if predict_snapshot is None:
                    missing_snapshot += 1
                    continue
                row: dict[str, Any] = {
                    "dataset_version": DATASET_VERSION,
                    "signal_source": source.upper(),
                    "parent_id": parent["parent_id"],
                    "market_id": market_id,
                    "order_hash": parent.get("order_hash"),
                    "side": parent["side"],
                    "target_event_ms": event_ms,
                    "event_bucket_start_ms": bucket_start,
                    "target_last_event_ms": int(parent["target_last_event_ms"]),
                    "target_first_observed_ms": int(parent["target_first_observed_ms"]),
                    "signal_sampled_at_ms": int(predict_snapshot["sampled_at_ms"]),
                    "label_side_up": int(parent["side"] == "UP"),
                    "target_average_price": parent.get("target_average_price"),
                    "target_latest_shares": parent.get("target_latest_shares"),
                    "target_fill_legs": parent.get("target_fill_legs"),
                }
                row.update(_predict_features(predict_snapshot, times=times, rows=predict_rows, event_bucket_start_ms=bucket_start))

                if source == "enriched" and enriched is not None:
                    cutoff_ns = bucket_start * 1_000_000
                    floor_ns = (bucket_start - max_signal_age_ms) * 1_000_000
                    snap = enriched.execute(
                        """SELECT * FROM wallet_taker_signal_snapshots
                             WHERE market_id=? AND timestamp_ns>=? AND timestamp_ns<?
                             ORDER BY timestamp_ns DESC LIMIT 1""",
                        (market_id, floor_ns, cutoff_ns),
                    ).fetchone()
                    if snap is None:
                        missing_snapshot += 1
                        continue
                    enriched_row = dict(snap)
                    row.update(_enriched_features(enriched_row, bucket_start))
                    row["signal_sampled_at_ms"] = int(enriched_row["sampled_at_ms"])
                else:
                    for feature in MICRO_FEATURES:
                        row.setdefault(feature, None)
                    row["signal_age_ms"] = predict_age

                row.update(_chosen_context(row, str(parent["side"])))
                shares = _number(parent.get("target_latest_shares"))
                price = _number(parent.get("target_average_price"))
                chosen_ask = _number(row.get("chosen_predict_ask"))
                row["target_notional_estimate"] = shares * price if shares is not None and price is not None else None
                row["target_log1p_latest_shares"] = math.log1p(max(0.0, shares)) if shares is not None else None
                row["chosen_price_slippage_vs_pre_ask"] = price - chosen_ask if price is not None and chosen_ask is not None else None
                prediction_side = "UP" if (_number(row.get("predict_up_mid")) or 0.5) > 0.5 else "DOWN" if (_number(row.get("predict_up_mid")) or 0.5) < 0.5 else None
                row["prediction_side"] = prediction_side
                row["label_matches_prediction_side"] = int(str(parent["side"]) == prediction_side) if prediction_side else None
                public_side = _sign_side(row.get("direction_score"), 0.05)
                row["public_direction_side"] = public_side
                row["label_matches_public_direction"] = int(str(parent["side"]) == public_side) if public_side else None
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

        up = sum(int(row["label_side_up"]) for row in output_rows)
        leads = [float(row["signal_age_ms"]) for row in output_rows if _number(row.get("signal_age_ms")) is not None]
        shares = [float(row["target_latest_shares"]) for row in output_rows if _number(row.get("target_latest_shares")) is not None]
        prices = [float(row["target_average_price"]) for row in output_rows if _number(row.get("target_average_price")) is not None]
        prediction_known = [row for row in output_rows if row.get("label_matches_prediction_side") is not None]
        public_known = [row for row in output_rows if row.get("label_matches_public_direction") is not None]
        report = {
            "datasetVersion": DATASET_VERSION,
            "asset": "ETH",
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "signalSource": source.upper(),
            "rows": len(output_rows),
            "markets": len({int(row["market_id"]) for row in output_rows}),
            "eligibleTargetTakerParents": len(parents),
            "matchedStrictPreEventParents": len(output_rows),
            "pairingRate": len(output_rows) / len(parents) if parents else None,
            "missingStrictPreSnapshot": missing_snapshot,
            "targetCollectorDeployedAtMs": deployed_at_ms,
            "excludedDeploymentMarketId": excluded_market_id,
            "ethTargetActivity": activity,
            "sideRates": {
                "up": up / len(output_rows) if output_rows else None,
                "down": (len(output_rows) - up) / len(output_rows) if output_rows else None,
            },
            "predictionMidDiagnostic": {
                "knownRows": len(prediction_known),
                "matchRate": sum(int(row["label_matches_prediction_side"]) for row in prediction_known) / len(prediction_known) if prediction_known else None,
            },
            "publicDirectionDiagnostic": {
                "knownRows": len(public_known),
                "matchRate": sum(int(row["label_matches_public_direction"]) for row in public_known) / len(public_known) if public_known else None,
                "availableOnlyForEnrichedSource": True,
            },
            "snapshotLeadMs": _quantiles(leads),
            "targetLatestShares": _quantiles(shares),
            "targetAveragePrice": _quantiles(prices),
            "timestampBoundary": (
                "Target ETH Taker event_ms is treated as second-quantized. The entire event second is forbidden; "
                "features must come from a snapshot strictly before eventBucketStartMs."
            ),
            "sideLeakageBoundary": (
                "SIDE_MODEL_FEATURES contain no target-side-aligned or chosen-side features. Chosen-side price context "
                "is legal only after conditioning on the observed Taker side for size/execution research."
            ),
            "sources": {
                "targetEthDb": str(target_db_path),
                "predictObserverDb": str(predict_db_path),
                "ethEnrichedSignalDb": str(eth_signal_db_path) if source == "enriched" else None,
            },
            "output": str(output_path),
        }
        meta_output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_meta = meta_output_path.with_suffix(meta_output_path.suffix + ".tmp")
        tmp_meta.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_meta.replace(meta_output_path)
        return report
    finally:
        target.close()
        predict.close()
        if enriched is not None:
            enriched.close()
