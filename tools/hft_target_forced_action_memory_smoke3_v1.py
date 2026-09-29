from __future__ import annotations

import bisect
import json
import math
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as mod
from tools import hft_safe_floor_contingent_pair_smoke_v1 as pair_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_target_forced_action_memory_smoke3_v1_preregistered.json"
REPORT = OUT_DIR / "hft_target_forced_action_memory_smoke3_v1_report.json"
ROWS_CSV = OUT_DIR / "hft_target_forced_action_memory_smoke3_v1_states.csv"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
MARKETS = [1569361, 1571387, 1572594]
TRAIN_MARKETS = MARKETS[:2]
VALIDATION_MARKETS = MARKETS[2:]
POLL_MS = 250
GRID_MS = 1000
EPS = 1e-9
TERMINAL = {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}
HEADS = ("MAKER_UP", "MAKER_DOWN", "TAKER_UP", "TAKER_DOWN")

CURRENT_FEATURES = [
    "seconds_left",
    "predict_up_bid",
    "predict_up_ask",
    "predict_down_bid",
    "predict_down_ask",
    "direction_score",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "native_best_bid",
    "native_best_ask",
    "native_spread_ticks",
    "native_best_bid_depth",
    "native_best_ask_depth",
    "own_up_shares",
    "own_down_shares",
    "own_cash",
    "own_worst_case_floor",
    "own_best_case_pnl",
    "own_abs_net",
    "active_maker_orders",
    "active_taker_orders",
    "cancel_pending_orders",
]

MEMORY_FEATURES = [
    "last_own_submit_age_ms",
    "last_own_fill_age_ms",
    "last_own_cancel_age_ms",
    "last_own_action_is_maker",
    "last_own_action_is_taker",
    "last_own_action_is_up",
    "last_own_action_is_down",
    "own_submits_1s",
    "own_submits_5s",
    "own_submits_10s",
    "own_submits_30s",
    "own_maker_submits_5s",
    "own_taker_submits_5s",
    "own_up_submits_5s",
    "own_down_submits_5s",
    "own_fills_1s",
    "own_fills_5s",
    "own_fills_10s",
    "own_fill_shares_5s",
    "own_fill_shares_10s",
    "own_cancel_requests_5s",
    "own_cancel_acks_5s",
    "cumulative_maker_fill_shares",
    "cumulative_taker_fill_shares",
]


