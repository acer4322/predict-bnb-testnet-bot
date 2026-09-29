from __future__ import annotations

import csv
import json
import lzma
import math
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT / "target_orderflow_multiview_identifiability_v1_preregistered.json"
REPORT = OUT / "target_orderflow_multiview_identifiability_v1_report.json"
ROWS_CSV = OUT / "target_orderflow_multiview_identifiability_v1_parents.csv"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
TAPE_DIR = ROOT / "data" / "execution_tape_v1" / "markets"

TRAIN_MARKETS = [1569361, 1571387]
VALIDATION_MARKETS = [1572594]
ALL_MARKETS = TRAIN_MARKETS + VALIDATION_MARKETS
SEED = 20260823
PERMUTATIONS = 64
EPS = 1e-9

RAW_FEATURES = [
    "side_is_bid",
    "native_price",
    "placement_quantity",
    "best_bid",
    "best_ask",
    "spread_ticks",
    "offset_ticks",
    "front_depth",
    "level_size_after",
    "top3_same_side_depth",
    "front_depth_per_share",
    "level_size_per_share",
    "level_add_qty_1s",
    "level_remove_qty_1s",
    "level_add_qty_5s",
    "level_remove_qty_5s",
    "level_update_count_1s",
    "level_update_count_5s",
]

PUBLIC_ADDED_FEATURES = [
    "order_count",
    "order_count_delta_1s",
    "order_count_delta_5s",
    "global_add_qty_1s",
    "global_remove_qty_1s",
    "global_add_qty_5s",
    "global_remove_qty_5s",
    "global_update_count_1s",
    "global_update_count_5s",
    "placement_source_receipt_lag_ms",
    "mean_abs_source_receipt_lag_1s",
    "mean_abs_source_receipt_lag_5s",
    "safe_match_count_1s",
    "safe_match_count_5s",
    "safe_match_qty_1s",
    "safe_match_qty_5s",
    "safe_match_maker_legs_1s",
    "safe_match_maker_legs_5s",
    "same_level_safe_match_count_5s",
    "same_level_safe_match_qty_5s",
    "same_level_unique_maker_hashes_5s",
    "same_level_match_to_remove_ratio_5s",
]

TARGET_ADDED_FEATURES = [
    "inferred_target_same_level_qty_before",
    "inferred_target_better_qty_before",
    "inferred_target_side_qty_before",
    "inferred_target_same_level_parent_count_before",
    "inferred_target_side_parent_count_before",
    "inferred_target_same_level_public_ratio_before",
    "inferred_target_better_to_front_ratio_before",
]

PUBLIC_FEATURES = RAW_FEATURES + PUBLIC_ADDED_FEATURES
MULTIVIEW_FEATURES = PUBLIC_FEATURES + TARGET_ADDED_FEATURES


