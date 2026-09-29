from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as mod
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_contingent_pair_smoke_v1_preregistered.json"
REPORT = OUT_DIR / "hft_safe_floor_contingent_pair_smoke_v1_report.json"
MARKET_ID = 1573252
QTY = 18.0
GRID = float(mod.GRID)
EPS = 1e-9
POLL_MS = 250
TERMINAL = {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}


def hft_book(bt: Any) -> dict[str, dict[float, float]]:
    depth = bt.depth(0)
    snapshot = depth.snapshot()
    try:
        bids: dict[float, float] = {}
        asks: dict[float, float] = {}
        for row in snapshot:
            event = int(row["ev"])
            price = round(float(row["px"]), 12)
            quantity = float(row["qty"])
            if quantity <= EPS:
                continue
            if event & int(ex.BUY_EVENT):
                bids[price] = quantity
            elif event & int(ex.SELL_EVENT):
                asks[price] = quantity
        return {"bids": bids, "asks": asks}
    finally:
        depth.snapshot_free(snapshot)


def outcome_ask(bt: Any, side: str) -> float | None:
    book = hft_book(bt)
    if side == "UP":
        return min(book["asks"]) if book["asks"] else None
    return 1.0 - max(book["bids"]) if book["bids"] else None


def outcome_fill_price(side: str, native_price: Any, fallback: float) -> float:
    if native_price is None:
        return float(fallback)
    value = float(native_price)
    return value if side == "UP" else 1.0 - value


def selected_checkpoint(market_id: int) -> dict[str, Any]:
    snapshots = [
        row
        for row in load_public_snapshots(market_id)
        if row.get("secondsLeft") is not None
        and float(row["secondsLeft"]) >= 240.0
        and row.get("predictUpBid") is not None
        and row.get("predictDownBid") is not None
    ]
    if not snapshots:
        raise RuntimeError("no strict-past 240s checkpoint")
    return min(snapshots, key=lambda row: float(row["secondsLeft"]) - 240.0)


def strict_past_entry_state(bt: Any, events: Any, checkpoint: dict[str, Any], checkpoint_ms: int, up_price: float, down_price: float) -> dict[str, Any]:
    book = hft_book(bt)
    bids, asks = book["bids"], book["asks"]
    best_bid = max(bids) if bids else math.nan
    best_ask = min(asks) if asks else math.nan
    native_down_price = round(1.0 - down_price, 12)
    out: dict[str, Any] = {
        "secondsLeft": float(checkpoint["secondsLeft"]),
        "predictUpBid": checkpoint.get("predictUpBid"),
        "predictUpAsk": checkpoint.get("predictUpAsk"),
        "predictUpMid": checkpoint.get("predictUpMid"),
        "predictDownBid": checkpoint.get("predictDownBid"),
        "predictDownAsk": checkpoint.get("predictDownAsk"),
        "predictDownMid": checkpoint.get("predictDownMid"),
        "directionScore": checkpoint.get("directionScore"),
        "spotReturn1sBps": checkpoint.get("spotReturn1sBps"),
        "spotReturn3sBps": checkpoint.get("spotReturn3sBps"),
        "spotQueueImbalance": checkpoint.get("spotQueueImbalance"),
        "spotTakerImbalance1s": checkpoint.get("spotTakerImbalance1s"),
        "futuresReturn1sBps": checkpoint.get("futuresReturn1sBps"),
        "futuresReturn3sBps": checkpoint.get("futuresReturn3sBps"),
        "futuresQueueImbalance": checkpoint.get("futuresQueueImbalance"),
        "futuresTakerImbalance1s": checkpoint.get("futuresTakerImbalance1s"),
        "perpSpotBasisBps": checkpoint.get("perpSpotBasisBps"),
        "volatilityAlertHigh": float(str(checkpoint.get("volatilityAlert") or "").upper() not in {"", "NORMAL"}),
        "nativeBestBid": best_bid,
        "nativeBestAsk": best_ask,
        "nativeSpreadTicks": (best_ask - best_bid) / GRID if math.isfinite(best_bid) and math.isfinite(best_ask) else math.nan,
        "nativeBestBidDepth": bids.get(best_bid, math.nan),
        "nativeBestAskDepth": asks.get(best_ask, math.nan),
        "upQuoteDepth": bids.get(round(up_price, 12), 0.0),
        "downQuoteDepth": asks.get(native_down_price, 0.0),
        "upPrice": up_price,
        "downPrice": down_price,
        "pairQuoteSum": up_price + down_price,
        "pairLockedEdgeIfBoth": 1.0 - up_price - down_price,
    }
    checkpoint_ns = checkpoint_ms * 1_000_000
    for seconds in (1, 3, 10):
        buy_qty = sell_qty = 0.0
        count = 0
        lower = checkpoint_ns - seconds * 1_000_000_000
        for event in events:
            local_ns = int(event["local_ts"])
            if local_ns > checkpoint_ns or local_ns <= lower or not (int(event["ev"]) & int(ex.TRADE_EVENT)):
                continue
            quantity = float(event["qty"])
            if int(event["ev"]) & int(ex.BUY_EVENT):
                buy_qty += quantity
            elif int(event["ev"]) & int(ex.SELL_EVENT):
                sell_qty += quantity
            count += 1
        out[f"nativeTradeBuyQty{seconds}s"] = buy_qty
        out[f"nativeTradeSellQty{seconds}s"] = sell_qty
        out[f"nativeTradeImbalance{seconds}s"] = (buy_qty - sell_qty) / (buy_qty + sell_qty) if buy_qty + sell_qty > EPS else 0.0
        out[f"nativeTradeCount{seconds}s"] = count
    return out