def ro(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def load_teacher_actions(market_id: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    book = ro(BOOK_DB)
    target = ro(TARGET_DB)
    try:
        maker_rows = [
            dict(row)
            for row in book.execute(
                """SELECT parent_id,target_side,target_price,expected_parent_shares,
                          placement_first_ms,last_target_ms,confidence,placement_coverage,
                          fill_allocation_coverage
                     FROM maker_book_inference_v21_parent_lifecycles
                    WHERE market_id=? AND placement_first_ms IS NOT NULL
                      AND confidence>=0.60 AND placement_coverage>=0.85
                      AND fill_allocation_coverage>=0.70
                    ORDER BY placement_first_ms,parent_id""",
                (market_id,),
            )
        ]
        official_counts = {
            str(row["role"]): int(row["parents"])
            for row in target.execute(
                "SELECT role,COUNT(*) parents FROM target_parent_orders WHERE market_id=? AND quote_type='BID' GROUP BY role",
                (market_id,),
            )
        }
        taker_rows = [
            dict(row)
            for row in target.execute(
                """SELECT parent_id,side,average_price,shares,first_event_ms,last_event_ms
                     FROM target_parent_orders
                    WHERE market_id=? AND role='TAKER' AND quote_type='BID'
                    ORDER BY first_event_ms,parent_id""",
                (market_id,),
            )
        ]
        actions: list[dict[str, Any]] = []
        for row in maker_rows:
            at_ms = int(row["placement_first_ms"])
            cancel_ms = max(at_ms + 1, int(row["last_target_ms"] or at_ms) + 1)
            actions.append(
                {
                    "teacherId": str(row["parent_id"]),
                    "role": "MAKER",
                    "side": str(row["target_side"]).upper(),
                    "targetPrice": float(row["target_price"]),
                    "quantity": float(row["expected_parent_shares"]),
                    "actionAtMs": at_ms,
                    "cancelAtMs": cancel_ms,
                    "source": "INFERRED_MAKER_PLACEMENT_V21",
                    "confidence": float(row["confidence"]),
                }
            )
        for row in taker_rows:
            at_ms = int(row["first_event_ms"]) - 1
            cancel_ms = max(at_ms + 1, int(row["last_event_ms"] or at_ms) + 1)
            actions.append(
                {
                    "teacherId": str(row["parent_id"]),
                    "role": "TAKER",
                    "side": str(row["side"]).upper(),
                    "targetPrice": float(row["average_price"]),
                    "quantity": float(row["shares"]),
                    "actionAtMs": at_ms,
                    "cancelAtMs": cancel_ms,
                    "source": "OFFICIAL_TAKER_PARENT",
                    "confidence": 1.0,
                }
            )
        actions.sort(key=lambda row: (int(row["actionAtMs"]), str(row["role"]), str(row["teacherId"])))
        audit = {
            "officialMakerParents": official_counts.get("MAKER", 0),
            "reconstructableMakerParents": len(maker_rows),
            "officialTakerParents": official_counts.get("TAKER", 0),
            "teacherActions": len(actions),
        }
        return actions, audit
    finally:
        book.close()
        target.close()


def target_actual_portfolio(market_id: int) -> dict[str, float]:
    target = ro(TARGET_DB)
    try:
        up = down = cash = fees = 0.0
        for row in target.execute(
            """SELECT role,side,average_price,shares FROM target_parent_orders
                WHERE market_id=? AND quote_type='BID'""",
            (market_id,),
        ):
            side = str(row["side"]).upper()
            price = float(row["average_price"])
            shares = float(row["shares"])
            if side == "UP":
                up += shares
            elif side == "DOWN":
                down += shares
            fee = mod.taker_fee(shares, price, mod.FEE_BPS) if str(row["role"]).upper() == "TAKER" else 0.0
            fees += fee
            cash -= price * shares + fee
        return {
            "upShares": up,
            "downShares": down,
            "cash": cash,
            "takerFeesUsdt": fees,
            "worstCaseFloor": cash + min(up, down),
            "bestCasePnl": cash + max(up, down),
        }
    finally:
        target.close()


def nearest_snapshot(snapshots: list[dict[str, Any]], times: list[int], now_ms: int) -> dict[str, Any]:
    index = bisect.bisect_right(times, now_ms) - 1
    return snapshots[index] if index >= 0 else {}


def outcome_bid(bt: Any, side: str) -> float | None:
    book = pair_v1.hft_book(bt)
    if side == "UP":
        return max(book["bids"]) if book["bids"] else None
    return 1.0 - min(book["asks"]) if book["asks"] else None


def submit_order(bt: Any, order_num: int, side: str, price: float, quantity: float) -> int:
    native_side, native_price = ex.native_order(side, price)
    if native_side == "BUY":
        return int(bt.submit_buy_order(0, order_num, native_price, quantity, ex.hbt.GTC, ex.LIMIT, False))
    return int(bt.submit_sell_order(0, order_num, native_price, quantity, ex.hbt.GTC, ex.LIMIT, False))


def replay_market(market_id: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    teacher_actions, source_audit = load_teacher_actions(market_id)
    events, _, feed = tape_v1.build_archive_events(market_id, trade_offset="mid")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    public = sorted(load_public_snapshots(market_id), key=lambda row: int(row["sampledAtMs"]))
    public_times = [int(row["sampledAtMs"]) for row in public]
    first_ms = max(int(events[0]["local_ts"] // 1_000_000), min(public_times) if public_times else 0)
    last_ms = int(feed["lastReceivedMs"])
    teacher_actions = [row for row in teacher_actions if first_ms <= int(row["actionAtMs"]) <= last_ms]
    action_by_time: dict[int, list[dict[str, Any]]] = {}
    for row in teacher_actions:
        action_by_time.setdefault(int(row["actionAtMs"]), []).append(row)
    grid_start = ((first_ms + GRID_MS - 1) // GRID_MS) * GRID_MS
    grid_times = list(range(grid_start, last_ms + 1, GRID_MS))
    poll_start = ((first_ms + POLL_MS - 1) // POLL_MS) * POLL_MS
    poll_times = range(poll_start, last_ms + 1, POLL_MS)
    timeline = sorted(set(poll_times) | set(grid_times) | set(action_by_time) | {int(row["cancelAtMs"]) for row in teacher_actions})

    orders: dict[int, dict[str, Any]] = {}
    own_events: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    next_order_num = 1
    up = down = cash = taker_fees = 0.0
    submit_rejects = 0
    cancellation_requests = 0
    cancellation_acks = 0
    last_status: dict[int, str] = {}

    def apply_fill(meta: dict[str, Any], quantity: float, price: float, exchange_ms: int, observed_ms: int) -> None:
        nonlocal up, down, cash, taker_fees
        fee = mod.taker_fee(quantity, price, mod.FEE_BPS) if meta["role"] == "TAKER" else 0.0
        if meta["side"] == "UP":
            up += quantity
        else:
            down += quantity
        cash -= price * quantity + fee
        taker_fees += fee
        event = {
            "atMs": observed_ms,
            "exchangeAtMs": exchange_ms,
            "kind": "FILL",
            "role": meta["role"],
            "side": meta["side"],
            "shares": quantity,
            "price": price,
            "orderNum": meta["orderNum"],
        }
        own_events.append(event)
        lifecycle.append(event)

    def harvest(now_ms: int) -> None:
        nonlocal cancellation_acks
        for order_num, meta in orders.items():
            snapshot = ex.order_snapshot(bt, order_num)
            cumulative = float(snapshot.get("cumExecQty") or 0.0)
            previous = float(meta.get("prevCum") or 0.0)
            if cumulative > previous + EPS:
                quantity = cumulative - previous
                price = pair_v1.outcome_fill_price(meta["side"], snapshot.get("execPrice"), float(meta["submittedPrice"]))
                exchange_ms = int((snapshot.get("exchangeTs") or now_ms * 1_000_000) // 1_000_000)
                apply_fill(meta, quantity, price, exchange_ms, now_ms)
                meta["prevCum"] = cumulative
            status = str(snapshot.get("status") or "NONE")
            previous_status = last_status.get(order_num)
            if meta.get("cancelRequested") and status == "CANCELED" and previous_status != "CANCELED":
                cancellation_acks += 1
                own_events.append({"atMs": now_ms, "kind": "CANCEL_ACK", "role": meta["role"], "side": meta["side"], "shares": 0.0, "orderNum": order_num})
                lifecycle.append({"atMs": now_ms, "kind": "CANCEL_ACK", "orderNum": order_num})
            last_status[order_num] = status
            meta["lastStatus"] = status

    def request_due_cancels(now_ms: int) -> None:
        nonlocal cancellation_requests
        for order_num, meta in orders.items():
            if meta.get("cancelRequested") or now_ms < int(meta["cancelAtMs"]):
                continue
            snapshot = ex.order_snapshot(bt, order_num)
            if str(snapshot.get("status")) not in {"NEW", "PARTIALLY_FILLED"}:
                continue
            order = bt.orders(0).get(order_num)
            if order is None or not bool(order.cancellable):
                continue
            bt.cancel(0, order_num, False)
            meta["cancelRequested"] = True
            cancellation_requests += 1
            event = {"atMs": now_ms, "kind": "CANCEL_REQUEST", "role": meta["role"], "side": meta["side"], "shares": 0.0, "orderNum": order_num}
            own_events.append(event)
            lifecycle.append(event)

    def submit_teacher_action(row: dict[str, Any], now_ms: int) -> None:
        nonlocal next_order_num, submit_rejects
        side = str(row["side"])
        role = str(row["role"])
        target_price = float(row["targetPrice"])
        quantity = float(row["quantity"])
        if role == "MAKER":
            bid = outcome_bid(bt, side)
            submitted_price = min(target_price, float(bid)) if bid is not None else target_price
        else:
            ask = pair_v1.outcome_ask(bt, side)
            submitted_price = min(0.99, round(float(ask) + 2.0 * float(mod.GRID), 2)) if ask is not None else target_price
        order_num = next_order_num
        next_order_num += 1
        rc = submit_order(bt, order_num, side, submitted_price, quantity)
        if rc != 0:
            submit_rejects += 1
        orders[order_num] = {
            **row,
            "orderNum": order_num,
            "submittedPrice": submitted_price,
            "prevCum": 0.0,
            "cancelRequested": False,
            "submitRc": rc,
        }
        event = {
            "atMs": now_ms,
            "kind": "SUBMIT",
            "role": role,
            "side": side,
            "shares": quantity,
            "orderNum": order_num,
            "teacherId": row["teacherId"],
            "targetPrice": target_price,
            "submittedPrice": submitted_price,
            "submitRc": rc,
        }
        own_events.append(event)
        lifecycle.append(event)

    def event_count(now_ms: int, kind: str, window_ms: int, *, role: str | None = None, side: str | None = None) -> int:
        return sum(
            int(event["kind"] == kind and (role is None or event.get("role") == role) and (side is None or event.get("side") == side))
            for event in own_events
            if now_ms - window_ms < int(event["atMs"]) <= now_ms
        )

    def last_age(now_ms: int, kinds: set[str]) -> float:
        matches = [int(event["atMs"]) for event in own_events if event["kind"] in kinds and int(event["atMs"]) <= now_ms]
        return float(now_ms - max(matches)) if matches else math.nan

    def record_state(now_ms: int) -> None:
        snap = nearest_snapshot(public, public_times, now_ms)
        hbook = pair_v1.hft_book(bt)
        bids, asks = hbook["bids"], hbook["asks"]
        best_bid = max(bids) if bids else math.nan
        best_ask = min(asks) if asks else math.nan
        active_maker = active_taker = cancel_pending = 0
        for order_num, meta in orders.items():
            status = str(ex.order_snapshot(bt, order_num).get("status") or "NONE")
            if status in {"NEW", "PARTIALLY_FILLED"}:
                if meta["role"] == "MAKER":
                    active_maker += 1
                else:
                    active_taker += 1
                cancel_pending += int(bool(meta.get("cancelRequested")))
        submits = [event for event in own_events if event["kind"] == "SUBMIT" and int(event["atMs"]) <= now_ms]
        last_submit = submits[-1] if submits else {}
        maker_fill_shares = sum(float(event.get("shares") or 0.0) for event in own_events if event["kind"] == "FILL" and event["role"] == "MAKER")
        taker_fill_shares = sum(float(event.get("shares") or 0.0) for event in own_events if event["kind"] == "FILL" and event["role"] == "TAKER")
        future = [row for row in teacher_actions if now_ms < int(row["actionAtMs"]) <= now_ms + GRID_MS]
        row: dict[str, Any] = {
            "market_id": market_id,
            "checkpoint_ms": now_ms,
            "seconds_left": float(snap.get("secondsLeft")) if snap.get("secondsLeft") is not None else (last_ms - now_ms) / 1000.0,
            "predict_up_bid": snap.get("predictUpBid"),
            "predict_up_ask": snap.get("predictUpAsk"),
            "predict_down_bid": snap.get("predictDownBid"),
            "predict_down_ask": snap.get("predictDownAsk"),
            "direction_score": snap.get("directionScore"),
            "spot_return_1s_bps": snap.get("spotReturn1sBps"),
            "spot_return_3s_bps": snap.get("spotReturn3sBps"),
            "futures_return_1s_bps": snap.get("futuresReturn1sBps"),
            "futures_return_3s_bps": snap.get("futuresReturn3sBps"),
            "native_best_bid": best_bid,
            "native_best_ask": best_ask,
            "native_spread_ticks": (best_ask - best_bid) / float(mod.GRID) if math.isfinite(best_bid) and math.isfinite(best_ask) else math.nan,
            "native_best_bid_depth": bids.get(best_bid, math.nan) if math.isfinite(best_bid) else math.nan,
            "native_best_ask_depth": asks.get(best_ask, math.nan) if math.isfinite(best_ask) else math.nan,
            "own_up_shares": up,
            "own_down_shares": down,
            "own_cash": cash,
            "own_worst_case_floor": cash + min(up, down),
            "own_best_case_pnl": cash + max(up, down),
            "own_abs_net": abs(up - down),
            "active_maker_orders": float(active_maker),
            "active_taker_orders": float(active_taker),
            "cancel_pending_orders": float(cancel_pending),
            "last_own_submit_age_ms": last_age(now_ms, {"SUBMIT"}),
            "last_own_fill_age_ms": last_age(now_ms, {"FILL"}),
            "last_own_cancel_age_ms": last_age(now_ms, {"CANCEL_REQUEST", "CANCEL_ACK"}),
            "last_own_action_is_maker": float(last_submit.get("role") == "MAKER"),
            "last_own_action_is_taker": float(last_submit.get("role") == "TAKER"),
            "last_own_action_is_up": float(last_submit.get("side") == "UP"),
            "last_own_action_is_down": float(last_submit.get("side") == "DOWN"),
            "own_submits_1s": float(event_count(now_ms, "SUBMIT", 1000)),
            "own_submits_5s": float(event_count(now_ms, "SUBMIT", 5000)),
            "own_submits_10s": float(event_count(now_ms, "SUBMIT", 10000)),
            "own_submits_30s": float(event_count(now_ms, "SUBMIT", 30000)),
            "own_maker_submits_5s": float(event_count(now_ms, "SUBMIT", 5000, role="MAKER")),
            "own_taker_submits_5s": float(event_count(now_ms, "SUBMIT", 5000, role="TAKER")),
            "own_up_submits_5s": float(event_count(now_ms, "SUBMIT", 5000, side="UP")),
            "own_down_submits_5s": float(event_count(now_ms, "SUBMIT", 5000, side="DOWN")),
            "own_fills_1s": float(event_count(now_ms, "FILL", 1000)),
            "own_fills_5s": float(event_count(now_ms, "FILL", 5000)),
            "own_fills_10s": float(event_count(now_ms, "FILL", 10000)),
            "own_fill_shares_5s": sum(float(event.get("shares") or 0.0) for event in own_events if event["kind"] == "FILL" and now_ms - 5000 < int(event["atMs"]) <= now_ms),
            "own_fill_shares_10s": sum(float(event.get("shares") or 0.0) for event in own_events if event["kind"] == "FILL" and now_ms - 10000 < int(event["atMs"]) <= now_ms),
            "own_cancel_requests_5s": float(event_count(now_ms, "CANCEL_REQUEST", 5000)),
            "own_cancel_acks_5s": float(event_count(now_ms, "CANCEL_ACK", 5000)),
            "cumulative_maker_fill_shares": maker_fill_shares,
            "cumulative_taker_fill_shares": taker_fill_shares,
            "teacher_action_count_next_1s": len(future),
            "label_WAIT": int(not future),
        }
        for head in HEADS:
            role, side = head.split("_")
            row[f"label_{head}"] = int(any(action["role"] == role and action["side"] == side for action in future))
        state_rows.append(row)

    try:
        for now_ms in timeline:
            if not ex.advance_to(bt, int(now_ms)):
                break
            harvest(int(now_ms))
            request_due_cancels(int(now_ms))
            for teacher_action in action_by_time.get(int(now_ms), []):
                submit_teacher_action(teacher_action, int(now_ms))
            if int(now_ms) in set(grid_times):
                record_state(int(now_ms))
        harvest(last_ms)
    finally:
        bt.close()

    requested_shares = sum(float(row["quantity"]) for row in teacher_actions)
    filled_shares = sum(float(event.get("shares") or 0.0) for event in own_events if event["kind"] == "FILL")
    action_seconds = len({int(row["actionAtMs"]) // GRID_MS for row in teacher_actions})
    target_portfolio = target_actual_portfolio(market_id)
    report = {
        "marketId": market_id,
        "sourceAudit": source_audit,
        "teacherActionsInTape": len(teacher_actions),
        "teacherActionCounts": dict(Counter(f"{row['role']}_{row['side']}" for row in teacher_actions)),
        "teacherRequestedShares": requested_shares,
        "forcedSubmitAttempts": sum(event["kind"] == "SUBMIT" for event in own_events),
        "forcedSubmitRejects": submit_rejects,
        "forcedActionSeconds": action_seconds,
        "gridRows": len(state_rows),
        "forcedActRateOn1sGrid": action_seconds / len(state_rows) if state_rows else None,
        "forcedWaitRateOn1sGrid": 1.0 - action_seconds / len(state_rows) if state_rows else None,
        "actualFilledShares": filled_shares,
        "fillRealizationRate": filled_shares / requested_shares if requested_shares > EPS else None,
        "makerFilledShares": sum(float(event.get("shares") or 0.0) for event in own_events if event["kind"] == "FILL" and event["role"] == "MAKER"),
        "takerFilledShares": sum(float(event.get("shares") or 0.0) for event in own_events if event["kind"] == "FILL" and event["role"] == "TAKER"),
        "cancelRequests": cancellation_requests,
        "cancelAcks": cancellation_acks,
        "finalOwnPortfolio": {
            "upShares": up,
            "downShares": down,
            "cash": cash,
            "takerFeesUsdt": taker_fees,
            "worstCaseFloor": cash + min(up, down),
            "bestCasePnl": cash + max(up, down),
        },
        "targetActualPortfolioAudit": target_portfolio,
        "lifecycle": lifecycle,
    }
    return report, state_rows


def fit_head(train: pd.DataFrame, validation: pd.DataFrame, features: list[str], label: str) -> dict[str, Any]:
    y_train = train[label].astype(int).to_numpy()
    y_validation = validation[label].astype(int).to_numpy()
    if len(np.unique(y_train)) < 2:
        return {"status": "UNTRAINABLE_SINGLE_CLASS", "trainPositives": int(y_train.sum()), "validationPositives": int(y_validation.sum())}
    model = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=1.0, class_weight="balanced", max_iter=1000, random_state=20260823)),
        ]
    )
    model.fit(train[features], y_train)
    probabilities = model.predict_proba(validation[features])[:, 1]
    output: dict[str, Any] = {
        "status": "OK",
        "features": len(features),
        "trainRows": len(train),
        "trainPositives": int(y_train.sum()),
        "validationRows": len(validation),
        "validationPositives": int(y_validation.sum()),
        "validationPositiveRate": float(y_validation.mean()) if len(y_validation) else None,
        "averagePrecision": float(average_precision_score(y_validation, probabilities)) if int(y_validation.sum()) > 0 else None,
        "brier": float(brier_score_loss(y_validation, probabilities)) if len(y_validation) else None,
        "rocAuc": float(roc_auc_score(y_validation, probabilities)) if len(np.unique(y_validation)) == 2 else None,
        "predictedMean": float(probabilities.mean()) if len(probabilities) else None,
    }
    return output


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    market_reports: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for index, market_id in enumerate(MARKETS, 1):
        market_report, rows = replay_market(market_id)
        market_reports.append(market_report)
        all_rows.extend(rows)
        print(
            json.dumps(
                {
                    "progress": index,
                    "marketId": market_id,
                    "teacherActions": market_report["teacherActionsInTape"],
                    "fillRealizationRate": market_report["fillRealizationRate"],
                    "floor": market_report["finalOwnPortfolio"]["worstCaseFloor"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    frame = pd.DataFrame(all_rows).sort_values(["market_id", "checkpoint_ms"], kind="stable")
    frame.to_csv(ROWS_CSV, index=False)
    train = frame[frame["market_id"].astype(int).isin(TRAIN_MARKETS)].copy()
    validation = frame[frame["market_id"].astype(int).isin(VALIDATION_MARKETS)].copy()
    variants: dict[str, Any] = {"currentOnly": {}, "currentPlusOwnActionMemory": {}}
    for head in HEADS:
        variants["currentOnly"][head] = fit_head(train, validation, CURRENT_FEATURES, f"label_{head}")
        variants["currentPlusOwnActionMemory"][head] = fit_head(train, validation, CURRENT_FEATURES + MEMORY_FEATURES, f"label_{head}")

    def macro(metric: str, variant: str) -> float | None:
        values = [
            float(row[metric])
            for row in variants[variant].values()
            if row.get("status") == "OK" and row.get(metric) is not None and int(row.get("validationPositives") or 0) > 0
        ]
        return float(np.mean(values)) if values else None

    current_ap = macro("averagePrecision", "currentOnly")
    memory_ap = macro("averagePrecision", "currentPlusOwnActionMemory")
    ap_delta = memory_ap - current_ap if memory_ap is not None and current_ap is not None else None
    aggregate_floor = sum(float(row["finalOwnPortfolio"]["worstCaseFloor"]) for row in market_reports)
    wait_inclusive_oracle = sum(max(0.0, float(row["finalOwnPortfolio"]["worstCaseFloor"])) for row in market_reports)
    positive_markets = sum(float(row["finalOwnPortfolio"]["worstCaseFloor"]) > EPS for row in market_reports)
    aggregate_fill_realization = sum(float(row["actualFilledShares"]) for row in market_reports) / max(
        EPS, sum(float(row["teacherRequestedShares"]) for row in market_reports)
    )
    keep = bool(
        aggregate_floor > EPS
        and positive_markets >= 2
        and aggregate_fill_realization > 0.0
        and ap_delta is not None
        and ap_delta >= 0.03 - EPS
    )
    replay_informative = aggregate_fill_realization > 0.0 and len(frame) > 0
    decision = (
        "KEEP_FOR_AUTOREGRESSIVE_CLOSED_LOOP_SMOKE"
        if keep
        else "NEED_MORE_DATA_FORCED_REPLAY_MEMORY"
        if replay_informative and wait_inclusive_oracle > EPS
        else "REJECT_DIRECT_TARGET_FORCED_IMITATION_UNDER_HFT"
    )
    report = {
        "reportVersion": "HFT_TARGET_FORCED_ACTION_MEMORY_SMOKE3_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "hypothesis": "Teacher-forcing every reconstructable Target Maker/Taker parent through the existing HftBacktest executor creates a viable own-fill trajectory, and strict-past memory of our submitted/filled/canceled actions improves chronological prediction of the next Target behavior.",
        "dedupBoundary": {
            "studentStateSupervisor": "Used Target future action as a label on OUR autonomous trajectory; it did not execute each Target action into HftBacktest.",
            "daggerLikeCorrective": "Predicted Target next-1s Maker from target-blind OUR HFT states; no teacher-forced HFT roll-in.",
            "thisTest": "Target historical events force actual HftBacktest submissions; memory and inventory come only from our resulting fills/cancels.",
        },
        "cohort": {
            "markets": MARKETS,
            "trainMarkets": TRAIN_MARKETS,
            "chronologicalValidationMarkets": VALIDATION_MARKETS,
            "openedDevelopmentOnly": True,
            "officialHftForward": False,
            "special20260816": False,
            "supervisorFinal75To99": False,
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "actualOwnStatePollMs": POLL_MS,
            "partialFill": True,
            "cancelAck": True,
            "makerPrice": "Target price capped at current passive outcome bid to preserve Maker role",
            "takerPrice": "current outcome ask plus 2 ticks, capped at 0.99",
            "dreamFill": False,
        },
        "teacherBoundary": {
            "maker": "Only reconstructable V2.1 parents with placement time, confidence>=0.60, placement coverage>=0.85 and fill-allocation coverage>=0.70.",
            "taker": "Official parent role/side/size; action checkpoint is 1ms before second-granular first executedAt.",
            "runtimePromotion": False,
            "targetFutureActionRuntimeInput": True,
            "note": "This is deliberately teacher-forced historical research and is not deployable until an autonomous model removes Target events.",
        },
        "marketReports": market_reports,
        "aggregate": {
            "stateRows": len(frame),
            "teacherActions": sum(int(row["teacherActionsInTape"]) for row in market_reports),
            "forcedActRate": sum(float(row["forcedActRateOn1sGrid"]) * int(row["gridRows"]) for row in market_reports) / max(1, sum(int(row["gridRows"]) for row in market_reports)),
            "forcedWaitRate": sum(float(row["forcedWaitRateOn1sGrid"]) * int(row["gridRows"]) for row in market_reports) / max(1, sum(int(row["gridRows"]) for row in market_reports)),
            "fillRealizationRate": aggregate_fill_realization,
            "terminalWorstCaseFloor": aggregate_floor,
            "positiveFloorMarkets": positive_markets,
            "waitInclusiveOracleCeiling": wait_inclusive_oracle,
        },
        "memoryPrediction": {
            "variants": variants,
            "currentOnlyMacroAveragePrecision": current_ap,
            "currentPlusMemoryMacroAveragePrecision": memory_ap,
            "memoryMacroAveragePrecisionDelta": ap_delta,
            "oneStepOnly": True,
            "autonomousClosedLoopRealizedValue": "N/A not run unless forced-replay and memory gates pass",
        },
        "lockedGate": {
            "aggregateForcedFloorPositive": aggregate_floor > EPS,
            "positiveFloorMarketsAtLeast2Of3": positive_markets >= 2,
            "nonzeroActualFillRealization": aggregate_fill_realization > 0.0,
            "memoryMacroAveragePrecisionDeltaAtLeast003": ap_delta is not None and ap_delta >= 0.03 - EPS,
        },
        "decision": decision,
        "nextStep": (
            "Freeze the memory features and run a one-market autonomous scheduled-sampling closed-loop smoke with formal WAIT."
            if keep
            else "Do not scale or tune imitation thresholds; inspect whether failure is execution non-realization, negative terminal floor, or no memory transfer."
        ),
        "artifacts": {"statesCsv": str(ROWS_CSV.resolve())},
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(REPORT),
                "decision": decision,
                "aggregate": report["aggregate"],
                "memory": {key: report["memoryPrediction"][key] for key in ("currentOnlyMacroAveragePrecision", "currentPlusMemoryMacroAveragePrecision", "memoryMacroAveragePrecisionDelta")},
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