def ro(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def iso_ms(value: Any) -> int:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def wei(value: Any) -> float:
    return float(value) / 1e18


def load_tape(market_id: int) -> dict[str, Any]:
    path = TAPE_DIR / f"{market_id}.json.xz"
    with lzma.open(path, "rt", encoding="utf-8") as handle:
        tape = json.load(handle)
    if int(tape.get("marketId") or 0) != int(market_id):
        raise RuntimeError(f"tape market mismatch: {path}")
    return tape


def load_parents(market_id: int) -> list[dict[str, Any]]:
    connection = ro(BOOK_DB)
    try:
        return [
            dict(row)
            for row in connection.execute(
                """SELECT p.parent_id,p.order_hash,p.target_side,p.native_book_side,
                          p.target_price,p.native_price,p.first_target_ms,p.last_target_ms,
                          p.target_filled_shares,p.expected_parent_shares,p.confidence,
                          p.placement_coverage,p.fill_allocation_coverage,
                          MIN(u.received_at_ms) placement_received_ms,
                          MIN(u.source_timestamp_ms) placement_source_ms,
                          COUNT(*) placement_allocation_count,
                          SUM(a.allocated_quantity) placement_quantity,
                          SUM(a.public_delta_quantity) placement_public_delta_quantity,
                          AVG(a.score) placement_allocation_score
                     FROM maker_book_inference_v21_parent_lifecycles p
                     JOIN maker_book_inference_v21_allocations a
                       ON a.parent_id=p.parent_id AND a.allocation_kind='PARENT_PLACEMENT'
                     JOIN maker_book_inference_updates u ON u.id=a.update_id
                    WHERE p.market_id=?
                      AND p.confidence>=0.60
                      AND p.placement_coverage>=0.85
                      AND p.fill_allocation_coverage>=0.70
                      AND ABS(p.expected_parent_shares-p.target_filled_shares)<=0.05
                    GROUP BY p.parent_id
                   HAVING COUNT(*)=1
                      AND MIN(u.received_at_ms)<=p.first_target_ms+999
                    ORDER BY placement_received_ms,p.parent_id""",
                (int(market_id),),
            )
        ]
    finally:
        connection.close()


def load_target_allocation_events(market_id: int) -> list[dict[str, Any]]:
    connection = ro(BOOK_DB)
    try:
        return [
            dict(row)
            for row in connection.execute(
                """SELECT a.parent_id,a.allocation_kind,a.native_book_side,a.native_price,
                          a.allocated_quantity,u.received_at_ms
                     FROM maker_book_inference_v21_allocations a
                     JOIN maker_book_inference_updates u ON u.id=a.update_id
                    WHERE a.market_id=?
                      AND a.allocation_kind IN ('PARENT_PLACEMENT','TARGET_FILL_DECREASE')
                    ORDER BY u.received_at_ms,a.allocation_id""",
                (int(market_id),),
            )
        ]
    finally:
        connection.close()


def exact_hash_fills(tape: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for match in tape.get("matches") or []:
        ts_ms = iso_ms(match.get("executedAt"))
        transaction_hash = str(match.get("transactionHash") or "")
        for maker in match.get("makers") or []:
            order_hash = str(maker.get("hash") or maker.get("orderHash") or "").lower()
            if not order_hash:
                continue
            row = grouped.setdefault(
                order_hash,
                {"first_ms": ts_ms, "last_ms": ts_ms, "shares": 0.0, "legs": 0, "transactions": set()},
            )
            row["first_ms"] = min(int(row["first_ms"]), ts_ms)
            row["last_ms"] = max(int(row["last_ms"]), ts_ms)
            row["shares"] += wei(maker.get("amount") or 0)
            row["legs"] += 1
            if transaction_hash:
                row["transactions"].add(transaction_hash)
    for row in grouped.values():
        row["transactions"] = len(row["transactions"])
    return grouped


def normalized_public_matches(tape: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in tape.get("matches") or []:
        ts_ms = iso_ms(match.get("executedAt"))
        taker = match.get("taker") if isinstance(match.get("taker"), dict) else {}
        outcome = taker.get("outcome") if isinstance(taker.get("outcome"), dict) else {}
        outcome_name = str(outcome.get("name") or "").upper()
        quote_type = str(taker.get("quoteType") or "").upper()
        price = wei(taker.get("price") or match.get("priceExecuted") or 0)
        qty = wei(taker.get("amount") or match.get("amountFilled") or 0)
        if outcome_name == "UP" and quote_type == "BID":
            aggressor, native_price = "BUY", price
        elif outcome_name == "UP" and quote_type == "ASK":
            aggressor, native_price = "SELL", price
        elif outcome_name == "DOWN" and quote_type == "BID":
            aggressor, native_price = "SELL", 1.0 - price
        elif outcome_name == "DOWN" and quote_type == "ASK":
            aggressor, native_price = "BUY", 1.0 - price
        else:
            continue
        resting_side = "ASK" if aggressor == "BUY" else "BID"
        makers = [maker for maker in (match.get("makers") or []) if isinstance(maker, dict)]
        hashes = {str(maker.get("hash") or "").lower() for maker in makers if maker.get("hash")}
        rows.append(
            {
                "ts_ms": ts_ms,
                "safe_known_ms": ts_ms + 999,
                "resting_side": resting_side,
                "native_price": round(native_price, 10),
                "qty": qty,
                "maker_legs": len(makers),
                "maker_hashes": hashes,
            }
        )
    return rows


def apply_update(book: dict[str, dict[float, float]], row: list[Any]) -> list[dict[str, Any]]:
    source_ms, received_ms, order_count, is_checkpoint = map(int, row[:4])
    changes = row[6] if len(row) > 6 and isinstance(row[6], dict) else {}
    if is_checkpoint:
        book["bids"] = {round(float(k), 10): float(v) for k, v in (row[4] or {}).items() if float(v) > EPS}
        book["asks"] = {round(float(k), 10): float(v) for k, v in (row[5] or {}).items() if float(v) > EPS}
    else:
        for side in ("bids", "asks"):
            for item in changes.get(side, []) or []:
                price = round(float(item[0]), 10)
                after = float(item[2])
                if after <= EPS:
                    book[side].pop(price, None)
                else:
                    book[side][price] = after
    flat_changes: list[dict[str, Any]] = []
    for side in ("bids", "asks"):
        for item in changes.get(side, []) or []:
            flat_changes.append(
                {
                    "side": side,
                    "price": round(float(item[0]), 10),
                    "delta": float(item[3]),
                }
            )
    return [
        {
            "source_ms": source_ms,
            "received_ms": received_ms,
            "order_count": order_count,
            "lag_ms": source_ms - received_ms,
            "changes": flat_changes,
        }
    ]


def aggregate_update_history(
    events: list[dict[str, Any]], placement_ms: int, native_side: str, native_price: float
) -> dict[str, float]:
    side_key = "bids" if native_side == "BID" else "asks"
    output: dict[str, float] = {}
    for horizon_ms, suffix in ((1000, "1s"), (5000, "5s")):
        selected = [event for event in events if placement_ms - horizon_ms < int(event["received_ms"]) <= placement_ms]
        level_add = level_remove = global_add = global_remove = 0.0
        level_updates = global_updates = 0
        for event in selected:
            for change in event["changes"]:
                delta = float(change["delta"])
                global_updates += 1
                global_add += max(0.0, delta)
                global_remove += max(0.0, -delta)
                if change["side"] == side_key and abs(float(change["price"]) - native_price) <= EPS:
                    level_updates += 1
                    level_add += max(0.0, delta)
                    level_remove += max(0.0, -delta)
        output[f"level_add_qty_{suffix}"] = level_add
        output[f"level_remove_qty_{suffix}"] = level_remove
        output[f"level_update_count_{suffix}"] = float(level_updates)
        output[f"global_add_qty_{suffix}"] = global_add
        output[f"global_remove_qty_{suffix}"] = global_remove
        output[f"global_update_count_{suffix}"] = float(global_updates)
        output[f"mean_abs_source_receipt_lag_{suffix}"] = (
            float(np.mean([abs(float(event["lag_ms"])) for event in selected])) if selected else math.nan
        )
        if selected:
            output[f"order_count_delta_{suffix}"] = float(selected[-1]["order_count"] - selected[0]["order_count"])
        else:
            output[f"order_count_delta_{suffix}"] = 0.0
    output["order_count"] = float(events[-1]["order_count"]) if events else math.nan
    output["placement_source_receipt_lag_ms"] = float(events[-1]["lag_ms"]) if events else math.nan
    return output


def aggregate_match_history(
    matches: list[dict[str, Any]], placement_ms: int, native_side: str, native_price: float, level_remove_5s: float
) -> dict[str, float]:
    output: dict[str, float] = {}
    for horizon_ms, suffix in ((1000, "1s"), (5000, "5s")):
        selected = [
            row
            for row in matches
            if placement_ms - horizon_ms < int(row["safe_known_ms"]) <= placement_ms
        ]
        output[f"safe_match_count_{suffix}"] = float(len(selected))
        output[f"safe_match_qty_{suffix}"] = float(sum(float(row["qty"]) for row in selected))
        output[f"safe_match_maker_legs_{suffix}"] = float(sum(int(row["maker_legs"]) for row in selected))
        if suffix == "5s":
            same = [
                row
                for row in selected
                if row["resting_side"] == native_side and abs(float(row["native_price"]) - native_price) <= EPS
            ]
            hashes: set[str] = set()
            for row in same:
                hashes.update(row["maker_hashes"])
            same_qty = float(sum(float(row["qty"]) for row in same))
            output["same_level_safe_match_count_5s"] = float(len(same))
            output["same_level_safe_match_qty_5s"] = same_qty
            output["same_level_unique_maker_hashes_5s"] = float(len(hashes))
            output["same_level_match_to_remove_ratio_5s"] = same_qty / max(level_remove_5s, EPS)
    return output


def inferred_target_state(
    events: list[dict[str, Any]], parent_id: str, placement_ms: int, native_side: str, native_price: float,
    level_size: float, front_depth: float,
) -> dict[str, float]:
    balance: dict[str, dict[str, Any]] = {}
    for event in events:
        if int(event["received_at_ms"]) > placement_ms:
            break
        pid = str(event["parent_id"] or "")
        if not pid or pid == parent_id:
            continue
        row = balance.setdefault(
            pid,
            {
                "side": str(event["native_book_side"]),
                "price": round(float(event["native_price"]), 10),
                "quantity": 0.0,
            },
        )
        sign = 1.0 if str(event["allocation_kind"]) == "PARENT_PLACEMENT" else -1.0
        row["quantity"] = max(0.0, float(row["quantity"]) + sign * float(event["allocated_quantity"]))
    active = [row for row in balance.values() if float(row["quantity"]) > EPS and row["side"] == native_side]
    same = [row for row in active if abs(float(row["price"]) - native_price) <= EPS]
    if native_side == "BID":
        better = [row for row in active if float(row["price"]) > native_price + EPS]
    else:
        better = [row for row in active if float(row["price"]) < native_price - EPS]
    same_qty = float(sum(float(row["quantity"]) for row in same))
    better_qty = float(sum(float(row["quantity"]) for row in better))
    side_qty = float(sum(float(row["quantity"]) for row in active))
    return {
        "inferred_target_same_level_qty_before": same_qty,
        "inferred_target_better_qty_before": better_qty,
        "inferred_target_side_qty_before": side_qty,
        "inferred_target_same_level_parent_count_before": float(len(same)),
        "inferred_target_side_parent_count_before": float(len(active)),
        "inferred_target_same_level_public_ratio_before": same_qty / max(level_size, EPS),
        "inferred_target_better_to_front_ratio_before": better_qty / max(front_depth, EPS),
    }


def placement_features(
    parent: dict[str, Any], tape: dict[str, Any], matches: list[dict[str, Any]], allocation_events: list[dict[str, Any]]
) -> dict[str, Any]:
    placement_ms = int(parent["placement_received_ms"])
    placement_source_ms = int(parent["placement_source_ms"])
    update_rows = sorted(tape.get("updates") or [], key=lambda row: (int(row[1]), int(row[0])))
    book: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}
    history: list[dict[str, Any]] = []
    for update in update_rows:
        key = (int(update[1]), int(update[0]))
        if key > (placement_ms, placement_source_ms):
            break
        history.extend(apply_update(book, update))
    if not history or not book["bids"] or not book["asks"]:
        raise RuntimeError(f"missing placement book state for {parent['parent_id']}")
    native_side = str(parent["native_book_side"]).upper()
    side_key = "bids" if native_side == "BID" else "asks"
    native_price = round(float(parent["native_price"]), 10)
    quantity = float(parent["placement_quantity"])
    best_bid = max(book["bids"])
    best_ask = min(book["asks"])
    if native_side == "BID":
        front_depth = sum(qty for price, qty in book[side_key].items() if price > native_price + EPS)
        offset_ticks = (best_bid - native_price) / 0.01
        ordered = sorted(book[side_key], reverse=True)
    else:
        front_depth = sum(qty for price, qty in book[side_key].items() if price < native_price - EPS)
        offset_ticks = (native_price - best_ask) / 0.01
        ordered = sorted(book[side_key])
    level_size = float(book[side_key].get(native_price, 0.0))
    top3_depth = float(sum(book[side_key][price] for price in ordered[:3]))
    features: dict[str, Any] = {
        "side_is_bid": float(native_side == "BID"),
        "native_price": native_price,
        "placement_quantity": quantity,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_ticks": (best_ask - best_bid) / 0.01,
        "offset_ticks": offset_ticks,
        "front_depth": float(front_depth),
        "level_size_after": level_size,
        "top3_same_side_depth": top3_depth,
        "front_depth_per_share": float(front_depth) / max(quantity, EPS),
        "level_size_per_share": level_size / max(quantity, EPS),
    }
    features.update(aggregate_update_history(history, placement_ms, native_side, native_price))
    features.update(
        aggregate_match_history(
            matches, placement_ms, native_side, native_price, float(features["level_remove_qty_5s"])
        )
    )
    features.update(
        inferred_target_state(
            allocation_events,
            str(parent["parent_id"]),
            placement_ms,
            native_side,
            native_price,
            level_size,
            float(front_depth),
        )
    )
    return features


def build_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    output: list[dict[str, Any]] = []
    integrity_rows: list[dict[str, Any]] = []
    for market_id in ALL_MARKETS:
        tape = load_tape(market_id)
        hash_fills = exact_hash_fills(tape)
        matches = normalized_public_matches(tape)
        allocation_events = load_target_allocation_events(market_id)
        for parent in load_parents(market_id):
            order_hash = str(parent.get("order_hash") or "").lower()
            exact = hash_fills.get(order_hash)
            found = exact is not None
            shares_ok = bool(found and abs(float(exact["shares"]) - float(parent["target_filled_shares"])) <= 0.05)
            second_ok = bool(found and int(exact["first_ms"]) == int(parent["first_target_ms"]))
            integrity_rows.append(
                {
                    "market_id": market_id,
                    "parent_id": parent["parent_id"],
                    "found": found,
                    "shares_ok": shares_ok,
                    "second_ok": second_ok,
                }
            )
            if not (found and shares_ok and second_ok):
                continue
            placement_ms = int(parent["placement_received_ms"])
            lower = max(0.0, float(exact["first_ms"] - placement_ms))
            upper = max(lower, float(exact["first_ms"] + 999 - placement_ms))
            row: dict[str, Any] = {
                "market_id": market_id,
                "parent_id": str(parent["parent_id"]),
                "order_hash": order_hash,
                "placement_received_ms": placement_ms,
                "first_hash_fill_ms": int(exact["first_ms"]),
                "duration_lower_ms": lower,
                "duration_upper_ms": upper,
                "duration_midpoint_ms": (lower + upper) / 2.0,
                "target_fill_shares": float(parent["target_filled_shares"]),
                "hash_fill_legs": int(exact["legs"]),
                "hash_fill_transactions": int(exact["transactions"]),
                "teacher_confidence": float(parent["confidence"]),
                "placement_coverage": float(parent["placement_coverage"]),
                "fill_allocation_coverage": float(parent["fill_allocation_coverage"]),
            }
            row.update(placement_features(parent, tape, matches, allocation_events))
            output.append(row)
    count = len(integrity_rows)
    integrity = {
        "qualifiedParents": count,
        "makerHashParentsFound": sum(int(row["found"]) for row in integrity_rows),
        "shareAgreementWithinFiveCents": sum(int(row["shares_ok"]) for row in integrity_rows),
        "firstSecondAgreement": sum(int(row["second_ok"]) for row in integrity_rows),
        "makerHashParentCoverage": sum(int(row["found"]) for row in integrity_rows) / count if count else 0.0,
        "makerHashShareAgreementRate": sum(int(row["shares_ok"]) for row in integrity_rows) / count if count else 0.0,
        "makerHashFirstSecondAgreementRate": sum(int(row["second_ok"]) for row in integrity_rows) / count if count else 0.0,
        "usableParents": len(output),
    }
    return output, integrity


def matrix(rows: list[dict[str, Any]], features: list[str]) -> np.ndarray:
    return np.asarray([[float(row.get(feature, math.nan)) for feature in features] for row in rows], dtype=float)


def rankdata(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + j - 1) / 2.0
        ranks[order[i:j]] = rank
        i = j
    return ranks


def spearman(y: np.ndarray, prediction: np.ndarray) -> float | None:
    if len(y) < 2:
        return None
    a, b = rankdata(y), rankdata(prediction)
    if float(np.std(a)) <= EPS or float(np.std(b)) <= EPS:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def metrics(rows: list[dict[str, Any]], prediction: np.ndarray) -> dict[str, Any]:
    lower = np.asarray([float(row["duration_lower_ms"]) for row in rows])
    upper = np.asarray([float(row["duration_upper_ms"]) for row in rows])
    midpoint = np.asarray([float(row["duration_midpoint_ms"]) for row in rows])
    pred = np.maximum(0.0, np.asarray(prediction, dtype=float))
    distance = np.where(pred < lower, lower - pred, np.where(pred > upper, pred - upper, 0.0))
    return {
        "n": len(rows),
        "medianIntervalErrorMs": float(np.median(distance)),
        "meanIntervalErrorMs": float(np.mean(distance)),
        "medianMidpointAbsoluteErrorMs": float(np.median(np.abs(pred - midpoint))),
        "exactIntervalCoverage": float(np.mean((pred >= lower) & (pred <= upper))),
        "expandedPlusMinus1sCoverage": float(np.mean((pred >= np.maximum(0.0, lower - 1000)) & (pred <= upper + 1000))),
        "spearmanDuration": spearman(midpoint, pred),
        "predictionMedianMs": float(np.median(pred)),
        "observedMidpointMedianMs": float(np.median(midpoint)),
    }


def fit_predict(
    train_rows: list[dict[str, Any]], validation_rows: list[dict[str, Any]], features: list[str], seed: int,
    trees: int = 300,
) -> tuple[np.ndarray, np.ndarray, Any]:
    model = make_pipeline(
        SimpleImputer(strategy="median", add_indicator=True),
        RandomForestRegressor(
            n_estimators=trees,
            max_depth=4,
            min_samples_leaf=3,
            max_features=0.8,
            random_state=seed,
            n_jobs=-1,
        ),
    )
    y = np.log1p(np.asarray([float(row["duration_midpoint_ms"]) for row in train_rows]))
    model.fit(matrix(train_rows, features), y)
    train_prediction = np.expm1(model.predict(matrix(train_rows, features)))
    validation_prediction = np.expm1(model.predict(matrix(validation_rows, features)))
    return train_prediction, validation_prediction, model


def shuffled_rows(rows: list[dict[str, Any]], features: list[str], rng: np.random.Generator) -> list[dict[str, Any]]:
    result = [dict(row) for row in rows]
    permutation = rng.permutation(len(rows))
    for target_index, source_index in enumerate(permutation):
        for feature in features:
            result[target_index][feature] = rows[int(source_index)].get(feature, math.nan)
    return result


def permutation_test(
    train_rows: list[dict[str, Any]], validation_rows: list[dict[str, Any]], base_features: list[str],
    added_features: list[str], base_error: float, actual_improvement: float,
) -> dict[str, Any]:
    rng = np.random.default_rng(SEED + len(base_features) + len(added_features))
    improvements: list[float] = []
    all_features = base_features + added_features
    for index in range(PERMUTATIONS):
        shuffled_train = shuffled_rows(train_rows, added_features, rng)
        shuffled_validation = shuffled_rows(validation_rows, added_features, rng)
        _, prediction, _ = fit_predict(
            shuffled_train, shuffled_validation, all_features, SEED + 1000 + index, trees=160
        )
        error = float(metrics(validation_rows, prediction)["medianIntervalErrorMs"])
        improvements.append(base_error - error)
    p_value = (1 + sum(value >= actual_improvement - EPS for value in improvements)) / (PERMUTATIONS + 1)
    return {
        "repeats": PERMUTATIONS,
        "actualImprovementMs": actual_improvement,
        "nullImprovementMedianMs": float(np.median(improvements)),
        "nullImprovementP90Ms": float(np.quantile(improvements, 0.90)),
        "oneSidedPValue": float(p_value),
    }


def importance(model: Any, features: list[str]) -> list[dict[str, Any]]:
    forest = model.named_steps["randomforestregressor"]
    imputer = model.named_steps["simpleimputer"]
    names = list(features)
    indicators = list(getattr(imputer, "indicator_", None).features_) if getattr(imputer, "indicator_", None) else []
    names.extend(f"missing:{features[index]}" for index in indicators)
    pairs = sorted(zip(names, forest.feature_importances_), key=lambda item: float(item[1]), reverse=True)
    return [{"feature": name, "importance": float(value)} for name, value in pairs[:20]]


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    if prereg.get("contractVersion") != "TARGET_ORDERFLOW_MULTIVIEW_IDENTIFIABILITY_V1":
        raise RuntimeError("unexpected preregistration")
    rows, integrity = build_rows()
    train_rows = [row for row in rows if int(row["market_id"]) in TRAIN_MARKETS]
    validation_rows = [row for row in rows if int(row["market_id"]) in VALIDATION_MARKETS]
    if not train_rows or not validation_rows:
        raise RuntimeError("empty train or validation rows")

    naive_duration = float(np.median([row["duration_midpoint_ms"] for row in train_rows]))
    results: dict[str, Any] = {
        "TRAIN_MEDIAN": {
            "train": metrics(train_rows, np.full(len(train_rows), naive_duration)),
            "validation": metrics(validation_rows, np.full(len(validation_rows), naive_duration)),
        }
    }
    models: dict[str, Any] = {}
    feature_sets = {"RAW_L2": RAW_FEATURES, "PREDICT_ORDERFLOW": PUBLIC_FEATURES, "MULTIVIEW": MULTIVIEW_FEATURES}
    for offset, (name, features) in enumerate(feature_sets.items()):
        train_prediction, validation_prediction, model = fit_predict(
            train_rows, validation_rows, features, SEED + offset
        )
        models[name] = model
        results[name] = {
            "featureCount": len(features),
            "train": metrics(train_rows, train_prediction),
            "validation": metrics(validation_rows, validation_prediction),
            "topFeatureImportances": importance(model, features),
        }

    raw_error = float(results["RAW_L2"]["validation"]["medianIntervalErrorMs"])
    public_error = float(results["PREDICT_ORDERFLOW"]["validation"]["medianIntervalErrorMs"])
    multiview_error = float(results["MULTIVIEW"]["validation"]["medianIntervalErrorMs"])
    public_improvement = raw_error - public_error
    multiview_increment = public_error - multiview_error
    combined_improvement = raw_error - multiview_error
    public_null = permutation_test(
        train_rows, validation_rows, RAW_FEATURES, PUBLIC_ADDED_FEATURES, raw_error, public_improvement
    )
    target_null = permutation_test(
        train_rows, validation_rows, PUBLIC_FEATURES, TARGET_ADDED_FEATURES, public_error, multiview_increment
    )

    integrity_passed = (
        integrity["makerHashParentCoverage"] >= 0.95
        and integrity["makerHashShareAgreementRate"] >= 0.90
        and integrity["makerHashFirstSecondAgreementRate"] >= 0.95
    )
    raw_expanded = float(results["RAW_L2"]["validation"]["expandedPlusMinus1sCoverage"])
    multiview_expanded = float(results["MULTIVIEW"]["validation"]["expandedPlusMinus1sCoverage"])
    combined_gate = (
        raw_error > EPS
        and combined_improvement >= 250.0
        and combined_improvement / raw_error >= 0.20
        and multiview_expanded + EPS >= raw_expanded
    )
    public_gate = (
        raw_error > EPS
        and public_improvement >= 250.0
        and public_improvement / raw_error >= 0.20
        and float(results["PREDICT_ORDERFLOW"]["validation"]["expandedPlusMinus1sCoverage"]) + EPS >= raw_expanded
        and float(public_null["oneSidedPValue"]) <= 0.10
    )
    target_increment_gate = multiview_increment > 0 and float(target_null["oneSidedPValue"]) <= 0.10
    if not integrity_passed:
        decision = "INVALID_DATA_JOIN"
    elif combined_gate and target_increment_gate and float(public_null["oneSidedPValue"]) <= 0.10:
        decision = "NEED_MORE_DATA_MULTIVIEW_SIGNAL"
    elif public_gate and not target_increment_gate:
        decision = "KEEP_PUBLIC_ORDERFLOW_ONLY_REJECT_TARGET_STATE_INCREMENT"
    else:
        decision = "REJECT_CURRENT_MULTIVIEW_IDENTIFIABILITY"

    for row in rows:
        row["split"] = "train" if int(row["market_id"]) in TRAIN_MARKETS else "chronological_validation"
    with ROWS_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "reportVersion": "TARGET_ORDERFLOW_MULTIVIEW_IDENTIFIABILITY_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "hypothesis": prereg["question"],
        "dedupBoundary": prereg["dedupBoundary"],
        "cohort": {
            "trainMarkets": TRAIN_MARKETS,
            "chronologicalValidationMarkets": VALIDATION_MARKETS,
            "trainParents": len(train_rows),
            "validationParents": len(validation_rows),
            "officialHftForward": False,
            "sealedInputs": False,
        },
        "labelSemantics": prereg["label"],
        "strictPastSemantics": {
            "placementBook": "Predict Tape updates through the exact inferred placement receipt/source tuple.",
            "rawMatchFeatures": "Only match buckets with executedAt+999ms <= placement receipt.",
            "inferredTargetState": "Only placement/fill-decrease allocations whose public update receipt <= subject placement; subject parent excluded.",
            "futureInputs": False,
        },
        "integrity": {**integrity, "passed": integrity_passed},
        "featureSets": {
            "RAW_L2": RAW_FEATURES,
            "PREDICT_ORDERFLOW_ADDED": PUBLIC_ADDED_FEATURES,
            "MULTIVIEW_TARGET_ADDED": TARGET_ADDED_FEATURES,
        },
        "model": prereg["model"],
        "metrics": results,
        "comparisons": {
            "predictOrderflowVsRaw": {
                "medianIntervalErrorImprovementMs": public_improvement,
                "relativeImprovement": public_improvement / raw_error if raw_error > EPS else None,
                "permutation": public_null,
                "passed": public_gate,
            },
            "multiviewVsPredictOrderflow": {
                "medianIntervalErrorImprovementMs": multiview_increment,
                "relativeImprovement": multiview_increment / public_error if public_error > EPS else None,
                "permutation": target_null,
                "passed": target_increment_gate,
            },
            "multiviewVsRaw": {
                "medianIntervalErrorImprovementMs": combined_improvement,
                "relativeImprovement": combined_improvement / raw_error if raw_error > EPS else None,
                "expandedCoverageDelta": multiview_expanded - raw_expanded,
                "passed": combined_gate,
            },
        },
        "policyEconomics": {
            "executionSimulator": "NOT_RUN_IDENTIFIABILITY_ONLY",
            "waitActRate": "N/A",
            "oracleValueCeiling": "N/A",
            "learnedPolicyRealizedValue": "N/A",
            "pnl": "N/A",
        },
        "decision": decision,
        "interpretationGuardrails": [
            "Known-to-fill selection means this result cannot estimate eventual fill probability or policy ACT value.",
            "Inferred Target outstanding is an offline noisy observation assembled from public allocations, not private queue truth.",
            "Exact maker hash labels improve historical attribution but arrive with the fill and are never runtime predictors.",
            "No validation threshold, model, feature, or cohort tuning is permitted after this report.",
        ],
        "artifacts": {"report": str(REPORT.resolve()), "parentRows": str(ROWS_CSV.resolve())},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(REPORT.resolve()),
                "cohort": report["cohort"],
                "integrity": report["integrity"],
                "comparisons": report["comparisons"],
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
