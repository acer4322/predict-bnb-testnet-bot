from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import sys
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
HFT_PATH = ROOT / ".tmp" / "hftbacktest_244"
if str(HFT_PATH) not in sys.path:
    sys.path.insert(0, str(HFT_PATH))

import hftbacktest as hbt  # noqa: E402
from hftbacktest import (  # noqa: E402
    BUY_EVENT,
    DEPTH_EVENT,
    DEPTH_SNAPSHOT_EVENT,
    EXCH_EVENT,
    GTX,
    LIMIT,
    LOCAL_EVENT,
    SELL_EVENT,
    TRADE_EVENT,
    BacktestAsset,
    HashMapMarketDepthBacktest,
    event_dtype,
)
from hftbacktest.order import CANCELED, EXPIRED, FILLED, NEW, PARTIALLY_FILLED, REJECTED  # noqa: E402

BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
STRATEGY_DB = ROOT / "data" / "strategy_target_compare_v1.db"
ENGINE_DB = ROOT / "data" / "echtgeld_engine_v1.db"
OUT_DIR = ROOT / "data" / "research" / "hftbacktest_execution_shift_v0"

VERSIONS = {
    "R1": "UNIFIED_PROMOTED_OWNSTATE_V4_R1_FORWARD_PAPER",
    "R2": "UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER",
    "CAP100": "UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_FORWARD_PAPER",
}

STATUS_NAME = {
    0: "NONE",
    int(NEW): "NEW",
    int(EXPIRED): "EXPIRED",
    int(FILLED): "FILLED",
    int(CANCELED): "CANCELED",
    int(PARTIALLY_FILLED): "PARTIALLY_FILLED",
    int(REJECTED): "REJECTED",
}


def dec(blob: bytes | None) -> Any:
    if not blob:
        return None
    return json.loads(zlib.decompress(blob).decode("utf-8"))


def event_row(ev: int, ts_ms: int, px: float, qty: float) -> np.void:
    row = np.zeros(1, event_dtype)[0]
    row["ev"] = int(ev | EXCH_EVENT | LOCAL_EVENT)
    row["exch_ts"] = int(ts_ms) * 1_000_000
    row["local_ts"] = int(ts_ms) * 1_000_000
    row["px"] = float(px)
    row["qty"] = float(qty)
    return row


def build_market_events(book_db: sqlite3.Connection, market_id: int, *, depletion_as_trade: bool) -> tuple[np.ndarray, list[int], dict[str, Any]]:
    rows = book_db.execute(
        """SELECT id,source_timestamp_ms,received_at_ms,is_checkpoint,
                  native_bids_z,native_asks_z,changes_z
             FROM maker_book_inference_updates
            WHERE market_id=? ORDER BY received_at_ms,id""",
        (int(market_id),),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"no book rows for market {market_id}")
    first = next((r for r in rows if int(r["is_checkpoint"] or 0) == 1 and r["native_bids_z"] and r["native_asks_z"]), None)
    if first is None:
        raise RuntimeError(f"no checkpoint for market {market_id}")

    out: list[np.void] = []
    bids = dec(first["native_bids_z"]) or {}
    asks = dec(first["native_asks_z"]) or {}
    for px, qty in bids.items():
        out.append(event_row(DEPTH_SNAPSHOT_EVENT | BUY_EVENT, int(first["received_at_ms"]), float(px), float(qty)))
    for px, qty in asks.items():
        out.append(event_row(DEPTH_SNAPSHOT_EVENT | SELL_EVENT, int(first["received_at_ms"]), float(px), float(qty)))

    update_times = [int(first["received_at_ms"])]
    negative_depth = 0.0
    synthetic_trade_qty = 0.0
    for r in rows:
        if int(r["id"]) <= int(first["id"]):
            continue
        ts = int(r["received_at_ms"])
        update_times.append(ts)
        changes = dec(r["changes_z"]) or {}
        for key, depth_side, trade_side in (
            ("bids", BUY_EVENT, SELL_EVENT),
            ("asks", SELL_EVENT, BUY_EVENT),
        ):
            for ch in changes.get(key, []) or []:
                px = float(ch.get("price"))
                after = max(0.0, float(ch.get("after", 0.0)))
                delta = float(ch.get("delta", 0.0))
                if delta < -1e-12:
                    negative_depth += -delta
                    if depletion_as_trade:
                        q = -delta
                        out.append(event_row(TRADE_EVENT | trade_side, ts, px, q))
                        synthetic_trade_qty += q
                out.append(event_row(DEPTH_EVENT | depth_side, ts, px, after))

    arr = np.asarray(out, dtype=event_dtype)
    arr.sort(order="local_ts")
    return arr, sorted(set(update_times)), {
        "updates": len(rows),
        "events": len(arr),
        "firstReceivedMs": int(first["received_at_ms"]),
        "lastReceivedMs": int(rows[-1]["received_at_ms"]),
        "negativeDepthQty": negative_depth,
        "syntheticTradeQty": synthetic_trade_qty,
        "depletionAsTrade": bool(depletion_as_trade),
        "timestampBasis": "received_at_ms used as both exchange/local replay time because source clock has a material offset versus controller wall-clock",
    }


