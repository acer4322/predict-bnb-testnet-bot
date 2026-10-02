from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WALLET_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_SIGNAL_DB = ROOT / "data" / "wallet_taker_signals.db"
DEFAULT_ARCHIVE_DB = ROOT / "data" / "wallet_taker_private_archive.db"
DEFAULT_DATASET_DB = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_private_research.db"
DEFAULT_MODEL = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_private_model_v1.json"
DEFAULT_REPORT = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_private_reconstruction_report.json"

FEATURE_NAMES = (
    "seconds_left_norm", "predict_up_mid_centered", "predict_extremity", "predict_spread",
    "predict_mid_delta_1s", "predict_mid_delta_3s", "predict_mid_delta_5s",
    "abs_predict_mid_delta_1s", "abs_predict_mid_delta_3s", "abs_predict_mid_delta_5s",
    "spot_queue_imbalance", "spot_taker_imbalance_250ms", "spot_taker_imbalance_1s",
    "spot_return_250ms_bps", "spot_return_1s_bps", "spot_return_3s_bps", "spot_return_5s_bps",
    "futures_queue_imbalance", "futures_taker_imbalance_250ms", "futures_taker_imbalance_1s",
    "futures_return_250ms_bps", "futures_return_1s_bps", "futures_return_3s_bps", "futures_return_5s_bps",
    "perp_spot_basis_bps", "spot_minus_strike_bps", "chainlink_minus_strike_bps",
    "spot_minus_chainlink_bps", "direction_score", "abs_direction_score",
    "abs_spot_return_250ms_bps", "abs_spot_return_1s_bps",
    "abs_futures_return_250ms_bps", "abs_futures_return_1s_bps",
)

ARCHIVE_KEYS = {
    "timestampNs": "timestamp_ns", "sampledAtMs": "sampled_at_ms", "marketId": "market_id",
    "bucketStartSec": "bucket_start_sec", "windowEndMs": "window_end_ms", "secondsLeft": "seconds_left",
    "strikePrice": "strike_price", "predictUpBid": "predict_up_bid", "predictUpAsk": "predict_up_ask",
    "predictUpMid": "predict_up_mid", "predictDownBid": "predict_down_bid", "predictDownAsk": "predict_down_ask",
    "predictDownMid": "predict_down_mid", "predictSourceAgeMs": "predict_source_age_ms",
    "predictReceiptAgeMs": "predict_receipt_age_ms", "spotPrice": "spot_price", "spotMicroprice": "spot_microprice",
    "spotQueueImbalance": "spot_queue_imbalance", "spotTakerImbalance250ms": "spot_taker_imbalance_250ms",
    "spotTakerImbalance1s": "spot_taker_imbalance_1s", "spotReturn250msBps": "spot_return_250ms_bps",
    "spotReturn1sBps": "spot_return_1s_bps", "spotReturn3sBps": "spot_return_3s_bps",
    "spotReturn5sBps": "spot_return_5s_bps", "futuresPrice": "futures_price",
    "futuresMicroprice": "futures_microprice", "futuresQueueImbalance": "futures_queue_imbalance",
    "futuresTakerImbalance250ms": "futures_taker_imbalance_250ms",
    "futuresTakerImbalance1s": "futures_taker_imbalance_1s", "futuresReturn250msBps": "futures_return_250ms_bps",
    "futuresReturn1sBps": "futures_return_1s_bps", "futuresReturn3sBps": "futures_return_3s_bps",
    "futuresReturn5sBps": "futures_return_5s_bps", "perpSpotBasisBps": "perp_spot_basis_bps",
    "spotMinusStrikeBps": "spot_minus_strike_bps", "chainlinkPrice": "chainlink_price",
    "chainlinkSourceAgeMs": "chainlink_source_age_ms", "chainlinkReceiptAgeMs": "chainlink_receipt_age_ms",
    "chainlinkMinusStrikeBps": "chainlink_minus_strike_bps", "spotMinusChainlinkBps": "spot_minus_chainlink_bps",
    "directionScore": "direction_score", "directionBias": "direction_bias", "volatilityAlert": "volatility_alert",
}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * q)))]


