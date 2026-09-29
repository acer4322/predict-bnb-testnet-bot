from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import HftBookAdapter, load_public_snapshots, load_reference_paper, new_controller
from tools.hftbacktest_target_ledger_executor_v0 import r2_passive_quote
from tools.public_source_snapshot_replay_v2 import load_raw_public_snapshots, assess_raw_public_snapshots
from tools.strategy_input_snapshot_replay_v1 import load_strategy_input_snapshots, assess_strategy_input_snapshots
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from src.predict_bot import unified_controller_paper_v2 as mod

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
EPS = 1e-9
TERMINAL = {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}
TAKER_CONFIRM_MS = 2200


def max_drawdown(xs: list[float]) -> float:
    peak = cur = dd = 0.0
    for x in xs:
        cur += x
        peak = max(peak, cur)
        dd = max(dd, peak - cur)
    return dd


def run_market(mid: int, *, taker_confirm_ms: int = TAKER_CONFIRM_MS) -> dict[str, Any]:
    consumer_quality = assess_strategy_input_snapshots(mid, mod.VERSION)
    raw_quality = assess_raw_public_snapshots(mid)
    if bool(consumer_quality.get("replayFidelityEligible")):
        snaps = load_strategy_input_snapshots(mid, mod.VERSION)
        replay_source = "EXACT_STRATEGY_INPUT_ARCHIVE_V1"
    elif bool(raw_quality.get("complete")):
        snaps = load_raw_public_snapshots(mid)
        replay_source = "RAW_PUBLIC_SOURCE_ARCHIVE_V2_NONEXACT_CONSUMER_FALLBACK"
    else:
        snaps = load_public_snapshots(mid)
        replay_source = "DECISION_ONLY_REPLAY_FALLBACK"
    if not snaps:
        raise RuntimeError(f"no snapshots for {mid}")
    paper = load_reference_paper(mid)
    win = winners([mid]).get(mid)

    a = HftBookAdapter(mid, 1092, 273, "risk", "mid")
    c = new_controller(a)
    actual = mod.Inventory()
    actual_taker_fees = 0.0

    # This ledger is execution bookkeeping only. It never replaces c.inventory.
    desired_maker = {"UP": 0.0, "DOWN": 0.0}
    active_maker: dict[str, int | None] = {"UP": None, "DOWN": None}
    maker_meta: dict[int, dict[str, Any]] = {}
    taker_meta: dict[int, dict[str, Any]] = {}

    strategy_maker_intents: list[dict[str, Any]] = []
    strategy_taker_intents: list[dict[str, Any]] = []
    maker_fill_events: list[dict[str, Any]] = []
    taker_fill_events: list[dict[str, Any]] = []
    taker_attempts: list[dict[str, Any]] = []
    maker_submit_events: list[dict[str, Any]] = []
    taker_cancel_events: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []

    maker_submits = maker_rejects = 0
    taker_submits = taker_rejects = 0
    maker_exposure_area = combined_exposure_area = 0.0
    last_area_ms: int | None = None

    orig_add = c._add_order
    orig_taker = c._record_taker

    def accumulate_area(now: int) -> None:
        nonlocal maker_exposure_area, combined_exposure_area, last_area_ms
        if last_area_ms is not None and now > last_area_ms:
            dt = (now - last_area_ms) / 1000.0
            maker_exposure_area += abs(actual.maker_up - actual.maker_down) * dt
            combined_exposure_area += abs((actual.maker_up + actual.taker_up) - (actual.maker_down + actual.taker_down)) * dt
        if last_area_ms is None or now >= last_area_ms:
            last_area_ms = int(now)

    def harvest_maker(now: int) -> None:
        nonlocal actual_taker_fees
        for side in ("UP", "DOWN"):
            num = active_maker[side]
            if num is None:
                continue
            om = maker_meta[num]
            s = ex.order_snapshot(a.bt, num)
            cum = float(s.get("cumExecQty") or 0.0)
            old = float(om.get("prevCum") or 0.0)
            if cum > old + EPS:
                q = cum - old
                native = s.get("execPrice")
                px = float(om["price"])
                if native is not None and math.isfinite(float(native)):
                    px = float(native) if side == "UP" else 1.0 - float(native)
                fill_ms = int((s.get("exchangeTs") or int(now) * 1_000_000) // 1_000_000)
                actual.apply({"event_ms": fill_ms, "role": "MAKER", "side": side, "price": px, "shares": q})
                om["prevCum"] = cum
                maker_fill_events.append({
                    "atMs": fill_ms, "observedAtMs": int(now), "orderNum": num,
                    "side": side, "price": px, "deltaShares": q, "cumShares": cum,
                    "status": s.get("status"), "targetAtSubmit": float(om["targetAtSubmit"]),
                })
            if str(s.get("status") or "NONE") in TERMINAL:
                active_maker[side] = None
                om["terminalStatus"] = str(s.get("status") or "NONE")
                om["terminalAtMs"] = int(now)

    def harvest_taker(now: int) -> None:
        nonlocal actual_taker_fees
        for num, om in taker_meta.items():
            if om.get("terminal"):
                continue
            s = ex.order_snapshot(a.bt, num)
            cum = float(s.get("cumExecQty") or 0.0)
            old = float(om.get("prevCum") or 0.0)
            if cum > old + EPS:
                q = cum - old
                native = s.get("execPrice")
                px = float(om["observedPrice"])
                if native is not None and math.isfinite(float(native)):
                    px = float(native) if om["side"] == "UP" else 1.0 - float(native)
                fill_ms = int((s.get("exchangeTs") or int(now) * 1_000_000) // 1_000_000)
                actual.apply({"event_ms": fill_ms, "role": "TAKER", "side": om["side"], "price": px, "shares": q})
                fee = mod.taker_fee(q, px, mod.FEE_BPS)
                actual_taker_fees += fee
                om["prevCum"] = cum
                taker_fill_events.append({
                    "atMs": fill_ms, "observedAtMs": int(now), "orderNum": num,
                    "side": om["side"], "price": px, "shares": q, "feeUsdt": fee,
                    "status": s.get("status"), "decisionId": om["decisionId"],
                })
            if str(s.get("status") or "NONE") in TERMINAL:
                om["terminal"] = str(s.get("status") or "NONE")
                om["terminalAtMs"] = int(now)

    def harvest(now: int) -> None:
        harvest_maker(now)
        harvest_taker(now)

    def submit_maker_child(side: str, now: int) -> None:
        nonlocal maker_submits, maker_rejects
        if active_maker[side] is not None:
            return
        actual_side = actual.maker_up if side == "UP" else actual.maker_down
        deficit = desired_maker[side] - actual_side
        if deficit <= EPS:
            return
        opp = "DOWN" if side == "UP" else "UP"
        opp_price = None
        if active_maker[opp] is not None:
            opp_price = float(maker_meta[int(active_maker[opp])]["price"])
        px = r2_passive_quote(c.book.book, side, opp_price)
        if px is None:
            return
        qty = min(float(mod.SHARES), deficit)
        num = int(a.next_num)
        a.next_num += 1
        rc = ex.submit_native(a.bt, num, side, px, qty)
        maker_submits += 1
        if rc != 0:
            maker_rejects += 1
        maker_meta[num] = {
            "side": side, "price": px, "qty": qty, "prevCum": 0.0,
            "submittedAtMs": int(now), "submitRc": int(rc),
            "targetAtSubmit": float(desired_maker[side]), "actualAtSubmit": float(actual_side),
        }
        maker_submit_events.append({"orderNum": num, **maker_meta[num]})
        if rc == 0:
            active_maker[side] = num

    def reconcile_maker(now: int) -> None:
        # Persistent target ledger: a strategy intent updates target only.
        # It never fans out to an independent child while a same-side child is live.
        for side in ("UP", "DOWN"):
            submit_maker_child(side, now)

    def process_taker_deadlines(up_to_ms: int) -> None:
        # Taker is asynchronous here: preserve Strategy Brain cadence; only venue child
        # is canceled when its 2200 ms confirmation horizon has elapsed.
        deadlines = sorted({int(om["deadlineMs"]) for om in taker_meta.values()
                            if not om.get("deadlineProcessed") and int(om["deadlineMs"]) <= int(up_to_ms)})
        for d in deadlines:
            if int(a.bt.current_timestamp // 1_000_000) < d:
                ex.advance_to(a.bt, d)
            accumulate_area(d)
            harvest(d)
            for num, om in taker_meta.items():
                if om.get("deadlineProcessed") or int(om["deadlineMs"]) != d:
                    continue
                s = ex.order_snapshot(a.bt, num)
                om["deadlineProcessed"] = True
                if str(s.get("status") or "NONE") in {"NEW", "PARTIALLY_FILLED"}:
                    cur = a.bt.orders(0).get(num)
                    if cur is not None and bool(cur.cancellable):
                        try:
                            a.bt.cancel(0, num, False)
                            om["cancelRequestedAtMs"] = d
                            taker_cancel_events.append({"atMs": d, "orderNum": num, "side": om["side"], "decisionId": om["decisionId"]})
                        except Exception:
                            pass

    def add_wrap(side: str, now: int, snapshot_ns: int, decision_id: str, reason: str, p: float,
                 snapshot: dict[str, Any], allow_stack: bool = True, bypass_guard: bool = False) -> bool:
        before = set(c.orders)
        made = orig_add(side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack, bypass_guard)
        if not made:
            return False
        new_keys = list(set(c.orders) - before)
        if not new_keys:
            return made
        order = c.orders[new_keys[0]]
        desired_maker[side] += float(order.shares)
        strategy_maker_intents.append({
            "atMs": int(now), "decisionId": decision_id, "intentId": order.id,
            "side": side, "shares": float(order.shares), "paperQuote": float(order.price),
            "reason": reason, "p": float(p), "desiredAfter": float(desired_maker[side]),
        })
        return made

    def taker_wrap(side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any],
                   raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> None:
        nonlocal taker_submits, taker_rejects
        # Preserve frozen Strategy Brain semantics exactly. This paper-side state is not PnL.
        orig_taker(side, price, now, decision_id, snapshot, raw, p1, p3, ppass, pred_effect)
        strategy_taker_intents.append({
            "atMs": int(now), "decisionId": decision_id, "side": side,
            "shares": float(mod.SHARES), "observedPrice": float(price),
        })
        max_price = min(0.99, float(price) + 0.02)
        num = int(a.next_num)
        a.next_num += 1
        native_side, native_price = ex.native_order(side, max_price)
        if native_side == "BUY":
            rc = int(a.bt.submit_buy_order(0, num, native_price, float(mod.SHARES), ex.hbt.GTC, ex.LIMIT, False))
        else:
            rc = int(a.bt.submit_sell_order(0, num, native_price, float(mod.SHARES), ex.hbt.GTC, ex.LIMIT, False))
        taker_submits += 1
        if rc != 0:
            taker_rejects += 1
        taker_meta[num] = {
            "side": side, "observedPrice": float(price), "maxPrice": max_price,
            "qty": float(mod.SHARES), "prevCum": 0.0, "submittedAtMs": int(now),
            "deadlineMs": int(now) + int(taker_confirm_ms), "deadlineProcessed": False,
            "submitRc": int(rc), "decisionId": decision_id, "terminal": None,
        }
        taker_attempts.append({"orderNum": num, **taker_meta[num]})

    c._add_order = add_wrap
    c._record_taker = taker_wrap

    try:
        for s in snaps:
            t = int(s["sampledAtMs"])
            process_taker_deadlines(t)
            # Advance actual venue first so the executor sees real fills before reconciling.
            if int(a.bt.current_timestamp // 1_000_000) < t:
                ex.advance_to(a.bt, t)
            accumulate_area(t)
            harvest(t)

            # Frozen Strategy Brain then advances on its original public/paper semantics.
            c._step(dict(s))
            if c.last_decision is not None and int(c.last_decision.get("decisionMs") or -1) == t:
                decisions.append(copy.deepcopy(c.last_decision))

            # Online strategy intents emitted during this step have already updated desired_maker.
            # Only now may the execution bridge create missing children.
            reconcile_maker(t)

        terminal = int(a.meta["lastReceivedMs"])
        process_taker_deadlines(terminal)
        if int(a.bt.current_timestamp // 1_000_000) < terminal:
            ex.advance_to(a.bt, terminal)
        accumulate_area(terminal)
        harvest(terminal)

        # One final reconcile is diagnostic only; do not create new children after feed terminal.
        strategy_port = c.inventory.features(terminal)
        strategy_port.pop("_combined_net", None)
        actual_port = actual.features(terminal)
        actual_port.pop("_combined_net", None)
    finally:
        a.close()

    maker_desired = desired_maker["UP"] + desired_maker["DOWN"]
    maker_filled = actual.maker_up + actual.maker_down
    taker_requested = sum(float(x["shares"]) for x in strategy_taker_intents)
    taker_filled = actual.taker_up + actual.taker_down
    combined_up = actual.maker_up + actual.taker_up
    combined_down = actual.maker_down + actual.taker_down
    payout = combined_up if win == "UP" else combined_down if win == "DOWN" else None
    total_cost = actual.maker_up_cost + actual.maker_down_cost + actual.taker_up_cost + actual.taker_down_cost + actual_taker_fees
    pnl = float(payout - total_cost) if payout is not None else None

    return {
        "version": "R2_ONLINE_TARGET_LEDGER_V2",
        "researchOnly": True,
        "graduationEligible": False,
        "liveTradingChanges": False,
        "marketId": mid,
        "winner": win,
        "strategyBrain": mod.VERSION,
        "replaySource": replay_source,
        "consumerReplayQuality": consumer_quality,
        "rawReplayQuality": raw_quality,
        "semantics": {
            "strategyAlphaInventory": "FROZEN_R2_ORIGINAL_PAPER_OWN_STATE; NOT REPLACED BY COMMITMENT OR ACTUAL HFT FILLS",
            "onlineTargetLedger": "EVERY ONLINE STRATEGY MAKER PLACEMENT INCREMENTS CUMULATIVE DESIRED SIDE TARGET",
            "makerExecution": "ONE_ACTIVE_HFT_CHILD_PER_SIDE; CHILD SIZE=min(18,target-actual); FIXED_R2_PASSIVE_QUOTE",
            "actualLedger": "HFTBACKTEST_FILLS_ONLY",
            "takerStrategyState": "FROZEN_R2 ORIGINAL PAPER SEMANTICS TO PRESERVE THEORY TRAJECTORY",
            "takerActualExecution": "ASYNC HFT MARKETABLE LIMIT +2C; CANCEL REMAINING AT 2200MS",
            "dreamFillUsedForPnl": False,
            "paperIntentTapeRuntimeInput": False,
            "targetRuntimeInput": False,
        },
        "paperReference": {
            "makerOrders": len(paper["orders"]),
            "makerShares": sum(float(x.get("shares") or 0.0) for x in paper["orders"]),
            "takerFills": len(paper["takers"]),
        },
        "strategyRollout": {
            "decisions": len(decisions),
            "makerIntents": len(strategy_maker_intents),
            "takerIntents": len(strategy_taker_intents),
            "desiredMakerUp": desired_maker["UP"],
            "desiredMakerDown": desired_maker["DOWN"],
            "desiredMakerShares": maker_desired,
            "finalPaperStrategyPortfolio": strategy_port,
            "runMetrics": dict(c.run_metrics),
        },
        "actualExecution": {
            "makerSubmits": maker_submits,
            "makerRejects": maker_rejects,
            "makerFilledShares": maker_filled,
            "makerRealizationRate": maker_filled / maker_desired if maker_desired > EPS else None,
            "takerSubmits": taker_submits,
            "takerRejects": taker_rejects,
            "takerRequestedShares": taker_requested,
            "takerFilledShares": taker_filled,
            "takerRealizationRate": taker_filled / taker_requested if taker_requested > EPS else None,
            "takerFeesUsdt": actual_taker_fees,
            "makerFinalAbsNet": abs(actual.maker_up - actual.maker_down),
            "combinedFinalAbsNet": abs(combined_up - combined_down),
            "makerExposureAreaShareSeconds": maker_exposure_area,
            "combinedExposureAreaShareSeconds": combined_exposure_area,
            "realizedPnl": pnl,
            "finalPortfolio": actual_port,
        },
        "intentCountDeltaVsPaper": len(strategy_maker_intents) - len(paper["orders"]),
        "strategyMakerIntents": strategy_maker_intents,
        "strategyTakerIntents": strategy_taker_intents,
        "makerSubmitEvents": maker_submit_events,
        "makerFillEvents": maker_fill_events,
        "takerAttempts": taker_attempts,
        "takerFillEvents": taker_fill_events,
        "takerCancelEvents": taker_cancel_events,
        "decisionRows": decisions,
        "boundary": "Online target is generated from the frozen Strategy Brain at runtime. Paper/dream fills remain internal only to reproduce the frozen theory student's own-state trajectory and are never counted as actual fills, risk, or PnL. This diagnostic is not graduation-eligible until that remaining internal-paper-state dependency is separately resolved/frozen by contract.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", required=True)
    args = ap.parse_args()
    mids = [int(x) for x in args.market_ids.split(",") if x.strip()]
    rows = []
    for i, mid in enumerate(mids, 1):
        r = run_market(mid)
        rows.append(r)
        print(json.dumps({
            "progress": i,
            "marketId": mid,
            "paperMaker": r["paperReference"]["makerOrders"],
            "onlineMaker": r["strategyRollout"]["makerIntents"],
            "intentDelta": r["intentCountDeltaVsPaper"],
            "makerRealization": r["actualExecution"]["makerRealizationRate"],
            "takerIntents": r["strategyRollout"]["takerIntents"],
            "pnl": r["actualExecution"]["realizedPnl"],
        }, ensure_ascii=False), flush=True)

    pnls = [float(r["actualExecution"]["realizedPnl"]) for r in rows if r["actualExecution"]["realizedPnl"] is not None]
    md = sum(float(r["strategyRollout"]["desiredMakerShares"]) for r in rows)
    mf = sum(float(r["actualExecution"]["makerFilledShares"]) for r in rows)
    tr = sum(float(r["actualExecution"]["takerRequestedShares"]) for r in rows)
    tf = sum(float(r["actualExecution"]["takerFilledShares"]) for r in rows)
    wins_n = sum(x > EPS for x in pnls)
    losses_n = sum(x < -EPS for x in pnls)
    agg = {
        "markets": len(rows),
        "paperMakerOrders": sum(int(r["paperReference"]["makerOrders"]) for r in rows),
        "onlineMakerIntents": sum(int(r["strategyRollout"]["makerIntents"]) for r in rows),
        "intentCountDeltaVsPaper": sum(int(r["intentCountDeltaVsPaper"]) for r in rows),
        "paperTakerFills": sum(int(r["paperReference"]["takerFills"]) for r in rows),
        "onlineTakerIntents": sum(int(r["strategyRollout"]["takerIntents"]) for r in rows),
        "makerDesiredShares": md,
        "makerFilledShares": mf,
        "makerRealizationRate": mf / md if md > EPS else None,
        "takerRequestedShares": tr,
        "takerFilledShares": tf,
        "takerRealizationRate": tf / tr if tr > EPS else None,
        "totalRealizedPnl": sum(pnls),
        "wins": wins_n,
        "losses": losses_n,
        "winRate": wins_n / len(pnls) if pnls else None,
        "maxCumulativeDrawdown": max_drawdown(pnls),
        "meanCombinedFinalAbsNet": sum(float(r["actualExecution"]["combinedFinalAbsNet"]) for r in rows) / len(rows) if rows else None,
        "maxCombinedFinalAbsNet": max((float(r["actualExecution"]["combinedFinalAbsNet"]) for r in rows), default=None),
        "combinedExposureAreaShareSeconds": sum(float(r["actualExecution"]["combinedExposureAreaShareSeconds"]) for r in rows),
        "takerFeesUsdt": sum(float(r["actualExecution"]["takerFeesUsdt"]) for r in rows),
    }
    out = OUT / "r2_online_target_ledger_v2.json"
    out.write_text(json.dumps({
        "version": "R2_ONLINE_TARGET_LEDGER_V2_REPORT",
        "researchOnly": True,
        "graduationEligible": False,
        "liveTradingChanges": False,
        "aggregate": agg,
        "rows": rows,
    }, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(out), "aggregate": agg}, ensure_ascii=False))


if __name__ == "__main__":
    main()