def new_bt(events: np.ndarray, *, entry_latency_ms: int, response_latency_ms: int, queue_model: str) -> Any:
    asset = (
        BacktestAsset()
        .data(events)
        .linear_asset(1.0)
        .constant_order_latency(int(entry_latency_ms) * 1_000_000, int(response_latency_ms) * 1_000_000)
        .partial_fill_exchange()
        .tick_size(0.01)
        .lot_size(0.01)
    )
    if queue_model == "risk":
        asset = asset.risk_adverse_queue_model()
    elif queue_model == "log":
        asset = asset.log_prob_queue_model()
    else:
        raise ValueError(queue_model)
    return HashMapMarketDepthBacktest([asset])


def initialize_bt(bt: Any) -> None:
    rc = bt.wait_next_feed(False, 1)
    if rc not in (0, 2):
        raise RuntimeError(f"cannot initialize HftBacktest feed rc={rc}")


def advance_to(bt: Any, target_ms: int) -> bool:
    target_ns = int(target_ms) * 1_000_000
    cur = int(bt.current_timestamp)
    if cur > target_ns:
        return True
    if cur == target_ns:
        return True
    return int(bt.elapse(target_ns - cur)) == 0


def native_order(side: str, price: float) -> tuple[str, float]:
    if side == "UP":
        return "BUY", round(float(price), 2)
    return "SELL", round(1.0 - float(price), 2)


def submit_native(bt: Any, order_num: int, side: str, price: float, shares: float) -> int:
    native_side, native_price = native_order(side, price)
    if native_side == "BUY":
        return int(bt.submit_buy_order(0, int(order_num), native_price, float(shares), GTX, LIMIT, False))
    return int(bt.submit_sell_order(0, int(order_num), native_price, float(shares), GTX, LIMIT, False))


def order_snapshot(bt: Any, order_num: int) -> dict[str, Any]:
    order = bt.orders(0).get(int(order_num))
    if order is None:
        return {"exists": False, "status": "NONE", "qty": None, "execQty": 0.0, "cumExecQty": 0.0, "leavesQty": None, "execPrice": None, "exchangeTs": None, "localTs": None}
    return {
        "exists": True,
        "status": STATUS_NAME.get(int(order.status), str(int(order.status))),
        "statusCode": int(order.status),
        "qty": float(order.qty),
        "execQty": float(order.exec_qty),
        "cumExecQty": max(0.0, float(order.qty) - float(order.leaves_qty)),
        "leavesQty": float(order.leaves_qty),
        "execPrice": float(order.exec_price) if int(order.status) in {int(FILLED), int(PARTIALLY_FILLED)} else None,
        "exchangeTs": int(order.exch_timestamp),
        "localTs": int(order.local_timestamp),
    }


def parse_decision_ms(client_order_id: str) -> int:
    parts = str(client_order_id).split(":")
    try:
        return int(parts[-2])
    except Exception as exc:
        raise ValueError(f"cannot parse decision timestamp from {client_order_id}") from exc