def _target_parents(db: sqlite3.Connection, deployed_at_ms: int) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(
        """SELECT market_id,order_hash,side,MIN(event_ms) event_ms,
                  SUM(shares) shares,SUM(shares*price) notional_usdt,
                  SUM(shares*price)/SUM(shares) average_price,COUNT(*) fill_legs
             FROM wallet_shadow_target_events
            WHERE role='TAKER' AND quote_type='BID' AND event_ms>=?
              AND side IN ('UP','DOWN') AND order_hash IS NOT NULL
            GROUP BY market_id,order_hash,side
            ORDER BY event_ms,order_hash""",
        (deployed_at_ms,),
    )]


def _snapshot_index(
    db: sqlite3.Connection, archive_db_path: Path | None = None
) -> dict[int, tuple[list[int], list[dict[str, Any]]]]:
    grouped: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    if archive_db_path is not None and archive_db_path.exists():
        archive = sqlite3.connect(archive_db_path)
        archive.row_factory = sqlite3.Row
        try:
            for row in archive.execute("SELECT snapshot_json FROM wallet_taker_private_signal_archive ORDER BY timestamp_ns"):
                raw = json.loads(row["snapshot_json"])
                normalized = dict(raw) if "timestamp_ns" in raw else {snake: raw.get(camel) for camel, snake in ARCHIVE_KEYS.items()}
                timestamp_ns = int(normalized.get("timestamp_ns") or 0)
                market_id = int(normalized.get("market_id") or 0)
                if timestamp_ns > 0 and market_id > 0:
                    grouped[market_id][timestamp_ns] = normalized
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            pass
        archive.close()
    for row in db.execute("SELECT * FROM wallet_taker_signal_snapshots ORDER BY market_id,timestamp_ns"):
        grouped[int(row["market_id"])][int(row["timestamp_ns"])] = dict(row)
    result = {}
    for market_id, by_timestamp in grouped.items():
        rows = sorted(by_timestamp.values(), key=lambda row: int(row["timestamp_ns"]))
        result[market_id] = ([int(row["sampled_at_ms"]) for row in rows], rows)
    return result


def _asof_snapshot(
    index: dict[int, tuple[list[int], list[dict[str, Any]]]],
    market_id: int,
    decision_ms: int,
    *,
    guard_ms: int = 250,
    maximum_age_ms: int = 1_250,
) -> dict[str, Any] | None:
    item = index.get(int(market_id))
    if item is None:
        return None
    times, rows = item
    cutoff = int(decision_ms) - guard_ms
    position = bisect.bisect_right(times, cutoff) - 1
    if position < 0 or cutoff - times[position] > maximum_age_ms:
        return None
    return rows[position]


def _previous_mid(index: tuple[list[int], list[dict[str, Any]]], sampled_ms: int, seconds: int) -> float:
    times, rows = index
    position = bisect.bisect_right(times, sampled_ms - seconds * 1_000) - 1
    return _number(rows[position].get("predict_up_mid"), 0.5) if position >= 0 else 0.5