def run_offset(
    market_id: int,
    offset: int,
    *,
    completion_policy: str = "SAFE_POSITIVE_FLOOR_ONLY",
    checkpoint_override: dict[str, Any] | None = None,
    events_override: Any | None = None,
    feed_override: dict[str, Any] | None = None,
    winner_audit: bool = True,
) -> dict[str, Any]:
    if completion_policy not in {"SAFE_POSITIVE_FLOOR_ONLY", "FIRST_IMBALANCE_TAKER"}:
        raise ValueError(f"unsupported completion_policy: {completion_policy}")
    checkpoint = dict(checkpoint_override) if checkpoint_override is not None else selected_checkpoint(market_id)
    checkpoint_ms = int(checkpoint["sampledAtMs"])
    up_price = round(float(checkpoint["predictUpBid"]) - offset * GRID, 2)
    down_price = round(float(checkpoint["predictDownBid"]) - offset * GRID, 2)
    if events_override is None:
        events, _, feed = tape_v1.build_archive_events(market_id, trade_offset="mid")
    else:
        if feed_override is None or feed_override.get("lastReceivedMs") is None:
            raise ValueError("feed_override with lastReceivedMs is required for injected events")
        events = events_override
        feed = dict(feed_override)
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    orders: dict[int, dict[str, Any]] = {
        1: {"side": "UP", "role": "MAKER", "price": up_price, "qty": QTY, "prevCum": 0.0, "cancelRequested": False},
        2: {"side": "DOWN", "role": "MAKER", "price": down_price, "qty": QTY, "prevCum": 0.0, "cancelRequested": False},
    }
    fills: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    phase_observations: list[dict[str, Any]] = []
    up = down = cash = taker_fees = 0.0
    pending_safe_completion = False
    taker_submitted = False
    taker_order_num: int | None = None
    first_maker_fill_observed = False
    safe_taker_feasibility_observed = False
    cancel_ack_observed = False
    taker_fill_observed = False
    violations: list[str] = []
    entry_state: dict[str, Any] = {}

    def apply_fill(meta: dict[str, Any], quantity: float, price: float, at_ms: int) -> None:
        nonlocal up, down, cash, taker_fees
        fee = mod.taker_fee(quantity, price, mod.FEE_BPS) if meta["role"] == "TAKER" else 0.0
        if meta["side"] == "UP":
            up += quantity
        else:
            down += quantity
        cash -= price * quantity + fee
        taker_fees += fee
        fills.append(
            {
                "atMs": at_ms,
                "role": meta["role"],
                "side": meta["side"],
                "price": price,
                "shares": quantity,
                "feeUsdt": fee,
            }
        )

    def harvest(now_ms: int) -> None:
        for order_num, meta in orders.items():
            snapshot = ex.order_snapshot(bt, order_num)
            cumulative = float(snapshot.get("cumExecQty") or 0.0)
            previous = float(meta.get("prevCum") or 0.0)
            if cumulative > previous + EPS:
                quantity = cumulative - previous
                price = outcome_fill_price(meta["side"], snapshot.get("execPrice"), float(meta["price"]))
                exchange_ms = int((snapshot.get("exchangeTs") or now_ms * 1_000_000) // 1_000_000)
                apply_fill(meta, quantity, price, exchange_ms)
                meta["prevCum"] = cumulative
                lifecycle.append({"atMs": now_ms, "action": "ACTUAL_FILL", "orderNum": order_num, "role": meta["role"], "side": meta["side"], "deltaShares": quantity})
            meta["lastStatus"] = snapshot.get("status")

    def request_cancel(order_num: int, now_ms: int, reason: str) -> None:
        meta = orders[order_num]
        if meta.get("cancelRequested"):
            return
        snapshot = ex.order_snapshot(bt, order_num)
        if str(snapshot.get("status")) not in {"NEW", "PARTIALLY_FILLED"}:
            return
        order = bt.orders(0).get(order_num)
        if order is not None and bool(order.cancellable):
            bt.cancel(0, order_num, False)
            meta["cancelRequested"] = True
            lifecycle.append({"atMs": now_ms, "action": "CANCEL_REQUESTED", "orderNum": order_num, "side": meta["side"], "reason": reason})

    def maker_terminal() -> bool:
        return all(str(ex.order_snapshot(bt, order_num).get("status")) in TERMINAL for order_num in (1, 2))

    def imbalance() -> tuple[str | None, float]:
        if up > down + EPS:
            return "DOWN", up - down
        if down > up + EPS:
            return "UP", down - up
        return None, 0.0

    def safe_taker_plan(*, require_positive_floor: bool = True) -> dict[str, float | str] | None:
        side, quantity = imbalance()
        if side is None or quantity <= EPS:
            return None
        ask = outcome_ask(bt, side)
        if ask is None:
            return None
        limit_price = min(0.99, round(float(ask) + 2.0 * GRID, 2))
        fee = mod.taker_fee(quantity, limit_price, mod.FEE_BPS)
        projected_up = up + (quantity if side == "UP" else 0.0)
        projected_down = down + (quantity if side == "DOWN" else 0.0)
        projected_cash = cash - limit_price * quantity - fee
        projected_floor = projected_cash + min(projected_up, projected_down)
        if require_positive_floor and projected_floor <= EPS:
            return None
        return {"side": side, "quantity": quantity, "ask": float(ask), "limitPrice": limit_price, "projectedFloorAtLimit": projected_floor}

    def recent_native_trade_flow(now_ms: int) -> dict[str, float | int]:
        out: dict[str, float | int] = {}
        now_ns = now_ms * 1_000_000
        for seconds in (1, 3, 10):
            buy_qty = sell_qty = 0.0
            count = 0
            lower = now_ns - seconds * 1_000_000_000
            for event in events:
                local_ns = int(event["local_ts"])
                if local_ns > now_ns or local_ns <= lower or not (int(event["ev"]) & int(ex.TRADE_EVENT)):
                    continue
                quantity = float(event["qty"])
                if int(event["ev"]) & int(ex.BUY_EVENT):
                    buy_qty += quantity
                elif int(event["ev"]) & int(ex.SELL_EVENT):
                    sell_qty += quantity
                count += 1
            out[f"nativeTradeBuyQty{seconds}s"] = buy_qty
            out[f"nativeTradeSellQty{seconds}s"] = sell_qty
            out[f"nativeTradeImbalance{seconds}s"] = (buy_qty - sell_qty) / (buy_qty + sell_qty) if buy_qty + sell_qty > EPS else 0.0
            out[f"nativeTradeCount{seconds}s"] = count
        return out

    def phase_observation(phase: str, now_ms: int) -> dict[str, Any]:
        book = hft_book(bt)
        bids, asks = book["bids"], book["asks"]
        best_bid = max(bids) if bids else None
        best_ask = min(asks) if asks else None
        deficit_side, deficit_quantity = imbalance()
        opposite_ask = None
        limit_price = None
        projected_floor = None
        taker_feasible = False
        if deficit_side is not None and deficit_quantity > EPS:
            opposite_ask = best_ask if deficit_side == "UP" else (1.0 - best_bid if best_bid is not None else None)
            if opposite_ask is not None:
                limit_price = min(0.99, round(float(opposite_ask) + 2.0 * GRID, 2))
                fee = mod.taker_fee(deficit_quantity, limit_price, mod.FEE_BPS)
                projected_up = up + (deficit_quantity if deficit_side == "UP" else 0.0)
                projected_down = down + (deficit_quantity if deficit_side == "DOWN" else 0.0)
                projected_cash = cash - limit_price * deficit_quantity - fee
                projected_floor = projected_cash + min(projected_up, projected_down)
                taker_feasible = projected_floor > EPS
        if deficit_side is None:
            taker_reason = "BALANCED_NO_COMPLETION_NEEDED"
        elif opposite_ask is None:
            taker_reason = "NO_OPPOSITE_ASK"
        elif taker_feasible:
            taker_reason = "POSITIVE_FLOOR_AT_LIMIT"
        else:
            taker_reason = "NONPOSITIVE_FLOOR_AT_LIMIT"
        maker_orders: dict[str, Any] = {}
        for order_num in (1, 2):
            snapshot = ex.order_snapshot(bt, order_num)
            maker_orders[str(order_num)] = {
                "side": orders[order_num]["side"],
                "status": snapshot.get("status"),
                "cumExecQty": float(snapshot.get("cumExecQty") or 0.0),
                "leavesQty": float(snapshot.get("leavesQty") or 0.0),
                "cancelRequested": bool(orders[order_num].get("cancelRequested")),
            }
        return {
            "phase": phase,
            "observedAtMs": now_ms,
            "ageFromPairSubmitMs": now_ms - checkpoint_ms,
            "actualOwnState": {
                "upShares": up,
                "downShares": down,
                "cash": cash,
                "worstCaseFloor": cash + min(up, down),
                "deficitSide": deficit_side,
                "deficitShares": deficit_quantity,
            },
            "nativeBookState": {
                "bestBid": best_bid,
                "bestAsk": best_ask,
                "spreadTicks": (best_ask - best_bid) / GRID if best_bid is not None and best_ask is not None else None,
                "bestBidDepth": bids.get(best_bid) if best_bid is not None else None,
                "bestAskDepth": asks.get(best_ask) if best_ask is not None else None,
                "queueImbalance": (
                    (bids.get(best_bid, 0.0) - asks.get(best_ask, 0.0)) / (bids.get(best_bid, 0.0) + asks.get(best_ask, 0.0))
                    if best_bid is not None and best_ask is not None and bids.get(best_bid, 0.0) + asks.get(best_ask, 0.0) > EPS
                    else 0.0
                ),
            },
            "completionEconomics": {
                "oppositeOutcomeAsk": opposite_ask,
                "limitPrice": limit_price,
                "projectedFloorAtLimit": projected_floor,
                "safeTakerFeasible": taker_feasible,
                "reason": taker_reason,
            },
            "makerOrders": maker_orders,
            "strictPastTradeFlow": recent_native_trade_flow(now_ms),
        }

    def submit_taker(plan: dict[str, float | str], now_ms: int) -> int:
        side = str(plan["side"])
        price = float(plan["limitPrice"])
        quantity = float(plan["quantity"])
        order_num = 3
        native_side, native_price = ex.native_order(side, price)
        if native_side == "BUY":
            rc = int(bt.submit_buy_order(0, order_num, native_price, quantity, ex.hbt.GTC, ex.LIMIT, False))
        else:
            rc = int(bt.submit_sell_order(0, order_num, native_price, quantity, ex.hbt.GTC, ex.LIMIT, False))
        orders[order_num] = {"side": side, "role": "TAKER", "price": price, "qty": quantity, "prevCum": 0.0, "cancelRequested": False, "submittedAtMs": now_ms, "submitRc": rc}
        lifecycle.append({"atMs": now_ms, "action": "SAFE_TAKER_SUBMITTED", "orderNum": order_num, **plan, "submitRc": rc})
        return order_num

    try:
        ex.advance_to(bt, checkpoint_ms)
        entry_state = strict_past_entry_state(bt, events, checkpoint, checkpoint_ms, up_price, down_price)
        submit_up = ex.submit_native(bt, 1, "UP", up_price, QTY)
        submit_down = ex.submit_native(bt, 2, "DOWN", down_price, QTY)
        lifecycle.append({"atMs": checkpoint_ms, "action": "BALANCED_PAIR_SUBMITTED", "upPrice": up_price, "downPrice": down_price, "submitRc": [submit_up, submit_down]})
        last_ms = int(feed["lastReceivedMs"])
        now_ms = checkpoint_ms
        while now_ms < last_ms:
            now_ms = min(last_ms, now_ms + POLL_MS)
            if not ex.advance_to(bt, now_ms):
                break
            harvest(now_ms)
            if not first_maker_fill_observed and any(fill["role"] == "MAKER" for fill in fills):
                phase_observations.append(phase_observation("FIRST_MAKER_FILL_CONFIRMED", now_ms))
                first_maker_fill_observed = True
            deficit_side, deficit_quantity = imbalance()
            if deficit_side is not None:
                surplus_side = "DOWN" if deficit_side == "UP" else "UP"
                surplus_order = 1 if surplus_side == "UP" else 2
                request_cancel(surplus_order, now_ms, "STOP_SURPLUS_AFTER_ACTUAL_FILL")
                plan_now = safe_taker_plan()
                if completion_policy == "FIRST_IMBALANCE_TAKER" and not taker_submitted:
                    pending_safe_completion = True
                    request_cancel(1, now_ms, "PREPARE_FIRST_IMBALANCE_TAKER")
                    request_cancel(2, now_ms, "PREPARE_FIRST_IMBALANCE_TAKER")
                elif not taker_submitted and plan_now is not None:
                    if not safe_taker_feasibility_observed:
                        phase_observations.append(phase_observation("SAFE_TAKER_FEASIBILITY_ENTERED", now_ms))
                        safe_taker_feasibility_observed = True
                    pending_safe_completion = True
                    request_cancel(1, now_ms, "PREPARE_SAFE_TAKER")
                    request_cancel(2, now_ms, "PREPARE_SAFE_TAKER")
            if pending_safe_completion and not taker_submitted and maker_terminal():
                if not cancel_ack_observed:
                    phase_observations.append(phase_observation("MAKER_CANCEL_ACKS_CONFIRMED", now_ms))
                    cancel_ack_observed = True
                plan = safe_taker_plan(require_positive_floor=completion_policy == "SAFE_POSITIVE_FLOOR_ONLY")
                if plan is not None:
                    taker_order_num = submit_taker(plan, now_ms)
                    taker_submitted = True
                else:
                    pending_safe_completion = False
                    lifecycle.append({"atMs": now_ms, "action": "SAFE_TAKER_ABORTED_AFTER_ACK", "reason": "FLOOR_NO_LONGER_POSITIVE"})
            if taker_submitted and taker_order_num is not None:
                if not taker_fill_observed and any(fill["role"] == "TAKER" for fill in fills):
                    phase_observations.append(phase_observation("TAKER_FILL_CONFIRMED", now_ms))
                    taker_fill_observed = True
                taker_meta = orders[taker_order_num]
                taker_status = str(ex.order_snapshot(bt, taker_order_num).get("status"))
                if now_ms - int(taker_meta["submittedAtMs"]) >= 2200 and taker_status in {"NEW", "PARTIALLY_FILLED"}:
                    request_cancel(taker_order_num, now_ms, "TAKER_CONFIRM_TIMEOUT")
        harvest(last_ms)
        for order_num in list(orders):
            request_cancel(order_num, last_ms, "TAPE_END")
    finally:
        bt.close()

    maker_up = sum(float(fill["shares"]) for fill in fills if fill["role"] == "MAKER" and fill["side"] == "UP")
    maker_down = sum(float(fill["shares"]) for fill in fills if fill["role"] == "MAKER" and fill["side"] == "DOWN")
    taker_up = sum(float(fill["shares"]) for fill in fills if fill["role"] == "TAKER" and fill["side"] == "UP")
    taker_down = sum(float(fill["shares"]) for fill in fills if fill["role"] == "TAKER" and fill["side"] == "DOWN")
    floor = cash + min(up, down)
    best = cash + max(up, down)
    winner = winners([market_id]).get(market_id) if winner_audit else None
    realized_pnl = cash + (up if winner == "UP" else down if winner == "DOWN" else 0.0)
    if taker_submitted and not any(fill["role"] == "TAKER" for fill in fills):
        violations.append("TAKER_SUBMITTED_WITHOUT_FILL")
    return {
        "marketId": market_id,
        "offset": offset,
        "completionPolicy": completion_policy,
        "checkpointMs": checkpoint_ms,
        "secondsLeft": float(checkpoint["secondsLeft"]),
        "initialQuotes": {"up": up_price, "down": down_price, "pairSum": up_price + down_price},
        "strictPastEntryState": entry_state,
        "winnerAuditOnly": winner,
        "actualExecution": {
            "makerUp": maker_up,
            "makerDown": maker_down,
            "takerUp": taker_up,
            "takerDown": taker_down,
            "upShares": up,
            "downShares": down,
            "cash": cash,
            "takerFeesUsdt": taker_fees,
            "worstCaseFloor": floor,
            "bestCasePnl": best,
            "realizedPnl": realized_pnl,
            "terminalState": "SAFE_POSITIVE_FLOOR" if floor > EPS else "DIRECTIONAL_OPTIONALITY" if best > EPS else "DOMINATED_NEGATIVE_BEST" if fills else "NO_FILL_WAIT_EQUIVALENT",
        },
        "lifecycle": lifecycle,
        "phaseObservations": phase_observations,
        "fills": fills,
        "cycleInvariantViolations": violations,
        "cycleInvariantViolationCount": len(violations),
    }


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    rows = [run_offset(MARKET_ID, offset) for offset in (0, 1)]
    valid = [row for row in rows if row["cycleInvariantViolationCount"] == 0]
    best = max(valid, key=lambda row: float(row["actualExecution"]["worstCaseFloor"])) if valid else None
    oracle_floor = max(0.0, float(best["actualExecution"]["worstCaseFloor"])) if best else 0.0
    oracle_action = "WAIT" if oracle_floor <= EPS else f"CONTINGENT_PAIR_OFFSET{int(best['offset'])}"
    keep = bool(
        best
        and best["cycleInvariantViolationCount"] == 0
        and float(best["actualExecution"]["worstCaseFloor"]) > EPS
        and float(best["actualExecution"]["realizedPnl"]) > EPS
    )
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_CONTINGENT_PAIR_SMOKE_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "marketId": MARKET_ID,
        "cohort": "opened development architecture smoke; not chronological OOS",
        "runtimeInputs": {
            "r2Intent": False,
            "targetIntentOrAction": False,
            "paperIntent": False,
            "winnerSettlementOrPnl": False,
            "strictPastPublicBook": True,
            "actualFillOwnState": True,
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": POLL_MS,
            "partialFill": True,
            "cancelAckBeforeTaker": True,
            "dreamFill": False,
        },
        "actionSpace": ["WAIT", "CONTINGENT_PAIR_OFFSET0", "CONTINGENT_PAIR_OFFSET1"],
        "rows": rows,
        "oracle": {
            "action": oracle_action,
            "worstCaseFloor": oracle_floor,
            "actRate": 0.0 if oracle_action == "WAIT" else 1.0,
            "settledRealizedPnlAudit": 0.0 if oracle_action == "WAIT" else float(best["actualExecution"]["realizedPnl"]),
        },
        "learnedPolicyRealizedValue": "N/A one-market architecture smoke",
        "decision": "KEEP_CONTINGENT_SAFE_FLOOR_PROGRAM_FOR_THREE_MARKET_SMOKE" if keep else "REJECT_ONE_SHOT_CONTINGENT_PAIR_FAMILY",
        "decisionReason": (
            "At least one fixed contingent program completed with positive terminal floor, positive settled PnL, and zero lifecycle violations."
            if keep
            else "No fixed contingent program cleared positive terminal floor plus settled PnL with zero lifecycle violations."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