def live_calibration_1513668(*, entry_latency_ms: int, response_latency_ms: int, queue_model: str, depletion_as_trade: bool) -> dict[str, Any]:
    market_id = 1513668
    book = sqlite3.connect(BOOK_DB)
    book.row_factory = sqlite3.Row
    engine = sqlite3.connect(ENGINE_DB)
    engine.row_factory = sqlite3.Row
    try:
        events, update_times, feed_meta = build_market_events(book, market_id, depletion_as_trade=depletion_as_trade)
        rows = [dict(r) for r in engine.execute(
            """SELECT * FROM engine_cap100_orders
                WHERE source_market_id=? AND role='MAKER' ORDER BY created_at_ms,client_order_id""",
            (market_id,),
        )]
        live_events = [dict(r) for r in engine.execute(
            """SELECT * FROM engine_cap100_events
                WHERE source_market_id=? AND role='MAKER' ORDER BY occurred_at_ms,seq""",
            (market_id,),
        )]
    finally:
        book.close(); engine.close()

    orders = []
    for i, r in enumerate(rows, start=1):
        orders.append({
            "num": i,
            "clientOrderId": r["client_order_id"],
            "decisionMs": parse_decision_ms(r["client_order_id"]),
            "side": r["side"],
            "price": float(r["requested_price"]),
            "shares": float(r["requested_shares"]),
            "liveState": r["state"],
            "liveFilledShares": float(r["filled_share_qty"] or 0.0),
            "liveAvgFillPrice": r["avg_fill_price"],
            "liveCompletedMs": r["completed_at_ms"],
            "livePlaceCompletedMs": r["place_completed_at_ms"],
        })
    fills_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in live_events:
        if ev["event_type"] == "FILL_DELTA":
            fills_by_id[str(ev["client_order_id"])].append(ev)
    for o in orders:
        fs = fills_by_id.get(o["clientOrderId"], [])
        o["liveFillPath"] = [
            {"ms": int(x["occurred_at_ms"]), "deltaShares": float(x["delta_shares"] or 0.0), "fillPrice": x["fill_price"], "state": x["state"]}
            for x in fs
        ]
        o["liveFirstFillMs"] = int(fs[0]["occurred_at_ms"]) if fs else None
        o["liveLastFillMs"] = int(fs[-1]["occurred_at_ms"]) if fs else None

    bt = new_bt(events, entry_latency_ms=entry_latency_ms, response_latency_ms=response_latency_ms, queue_model=queue_model)
    initialize_bt(bt)
    try:
        timeline = sorted(set(update_times + [int(o["decisionMs"]) for o in orders]))
        by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for o in orders:
            by_time[int(o["decisionMs"])].append(o)
        prev_exec = {int(o["num"]): 0.0 for o in orders}
        transitions: dict[int, list[dict[str, Any]]] = defaultdict(list)
        submitted: set[int] = set()
        for t in timeline:
            if int(bt.current_timestamp) <= int(t) * 1_000_000:
                if not advance_to(bt, t):
                    break
            for num in sorted(submitted):
                snap = order_snapshot(bt, num)
                if snap["cumExecQty"] > prev_exec[num] + 1e-9 or (transitions[num] and snap["status"] != transitions[num][-1]["status"]):
                    transitions[num].append({"atMs": t, **snap})
                elif not transitions[num] and snap["status"] not in {"NONE", "NEW"}:
                    transitions[num].append({"atMs": t, **snap})
                prev_exec[num] = max(prev_exec[num], float(snap["cumExecQty"] or 0.0))
            for o in by_time.get(t, []):
                rc = submit_native(bt, int(o["num"]), str(o["side"]), float(o["price"]), float(o["shares"]))
                o["hftSubmitRc"] = rc
                submitted.add(int(o["num"]))
        # consume remainder
        while True:
            rc = int(bt.wait_next_feed(True, 10_000_000_000))
            if rc == 1:
                break
            t = int(bt.current_timestamp // 1_000_000)
            for num in sorted(submitted):
                snap = order_snapshot(bt, num)
                if snap["cumExecQty"] > prev_exec[num] + 1e-9 or (transitions[num] and snap["status"] != transitions[num][-1]["status"]):
                    transitions[num].append({"atMs": t, **snap})
                elif not transitions[num] and snap["status"] not in {"NONE", "NEW"}:
                    transitions[num].append({"atMs": t, **snap})
                prev_exec[num] = max(prev_exec[num], float(snap["cumExecQty"] or 0.0))
        for o in orders:
            num = int(o["num"])
            o["hftTransitions"] = transitions[num]
            final = order_snapshot(bt, num)
            o["hftFinal"] = final
            fill_transitions = [x for x in transitions[num] if float(x.get("cumExecQty") or 0.0) > 0]
            o["hftFirstFillMs"] = int(fill_transitions[0]["atMs"]) if fill_transitions else None
            full = [x for x in transitions[num] if x.get("status") == "FILLED"]
            o["hftFullFillMs"] = int(full[0]["atMs"]) if full else None
            o["hftFilledShares"] = float(final.get("cumExecQty") or 0.0)
    finally:
        bt.close()

    def err(a: int | None, b: int | None) -> int | None:
        return None if a is None or b is None else int(a - b)
    for o in orders:
        o["firstFillErrorMsHftMinusLive"] = err(o.get("hftFirstFillMs"), o.get("liveFirstFillMs"))
        o["fullFillErrorMsHftMinusLiveLast"] = err(o.get("hftFullFillMs"), o.get("liveLastFillMs"))
        o["filledShareErrorHftMinusLive"] = float(o.get("hftFilledShares") or 0.0) - float(o.get("liveFilledShares") or 0.0)

    matched_first = [abs(int(o["firstFillErrorMsHftMinusLive"])) for o in orders if o.get("firstFillErrorMsHftMinusLive") is not None]
    return {
        "version": "HFTBACKTEST_EXECUTION_SHIFT_AUDIT_V0",
        "mode": "LIVE_CALIBRATION_1513668",
        "marketId": market_id,
        "hftbacktestVersion": getattr(hbt, "__version__", None),
        "config": {"entryLatencyMs": entry_latency_ms, "responseLatencyMs": response_latency_ms, "queueModel": queue_model, "depletionAsTrade": depletion_as_trade},
        "feed": feed_meta,
        "orders": orders,
        "summary": {
            "orders": len(orders),
            "liveFilled": sum(float(o["liveFilledShares"]) > 0 for o in orders),
            "hftFilled": sum(float(o["hftFilledShares"]) > 0 for o in orders),
            "medianAbsFirstFillErrorMs": statistics.median(matched_first) if matched_first else None,
            "hftRejects": sum(o["hftFinal"].get("status") == "REJECTED" for o in orders),
            "liveRejects": sum(o["liveState"] == "REJECTED" for o in orders),
        },
        "guard": "8781 live fills are evaluation-only and are never inserted into HftBacktest feed. depletionAsTrade=true is an explicit optimistic upper-bound because public depth decreases mix trades and cancels.",
    }


def load_strategy_orders(con: sqlite3.Connection, version: str, market_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(
        """SELECT order_id,market_id,side,price,shares,placed_at_ms,status,filled_at_ms,cancelled_at_ms
             FROM our_orders WHERE strategy_version=? AND market_id=? AND channel='MAKER'
             ORDER BY placed_at_ms,order_id""",
        (version, int(market_id)),
    )]


def fixed_decision_market_audit(
    book_con: sqlite3.Connection,
    strat_con: sqlite3.Connection,
    *,
    version: str,
    market_id: int,
    entry_latency_ms: int,
    response_latency_ms: int,
    queue_model: str,
    depletion_as_trade: bool,
) -> dict[str, Any]:
    events, _update_times, feed_meta = build_market_events(book_con, market_id, depletion_as_trade=depletion_as_trade)
    orders = load_strategy_orders(strat_con, version, market_id)
    bt = new_bt(events, entry_latency_ms=entry_latency_ms, response_latency_ms=response_latency_ms, queue_model=queue_model)
    initialize_bt(bt)
    try:
        actions: list[tuple[int, int, str, dict[str, Any]]] = []
        records: dict[int, dict[str, Any]] = {}
        for num, o in enumerate(orders, start=1):
            o = dict(o)
            o["num"] = num
            terminal_ms = o.get("filled_at_ms") if o.get("status") == "FILLED" else o.get("cancelled_at_ms")
            if terminal_ms is None:
                terminal_ms = feed_meta["lastReceivedMs"]
            o["paperTerminalMs"] = int(terminal_ms)
            o["paperFill"] = o.get("status") == "FILLED"
            o["paperLifetimeMs"] = int(o["paperTerminalMs"]) - int(o["placed_at_ms"])
            o["paperFillBeforeMeasuredEntryLatency"] = bool(o["paperFill"] and o["paperLifetimeMs"] < entry_latency_ms)
            records[num] = o
            actions.append((int(o["placed_at_ms"]), 0, "SUBMIT", o))
            actions.append((int(terminal_ms), 1, "CHECK", o))
        actions.sort(key=lambda x: (x[0], x[1], int(x[3]["num"])))
        feed_exhausted = False
        for t, _, kind, o in actions:
            if not feed_exhausted and int(bt.current_timestamp) <= int(t) * 1_000_000:
                if not advance_to(bt, t):
                    feed_exhausted = True
            num = int(o["num"])
            if kind == "SUBMIT":
                o["hftSubmitRc"] = 1 if feed_exhausted else submit_native(bt, num, str(o["side"]), float(o["price"]), float(o["shares"]))
            else:
                snap = order_snapshot(bt, num)
                o["hftAtPaperTerminal"] = snap
                o["hftFillByPaperTerminal"] = float(snap.get("cumExecQty") or 0.0) > 0
                o["hftFullFillByPaperTerminal"] = snap.get("status") == "FILLED"
                # retire the order after the original paper lifecycle boundary; classification is already frozen above.
                cur = bt.orders(0).get(num)
                if (not feed_exhausted) and cur is not None and int(cur.status) in {int(NEW), int(PARTIALLY_FILLED)} and bool(cur.cancellable):
                    bt.cancel(0, num, False)
        result_orders = list(records.values())
    finally:
        bt.close()

    paper_fills = [o for o in result_orders if o.get("paperFill")]
    paper_cancels = [o for o in result_orders if not o.get("paperFill")]
    optimistic_miss = [o for o in paper_fills if not o.get("hftFillByPaperTerminal")]
    full_miss = [o for o in paper_fills if not o.get("hftFullFillByPaperTerminal")]
    hft_only = [o for o in paper_cancels if o.get("hftFillByPaperTerminal")]
    impossible_latency = [o for o in paper_fills if o.get("paperFillBeforeMeasuredEntryLatency")]
    return {
        "marketId": int(market_id),
        "orders": len(result_orders),
        "paperFills": len(paper_fills),
        "paperCancelsOrActiveEnd": len(paper_cancels),
        "hftAnyFillByPaperTerminal": sum(bool(o.get("hftFillByPaperTerminal")) for o in result_orders),
        "hftFullFillByPaperTerminal": sum(bool(o.get("hftFullFillByPaperTerminal")) for o in result_orders),
        "paperFillHftNoAnyFill": len(optimistic_miss),
        "paperFillHftNotFull": len(full_miss),
        "paperCancelHftFill": len(hft_only),
        "paperFillBeforeMeasuredEntryLatency": len(impossible_latency),
        "feed": feed_meta,
        "orderRows": result_orders,
    }


def cohort_audit(label: str, *, entry_latency_ms: int, response_latency_ms: int, queue_model: str, depletion_as_trade: bool, max_markets: int | None) -> dict[str, Any]:
    version = VERSIONS[label]
    book = sqlite3.connect(BOOK_DB); book.row_factory = sqlite3.Row
    strat = sqlite3.connect(STRATEGY_DB); strat.row_factory = sqlite3.Row
    try:
        markets = [int(r[0]) for r in strat.execute(
            "SELECT DISTINCT market_id FROM our_orders WHERE strategy_version=? AND channel='MAKER' ORDER BY market_id",
            (version,),
        )]
        if max_markets is not None:
            markets = markets[: max(0, int(max_markets))]
        rows = []
        errors = []
        for m in markets:
            try:
                rows.append(fixed_decision_market_audit(
                    book, strat, version=version, market_id=m,
                    entry_latency_ms=entry_latency_ms,
                    response_latency_ms=response_latency_ms,
                    queue_model=queue_model,
                    depletion_as_trade=depletion_as_trade,
                ))
            except Exception as exc:
                errors.append({"marketId": m, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        book.close(); strat.close()

    def total(key: str) -> int:
        return sum(int(r.get(key) or 0) for r in rows)
    pf = total("paperFills")
    po = total("orders")
    return {
        "version": "HFTBACKTEST_EXECUTION_SHIFT_AUDIT_V0",
        "mode": "FIXED_DECISION_EXECUTION_PLAUSIBILITY",
        "strategyLabel": label,
        "strategyVersion": version,
        "hftbacktestVersion": getattr(hbt, "__version__", None),
        "config": {"entryLatencyMs": entry_latency_ms, "responseLatencyMs": response_latency_ms, "queueModel": queue_model, "depletionAsTrade": depletion_as_trade},
        "marketsRequested": len(markets),
        "marketsCompleted": len(rows),
        "errors": errors,
        "summary": {
            "orders": po,
            "paperFills": pf,
            "hftAnyFillByPaperTerminal": total("hftAnyFillByPaperTerminal"),
            "hftFullFillByPaperTerminal": total("hftFullFillByPaperTerminal"),
            "paperFillHftNoAnyFill": total("paperFillHftNoAnyFill"),
            "paperFillHftNotFull": total("paperFillHftNotFull"),
            "paperCancelHftFill": total("paperCancelHftFill"),
            "paperFillBeforeMeasuredEntryLatency": total("paperFillBeforeMeasuredEntryLatency"),
            "paperFillAnyHftAgreementRate": (total("hftAnyFillByPaperTerminal") / pf) if pf else None,
            "paperFillFullHftAgreementRate": (total("hftFullFillByPaperTerminal") / pf) if pf else None,
            "latencyImpossibleShareOfPaperFills": (total("paperFillBeforeMeasuredEntryLatency") / pf) if pf else None,
        },
        "markets": rows,
        "interpretationBoundary": "This is phase-1 fixed-decision execution audit. It does not yet rerun the controller on HftBacktest-derived inventory. A paper fill is tested only at its original paper lifecycle deadline. Closed-loop state/timing shift is phase 2.",
        "tradeBoundary": "depletionAsTrade=false is strict depth-only. depletionAsTrade=true labels every negative L2 quantity change as a synthetic trade and is therefore an explicit optimistic upper bound because cancellations/modifications are mixed in.",
    }


def write_report(name: str, payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=True), encoding="utf-8")
    return path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["calibrate", "cohort"], required=True)
    p.add_argument("--strategy", choices=["R1", "R2", "CAP100"])
    p.add_argument("--entry-latency-ms", type=int, default=1092)
    p.add_argument("--response-latency-ms", type=int, default=273)
    p.add_argument("--queue-model", choices=["risk", "log"], default="risk")
    p.add_argument("--depletion-as-trade", action="store_true")
    p.add_argument("--max-markets", type=int)
    args = p.parse_args()

    if args.mode == "calibrate":
        report = live_calibration_1513668(
            entry_latency_ms=args.entry_latency_ms,
            response_latency_ms=args.response_latency_ms,
            queue_model=args.queue_model,
            depletion_as_trade=args.depletion_as_trade,
        )
        tag = "depletion_upper" if args.depletion_as_trade else "strict"
        path = write_report(f"cap100_1513668_hft_calibration_{tag}_{args.queue_model}_lat{args.entry_latency_ms}_resp{args.response_latency_ms}_v0.json", report)
    else:
        if not args.strategy:
            p.error("--strategy is required for cohort mode")
        report = cohort_audit(
            args.strategy,
            entry_latency_ms=args.entry_latency_ms,
            response_latency_ms=args.response_latency_ms,
            queue_model=args.queue_model,
            depletion_as_trade=args.depletion_as_trade,
            max_markets=args.max_markets,
        )
        tag = "depletion_upper" if args.depletion_as_trade else "strict"
        suffix = f"_{args.max_markets}" if args.max_markets else "_full"
        path = write_report(f"{args.strategy.lower()}_fixed_execution_{tag}_{args.queue_model}_lat{args.entry_latency_ms}_resp{args.response_latency_ms}{suffix}_v0.json", report)
    print(json.dumps({"ok": True, "path": str(path), "summary": report.get("summary"), "config": report.get("config")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