def _features(snapshot: dict[str, Any], market_index: tuple[list[int], list[dict[str, Any]]]) -> list[float]:
    mid = _number(snapshot.get("predict_up_mid"), 0.5)
    sampled = int(snapshot.get("sampled_at_ms") or 0)
    deltas = {seconds: mid - _previous_mid(market_index, sampled, seconds) for seconds in (1, 3, 5)}
    up_bid = _number(snapshot.get("predict_up_bid"), mid)
    up_ask = _number(snapshot.get("predict_up_ask"), mid)
    values = {
        "seconds_left_norm": _number(snapshot.get("seconds_left")) / 300.0,
        "predict_up_mid_centered": mid - 0.5,
        "predict_extremity": abs(mid - 0.5),
        "predict_spread": max(0.0, up_ask - up_bid),
        "predict_mid_delta_1s": deltas[1], "predict_mid_delta_3s": deltas[3], "predict_mid_delta_5s": deltas[5],
        "abs_predict_mid_delta_1s": abs(deltas[1]), "abs_predict_mid_delta_3s": abs(deltas[3]), "abs_predict_mid_delta_5s": abs(deltas[5]),
    }
    raw_names = (
        "spot_queue_imbalance", "spot_taker_imbalance_250ms", "spot_taker_imbalance_1s",
        "spot_return_250ms_bps", "spot_return_1s_bps", "spot_return_3s_bps", "spot_return_5s_bps",
        "futures_queue_imbalance", "futures_taker_imbalance_250ms", "futures_taker_imbalance_1s",
        "futures_return_250ms_bps", "futures_return_1s_bps", "futures_return_3s_bps", "futures_return_5s_bps",
        "perp_spot_basis_bps", "spot_minus_strike_bps", "chainlink_minus_strike_bps",
        "spot_minus_chainlink_bps", "direction_score",
    )
    values.update({name: _number(snapshot.get(name)) for name in raw_names})
    values.update({
        "abs_direction_score": abs(values["direction_score"]),
        "abs_spot_return_250ms_bps": abs(values["spot_return_250ms_bps"]),
        "abs_spot_return_1s_bps": abs(values["spot_return_1s_bps"]),
        "abs_futures_return_250ms_bps": abs(values["futures_return_250ms_bps"]),
        "abs_futures_return_1s_bps": abs(values["futures_return_1s_bps"]),
    })
    return [float(values[name]) for name in FEATURE_NAMES]


def _split_markets(market_first_ms: dict[int, int]) -> dict[int, str]:
    ordered = sorted(market_first_ms, key=lambda market_id: (market_first_ms[market_id], market_id))
    count = len(ordered)
    train_end = max(1, int(count * 0.60))
    validation_end = max(train_end + 1, int(count * 0.80)) if count >= 3 else count
    return {
        market_id: "TRAIN" if index < train_end else "VALIDATION" if index < validation_end else "TEST"
        for index, market_id in enumerate(ordered)
    }


def _build_samples(
    parents: list[dict[str, Any]],
    index: dict[int, tuple[list[int], list[dict[str, Any]]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[int, str]]:
    by_second: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in parents:
        market_id = int(parent["market_id"])
        second_ms = (int(parent["event_ms"]) // 1_000) * 1_000
        by_second[(market_id, second_ms)].append(parent)
        by_market[market_id].append(parent)
    market_first = {market_id: min(int(row["event_ms"]) for row in rows) for market_id, rows in by_market.items() if market_id in index}
    splits = _split_markets(market_first)

    parent_contexts = []
    for parent in parents:
        market_id = int(parent["market_id"])
        snapshot = _asof_snapshot(index, market_id, int(parent["event_ms"]), maximum_age_ms=2_000)
        if snapshot is None or market_id not in splits:
            continue
        parent_contexts.append({**parent, "split": splits[market_id], "snapshot": snapshot})

    samples = []
    for market_id, split in splits.items():
        times, rows = index[market_id]
        if not rows:
            continue
        start_ms = int(rows[0].get("bucket_start_sec") or 0) * 1_000
        end_ms = int(rows[0].get("window_end_ms") or 0)
        if start_ms <= 0 or end_ms <= start_ms:
            start_ms, end_ms = min(times), max(times)
        for decision_ms in range(((start_ms + 999) // 1_000) * 1_000, end_ms, 1_000):
            snapshot = _asof_snapshot(index, market_id, decision_ms)
            if snapshot is None:
                continue
            batch = by_second.get((market_id, decision_ms), [])
            up_notional = sum(float(row["notional_usdt"]) for row in batch if row["side"] == "UP")
            down_notional = sum(float(row["notional_usdt"]) for row in batch if row["side"] == "DOWN")
            side = "UP" if up_notional > down_notional else "DOWN" if down_notional > up_notional else None
            samples.append({
                "market_id": market_id, "decision_ms": decision_ms, "split": split,
                "target_event": int(bool(batch)), "target_parent_count": len(batch),
                "target_side": side, "target_notional_usdt": up_notional + down_notional,
                "snapshot": snapshot, "features": _features(snapshot, index[market_id]),
            })
    return parent_contexts, samples, splits


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))


def _standardize(train: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = train.mean(axis=0)
    scale = train.std(axis=0)
    scale[scale < 1e-9] = 1.0
    return (values - mean) / scale, mean, scale


def _fit_logistic(x: np.ndarray, y: np.ndarray, *, balanced: bool, epochs: int = 600) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    weights = np.zeros(design.shape[1], dtype=float)
    if balanced:
        positive = max(1, int(y.sum()))
        negative = max(1, len(y) - positive)
        sample_weight = np.where(y > 0.5, len(y) / (2 * positive), len(y) / (2 * negative))
    else:
        sample_weight = np.ones(len(y))
    for epoch in range(epochs):
        probability = _sigmoid(design @ weights)
        gradient = design.T @ ((probability - y) * sample_weight) / len(y)
        gradient[1:] += 0.02 * weights[1:] / len(y)
        learning_rate = 0.08 / math.sqrt(1 + epoch / 80)
        weights -= learning_rate * gradient
    return weights


def _fit_ridge(x: np.ndarray, y: np.ndarray, penalty: float = 2.0) -> np.ndarray:
    design = np.column_stack([np.ones(len(x)), x])
    regularizer = np.eye(design.shape[1]) * penalty
    regularizer[0, 0] = 0.0
    return np.linalg.solve(design.T @ design + regularizer, design.T @ y)


def _predict_linear(x: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones(len(x)), x]) @ weights


def _match_batches(actual: list[dict[str, Any]], predicted: list[dict[str, Any]], window_ms: int) -> list[tuple[int, int]]:
    actual_by_market: dict[int, list[int]] = defaultdict(list)
    predicted_by_market: dict[int, list[int]] = defaultdict(list)
    for index, row in enumerate(actual):
        actual_by_market[int(row["market_id"])].append(index)
    for index, row in enumerate(predicted):
        predicted_by_market[int(row["market_id"])].append(index)
    matches = []
    for market_id, predicted_indexes in predicted_by_market.items():
        unused = set(actual_by_market.get(market_id, []))
        for predicted_index in predicted_indexes:
            candidates = [
                (abs(int(predicted[predicted_index]["decision_ms"]) - int(actual[index]["decision_ms"])), index)
                for index in unused
                if abs(int(predicted[predicted_index]["decision_ms"]) - int(actual[index]["decision_ms"])) <= window_ms
            ]
            if not candidates:
                continue
            _, actual_index = min(candidates)
            unused.remove(actual_index)
            matches.append((predicted_index, actual_index))
    return matches


def _ratio_similarity(left: float, right: float) -> float:
    if left <= 0 and right <= 0:
        return 1.0
    if left <= 0 or right <= 0:
        return 0.0
    return min(left, right) / max(left, right)


def _evaluate(actual: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> dict[str, Any]:
    matches_1s = _match_batches(actual, predicted, 1_000)
    matches_3s = _match_batches(actual, predicted, 3_000)
    precision = len(matches_1s) / len(predicted) if predicted else 0.0
    recall = len(matches_1s) / len(actual) if actual else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    side_samples = [
        predicted[predicted_index].get("side") == actual[actual_index].get("target_side")
        for predicted_index, actual_index in matches_1s
        if actual[actual_index].get("target_side") in {"UP", "DOWN"}
    ]
    side_accuracy = sum(side_samples) / len(side_samples) if side_samples else 0.0
    markets = sorted({int(row["market_id"]) for row in actual} | {int(row["market_id"]) for row in predicted})
    parent_similarities = []
    notional_similarities = []
    for market_id in markets:
        actual_rows = [row for row in actual if int(row["market_id"]) == market_id]
        predicted_rows = [row for row in predicted if int(row["market_id"]) == market_id]
        parent_similarities.append(_ratio_similarity(
            sum(int(row["target_parent_count"]) for row in actual_rows),
            sum(int(row["parent_count"]) for row in predicted_rows),
        ))
        notional_similarities.append(_ratio_similarity(
            sum(float(row["target_notional_usdt"]) for row in actual_rows),
            sum(float(row["notional_usdt"]) for row in predicted_rows),
        ))
    parent_similarity = statistics.mean(parent_similarities) if parent_similarities else 0.0
    notional_similarity = statistics.mean(notional_similarities) if notional_similarities else 0.0
    overall = 0.40 * f1 + 0.20 * side_accuracy + 0.20 * parent_similarity + 0.20 * notional_similarity
    return {
        "actualBatches": len(actual), "predictedBatches": len(predicted),
        "matchedWithin1s": len(matches_1s), "matchedWithin3s": len(matches_3s),
        "timingPrecision1s": precision, "timingRecall1s": recall, "timingF1_1s": f1,
        "timingRecall3s": len(matches_3s) / len(actual) if actual else 0.0,
        "sideAccuracyMatched1s": side_accuracy, "sideSamples": len(side_samples),
        "perMarketParentCountSimilarity": parent_similarity,
        "perMarketNotionalSimilarity": notional_similarity,
        "overallSimilarity": overall,
        "weights": {"timingF1_1s": 0.40, "sideAccuracy": 0.20, "parentCount": 0.20, "notional": 0.20},
    }


def _simulate(
    rows: list[dict[str, Any]], event_probability: np.ndarray, side_probability: np.ndarray,
    count_prediction: np.ndarray, notional_prediction: np.ndarray,
    *, threshold: float, cooldown_seconds: int,
) -> list[dict[str, Any]]:
    result = []
    last_by_market: dict[int, int] = {}
    for index, row in enumerate(rows):
        if event_probability[index] < threshold:
            continue
        market_id = int(row["market_id"])
        decision_ms = int(row["decision_ms"])
        if decision_ms - last_by_market.get(market_id, -10**18) < cooldown_seconds * 1_000:
            continue
        result.append({
            "market_id": market_id, "decision_ms": decision_ms,
            "side": "UP" if side_probability[index] >= 0.5 else "DOWN",
            "event_probability": float(event_probability[index]),
            "parent_count": int(np.clip(round(math.expm1(count_prediction[index])), 1, 7)),
            "notional_usdt": float(np.clip(math.expm1(notional_prediction[index]), 1.0, 250.0)),
        })
        last_by_market[market_id] = decision_ms
    return result


def _persist_dataset(
    path: Path, parent_contexts: list[dict[str, Any]], samples: list[dict[str, Any]],
    splits: dict[int, str], metadata: dict[str, Any], simulations: dict[str, list[dict[str, Any]]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE research_meta(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        CREATE TABLE market_splits(market_id INTEGER PRIMARY KEY,split TEXT NOT NULL);
        CREATE TABLE target_parent_context(
          market_id INTEGER NOT NULL,order_hash TEXT NOT NULL,side TEXT NOT NULL,event_ms INTEGER NOT NULL,
          shares REAL NOT NULL,notional_usdt REAL NOT NULL,average_price REAL NOT NULL,fill_legs INTEGER NOT NULL,
          split TEXT NOT NULL,snapshot_timestamp_ns INTEGER NOT NULL,snapshot_age_ms REAL NOT NULL,snapshot_json TEXT NOT NULL,
          PRIMARY KEY(market_id,order_hash,side));
        CREATE TABLE decision_samples(
          market_id INTEGER NOT NULL,decision_ms INTEGER NOT NULL,split TEXT NOT NULL,target_event INTEGER NOT NULL,
          target_parent_count INTEGER NOT NULL,target_side TEXT,target_notional_usdt REAL NOT NULL,
          snapshot_timestamp_ns INTEGER NOT NULL,snapshot_json TEXT NOT NULL,features_json TEXT NOT NULL,
          PRIMARY KEY(market_id,decision_ms));
        CREATE TABLE simulated_batches(
          split TEXT NOT NULL,market_id INTEGER NOT NULL,decision_ms INTEGER NOT NULL,side TEXT NOT NULL,
          event_probability REAL NOT NULL,parent_count INTEGER NOT NULL,notional_usdt REAL NOT NULL,
          PRIMARY KEY(split,market_id,decision_ms));
        """
    )
    db.executemany("INSERT INTO research_meta(key,value_json) VALUES (?,?)", [
        (key, json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)) for key, value in metadata.items()
    ])
    db.executemany("INSERT INTO market_splits(market_id,split) VALUES (?,?)", sorted(splits.items()))
    db.executemany(
        "INSERT INTO target_parent_context VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(
            int(row["market_id"]), row["order_hash"], row["side"], int(row["event_ms"]), float(row["shares"]),
            float(row["notional_usdt"]), float(row["average_price"]), int(row["fill_legs"]), row["split"],
            int(row["snapshot"]["timestamp_ns"]),
            int(row["event_ms"]) - int(row["snapshot"]["sampled_at_ms"]),
            json.dumps(row["snapshot"], ensure_ascii=False, separators=(",", ":"), default=str),
        ) for row in parent_contexts],
    )
    db.executemany(
        "INSERT INTO decision_samples VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(
            int(row["market_id"]), int(row["decision_ms"]), row["split"], int(row["target_event"]),
            int(row["target_parent_count"]), row["target_side"], float(row["target_notional_usdt"]),
            int(row["snapshot"]["timestamp_ns"]),
            json.dumps(row["snapshot"], ensure_ascii=False, separators=(",", ":"), default=str),
            json.dumps(dict(zip(FEATURE_NAMES, row["features"])), ensure_ascii=False, separators=(",", ":")),
        ) for row in samples],
    )
    for split, rows in simulations.items():
        db.executemany(
            "INSERT INTO simulated_batches VALUES (?,?,?,?,?,?,?)",
            [(split, int(row["market_id"]), int(row["decision_ms"]), row["side"], float(row["event_probability"]), int(row["parent_count"]), float(row["notional_usdt"])) for row in rows],
        )
    db.commit()
    db.close()


def reconstruct(
    wallet_db_path: Path, signal_db_path: Path, archive_db_path: Path | None = DEFAULT_ARCHIVE_DB
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    signal_db = sqlite3.connect(signal_db_path)
    signal_db.row_factory = sqlite3.Row
    deployed_row = signal_db.execute("SELECT value FROM wallet_taker_signal_meta WHERE key='deployed_at_ms'").fetchone()
    deployed_at_ms = int(deployed_row[0]) if deployed_row else 0
    wallet_db = sqlite3.connect(wallet_db_path)
    wallet_db.row_factory = sqlite3.Row
    parents = _target_parents(wallet_db, deployed_at_ms)
    index = _snapshot_index(signal_db, archive_db_path)
    parent_contexts, samples, splits = _build_samples(parents, index)
    wallet_db.close()
    signal_db.close()

    split_rows = {split: [row for row in samples if row["split"] == split] for split in ("TRAIN", "VALIDATION", "TEST")}
    train_rows = split_rows["TRAIN"]
    train_x_raw = np.asarray([row["features"] for row in train_rows], dtype=float)
    all_x_raw = np.asarray([row["features"] for row in samples], dtype=float)
    all_x, mean, scale = _standardize(train_x_raw, all_x_raw)
    x_by_id = {id(row): all_x[index] for index, row in enumerate(samples)}
    train_x = np.asarray([x_by_id[id(row)] for row in train_rows])
    train_event_y = np.asarray([row["target_event"] for row in train_rows], dtype=float)
    event_weights = _fit_logistic(train_x, train_event_y, balanced=True)

    positive_train = [row for row in train_rows if row["target_event"] and row["target_side"] in {"UP", "DOWN"}]
    positive_x = np.asarray([x_by_id[id(row)] for row in positive_train])
    side_y = np.asarray([1.0 if row["target_side"] == "UP" else 0.0 for row in positive_train])
    side_weights = _fit_logistic(positive_x, side_y, balanced=False)
    count_weights = _fit_ridge(positive_x, np.log1p([row["target_parent_count"] for row in positive_train]))
    notional_weights = _fit_ridge(positive_x, np.log1p([row["target_notional_usdt"] for row in positive_train]))

    predictions: dict[str, dict[str, np.ndarray]] = {}
    for split, rows in split_rows.items():
        x = np.asarray([x_by_id[id(row)] for row in rows])
        predictions[split] = {
            "event": _sigmoid(_predict_linear(x, event_weights)),
            "side": _sigmoid(_predict_linear(x, side_weights)),
            "count": _predict_linear(x, count_weights),
            "notional": _predict_linear(x, notional_weights),
        }

    validation_actual = [row for row in split_rows["VALIDATION"] if row["target_event"]]
    candidates = []
    for threshold in np.linspace(0.50, 0.90, 17):
        for cooldown in (0, 1, 2, 3, 5, 8, 10):
            predicted = _simulate(split_rows["VALIDATION"], **{
                "event_probability": predictions["VALIDATION"]["event"],
                "side_probability": predictions["VALIDATION"]["side"],
                "count_prediction": predictions["VALIDATION"]["count"],
                "notional_prediction": predictions["VALIDATION"]["notional"],
            }, threshold=float(threshold), cooldown_seconds=cooldown)
            metrics = _evaluate(validation_actual, predicted)
            candidates.append((metrics["overallSimilarity"], -abs(len(predicted) - len(validation_actual)), float(threshold), cooldown, metrics))
    _, _, threshold, cooldown, validation_metrics = max(candidates, key=lambda row: (row[0], row[1]))

    simulations = {}
    evaluations = {}
    for split, rows in split_rows.items():
        simulations[split] = _simulate(
            rows, predictions[split]["event"], predictions[split]["side"],
            predictions[split]["count"], predictions[split]["notional"],
            threshold=threshold, cooldown_seconds=cooldown,
        )
        evaluations[split] = _evaluate([row for row in rows if row["target_event"]], simulations[split])

    market_profiles = {}
    for split in ("TRAIN", "VALIDATION", "TEST"):
        rows = [row for row in parent_contexts if row["split"] == split]
        market_ids = sorted({int(row["market_id"]) for row in rows})
        market_profiles[split] = {
            "markets": len(market_ids), "parents": len(rows),
            "notionalUsdt": sum(float(row["notional_usdt"]) for row in rows),
            "parentsPerMarket": len(rows) / len(market_ids) if market_ids else None,
            "notionalPerMarket": sum(float(row["notional_usdt"]) for row in rows) / len(market_ids) if market_ids else None,
        }
    gaps = []
    by_market: dict[int, list[int]] = defaultdict(list)
    for row in parent_contexts:
        by_market[int(row["market_id"])].append(int(row["event_ms"]))
    for times in by_market.values():
        times.sort()
        gaps.extend((right - left) / 1_000 for left, right in zip(times, times[1:]))

    model = {
        "name": "TARGET_TAKER_PRIVATE_INFERENCE_V1", "status": "OFFLINE_HOLDOUT_ONLY",
        "trainedAt": datetime.now().astimezone().isoformat(), "featureNames": list(FEATURE_NAMES),
        "standardization": {"mean": mean.tolist(), "scale": scale.tolist()},
        "eventLogisticWeights": event_weights.tolist(), "sideLogisticWeights": side_weights.tolist(),
        "batchCountRidgeWeights": count_weights.tolist(), "batchNotionalRidgeWeights": notional_weights.tolist(),
        "decisionThreshold": threshold, "cooldownSeconds": cooldown, "decisionCadenceSeconds": 1,
        "liveEligible": False, "targetEventsRequiredAtInference": False,
    }
    report = {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "scope": {
            "objective": "infer a private Taker timing/side/count/notional policy from causal public market data",
            "oldStrategyUsed": False, "paperOnly": True, "liveRuntimeChanged": False,
            "permanentOneSecondArchive": str(archive_db_path) if archive_db_path else None,
            "parentDefinition": "one target order_hash; timestamp is first observed fill",
            "eventTimeCaveat": "Predict executedAt is second-resolution; selected snapshot is at least 250ms before that second",
            "unobservable": "private order placement time, cancelled/unfilled orders and private data sources",
        },
        "coverage": {
            "targetParents": len(parents), "savedParentContexts": len(parent_contexts),
            "pairingRate": len(parent_contexts) / len(parents) if parents else None,
            "markets": len(splits), "decisionSamples": len(samples),
            "negativeControls": sum(not row["target_event"] for row in samples),
        },
        "behavior": {
            "marketSplits": market_profiles,
            "parentGapSeconds": {
                "median": statistics.median(gaps) if gaps else None, "p25": _quantile(gaps, 0.25),
                "p75": _quantile(gaps, 0.75), "p90": _quantile(gaps, 0.90),
                "within1s": sum(value <= 1 for value in gaps) / len(gaps) if gaps else None,
                "within5s": sum(value <= 5 for value in gaps) / len(gaps) if gaps else None,
            },
        },
        "model": {
            "name": model["name"], "features": len(FEATURE_NAMES),
            "thresholdSelectedOnValidation": threshold, "cooldownSelectedOnValidationSeconds": cooldown,
            "training": evaluations["TRAIN"], "validation": validation_metrics,
            "untouchedChronologicalTest": evaluations["TEST"],
        },
        "interpretation": {
            "minimumInitialInferenceMarkets": 30,
            "sampleSizeReady": len(splits) >= 30,
            "similarityTarget": 0.80,
            "testMeetsSimilarityTarget": evaluations["TEST"]["overallSimilarity"] >= 0.80,
            "rule": "Only untouched chronological TEST similarity can justify a forward paper candidate; this cannot prove the target uses these exact inputs.",
        },
    }
    metadata = {"report": report, "model": model, "feature_names": list(FEATURE_NAMES)}
    dataset = {"parentContexts": parent_contexts, "samples": samples, "splits": splits, "simulations": simulations, "metadata": metadata}
    return report, model, dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconstruct a target-specific private Taker policy with chronological holdout")
    parser.add_argument("--wallet-db", type=Path, default=DEFAULT_WALLET_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--archive-db", type=Path, default=DEFAULT_ARCHIVE_DB)
    parser.add_argument("--dataset-db", type=Path, default=DEFAULT_DATASET_DB)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    report, model, dataset = reconstruct(args.wallet_db, args.signal_db, args.archive_db)
    _persist_dataset(args.dataset_db, dataset["parentContexts"], dataset["samples"], dataset["splits"], dataset["metadata"], dataset["simulations"])
    args.model.parent.mkdir(parents=True, exist_ok=True)
    args.model.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"dataset": str(args.dataset_db), "model": str(args.model), "report": str(args.report), **report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
