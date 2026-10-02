from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots, load_reference_paper
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from tools.hftbacktest_target_ledger_executor_v0 import r2_passive_quote
from tools import hftbacktest_target_ledger_full_strategy_v0 as base
from src.predict_bot import unified_controller_paper_v2 as mod

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
EPS = 1e-9
TAKER_CONFIRM_MS = 2200
CHUNK = float(mod.SHARES)


def run_recovery(mid: int, enable_intervention: bool = True) -> dict[str, Any]:
    paper = load_reference_paper(mid)
    maker_intents = [
        {"atMs": int(o["placed_at_ms"]), "side": str(o["side"]).upper(), "shares": float(o.get("shares") or CHUNK)}
        for o in paper["orders"]
    ]
    taker_intents = [
        {"atMs": int(x["filled_at_ms"]), "side": str(x["side"]).upper(), "shares": float(x.get("shares") or CHUNK),
         "observedPrice": float(x["price"]), "kind": "FROZEN_R2"}
        for x in paper["takers"] if x.get("filled_at_ms") is not None and x.get("price") is not None
    ]
    maker_intents.sort(key=lambda x: x["atMs"])
    taker_intents.sort(key=lambda x: x["atMs"])
    snaps = load_public_snapshots(mid)
    snap_by_time = {int(z["sampledAtMs"]): z for z in snaps}
    latest_public_snapshot: dict[str, Any] | None = None

    by_maker: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_taker: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for x in maker_intents:
        by_maker[int(x["atMs"])].append(x)
    for x in taker_intents:
        by_taker[int(x["atMs"])].append(x)

    timeline = sorted(set(
        [int(s["sampledAtMs"]) for s in snaps]
        + [int(x["atMs"]) for x in maker_intents]
        + [int(x["atMs"]) for x in taker_intents]
    ))

    events, _, feed_meta = tape_v1.build_archive_events(mid, trade_offset="mid")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    book = mod.PublicBookTailer(mod.BOOK_DB)
    win = winners([mid]).get(mid)

    desired_maker = {"UP": 0.0, "DOWN": 0.0}
    desired_taker = {"UP": 0.0, "DOWN": 0.0}
    actual = mod.Inventory()
    taker_fees = 0.0

    active_maker: dict[str, int | None] = {"UP": None, "DOWN": None}
    maker_meta: dict[int, dict[str, Any]] = {}
    taker_meta: dict[int, dict[str, Any]] = {}
    next_num = 1
    intervention: dict[str, Any] | None = None
    maker_exposure_area = combined_exposure_area = target_error_area = 0.0
    last_t: int | None = None
    last_maker_fill_side: str | None = None
    last_maker_fill_ms: int | None = None

    def harvest_maker(now: int) -> None:
        nonlocal last_maker_fill_side, last_maker_fill_ms
        for side in ("UP", "DOWN"):
            num = active_maker[side]
            if num is None:
                continue
            om = maker_meta[num]
            snap = ex.order_snapshot(bt, num)
            cum = float(snap.get("cumExecQty") or 0.0)
            old = float(om.get("prevCum") or 0.0)
            if cum > old + EPS:
                delta = cum - old
                native_px = snap.get("execPrice")
                px = float(om["price"])
                if native_px is not None and math.isfinite(float(native_px)):
                    px = float(native_px) if side == "UP" else 1.0 - float(native_px)
                fill_ms = int((snap.get("exchangeTs") or now * 1_000_000) // 1_000_000)
                actual.apply({"event_ms": fill_ms, "role": "MAKER", "side": side, "price": px, "shares": delta})
                last_maker_fill_side = side
                last_maker_fill_ms = fill_ms
                om["prevCum"] = cum
            if snap.get("status") in {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}:
                active_maker[side] = None

    def harvest_taker(now: int) -> None:
        nonlocal taker_fees
        for num, om in list(taker_meta.items()):
            snap = ex.order_snapshot(bt, num)
            cum = float(snap.get("cumExecQty") or 0.0)
            old = float(om.get("prevCum") or 0.0)
            if cum > old + EPS:
                delta = cum - old
                native_px = snap.get("execPrice")
                px = float(om["observedPrice"])
                if native_px is not None and math.isfinite(float(native_px)):
                    px = float(native_px) if om["side"] == "UP" else 1.0 - float(native_px)
                fill_ms = int((snap.get("exchangeTs") or now * 1_000_000) // 1_000_000)
                actual.apply({"event_ms": fill_ms, "role": "TAKER", "side": om["side"], "price": px, "shares": delta})
                taker_fees += mod.taker_fee(delta, px, mod.FEE_BPS)
                om["prevCum"] = cum
            if not om.get("cancelRequested") and now >= int(om["expiresAtMs"]):
                if snap.get("status") in {"NEW", "PARTIALLY_FILLED"}:
                    cur = bt.orders(0).get(num)
                    if cur is not None and bool(cur.cancellable):
                        try:
                            bt.cancel(0, num, False)
                            om["cancelRequested"] = True
                        except Exception:
                            pass

    def submit_maker_side(side: str, t: int) -> None:
        nonlocal next_num
        if active_maker[side] is not None:
            return
        actual_side = actual.maker_up if side == "UP" else actual.maker_down
        deficit = desired_maker[side] - actual_side
        if deficit <= EPS:
            return
        opp = "DOWN" if side == "UP" else "UP"
        opp_price = float(maker_meta[int(active_maker[opp])]["price"]) if active_maker[opp] is not None else None
        px = r2_passive_quote(book.book, side, opp_price)
        if px is None:
            return
        qty = min(CHUNK, deficit)
        num = next_num; next_num += 1
        rc = ex.submit_native(bt, num, side, px, qty)
        maker_meta[num] = {"side": side, "price": px, "qty": qty, "prevCum": 0.0, "submittedAtMs": t, "submitRc": int(rc)}
        active_maker[side] = num

    def submit_taker(side: str, shares: float, observed_price: float, t: int, kind: str) -> int:
        nonlocal next_num
        max_price = min(0.99, float(observed_price) + 0.02)
        num = next_num; next_num += 1
        native_side, native_price = ex.native_order(side, max_price)
        if native_side == "BUY":
            rc = int(bt.submit_buy_order(0, num, native_price, float(shares), ex.hbt.GTC, ex.LIMIT, False))
        else:
            rc = int(bt.submit_sell_order(0, num, native_price, float(shares), ex.hbt.GTC, ex.LIMIT, False))
        taker_meta[num] = {
            "side": side, "shares": float(shares), "observedPrice": float(observed_price), "kind": kind,
            "submittedAtMs": int(t), "expiresAtMs": int(t) + TAKER_CONFIRM_MS,
            "prevCum": 0.0, "submitRc": int(rc), "cancelRequested": False,
        }
        return num

    try:
        for t in timeline:
            if t in snap_by_time:
                latest_public_snapshot = snap_by_time[t]
            if int(bt.current_timestamp // 1_000_000) <= t:
                ex.advance_to(bt, t)
            harvest_maker(t)
            harvest_taker(t)

            if last_t is not None:
                dt = max(0, t - last_t) / 1000.0
                maker_exposure_area += abs(actual.maker_up - actual.maker_down) * dt
                combined_net = (actual.maker_up + actual.taker_up) - (actual.maker_down + actual.taker_down)
                combined_exposure_area += abs(combined_net) * dt
                target_net = (desired_maker["UP"] + desired_taker["UP"]) - (desired_maker["DOWN"] + desired_taker["DOWN"])
                target_error_area += abs(combined_net - target_net) * dt
            last_t = t

            book.advance(mid, t)
            for x in by_maker.get(t, []):
                desired_maker[x["side"]] += float(x["shares"])
            for x in by_taker.get(t, []):
                desired_taker[x["side"]] += float(x["shares"])
                submit_taker(x["side"], float(x["shares"]), float(x["observedPrice"]), t, "FROZEN_R2")

            # One-shot execution curriculum probe. Trigger is strategy-chunk tracking error, not winner/PnL.
            combined_up = actual.maker_up + actual.taker_up
            combined_down = actual.maker_down + actual.taker_down
            actual_net = combined_up - combined_down
            target_net = (desired_maker["UP"] + desired_taker["UP"]) - (desired_maker["DOWN"] + desired_taker["DOWN"])
            error = actual_net - target_net
            asymmetric_fill_state = (
                desired_maker["UP"] > EPS and desired_maker["DOWN"] > EPS
                and (actual.maker_up + actual.maker_down) > EPS
                and abs(error) >= CHUNK - EPS
            )
            if enable_intervention and intervention is None and asymmetric_fill_state:
                recovery_side = "DOWN" if error > 0 else "UP"
                bf = mod.outcome_book(book.book, None)
                if bf:
                    ask = float(bf["down_ask"] if recovery_side == "DOWN" else bf["up_ask"])
                    qty = min(CHUNK, abs(error))
                    num = submit_taker(recovery_side, qty, ask, t, "PAIR_COMPLETION_ONE_SHOT")
                    recovery_bid = float(bf["down_bid"] if recovery_side == "DOWN" else bf["up_bid"])
                    recovery_ask = float(bf["down_ask"] if recovery_side == "DOWN" else bf["up_ask"])
                    other_side = "UP" if recovery_side == "DOWN" else "DOWN"
                    other_bid = float(bf["up_bid"] if other_side == "UP" else bf["down_bid"])
                    other_ask = float(bf["up_ask"] if other_side == "UP" else bf["down_ask"])
                    child_num = active_maker[recovery_side]
                    child_features = {
                        "workingRecoveryExists": float(child_num is not None),
                        "workingRecoveryAgeMs": None, "workingRecoveryPrice": None,
                        "workingRecoveryOffsetTicks": None, "workingRecoveryCumExecQty": None,
                        "workingRecoveryRemainingQty": None, "workingRecoveryStatus": None,
                    }
                    if child_num is not None:
                        cm = maker_meta[int(child_num)]
                        cs = ex.order_snapshot(bt, int(child_num))
                        cprice = float(cm["price"])
                        child_features.update({
                            "workingRecoveryAgeMs": int(t) - int(cm["submittedAtMs"]),
                            "workingRecoveryPrice": cprice,
                            "workingRecoveryOffsetTicks": (recovery_bid - cprice) / mod.GRID,
                            "workingRecoveryCumExecQty": float(cs.get("cumExecQty") or 0.0),
                            "workingRecoveryRemainingQty": float(cs.get("leavesQty") or 0.0),
                            "workingRecoveryStatus": str(cs.get("status") or "NONE"),
                        })
                    ps = latest_public_snapshot or {}
                    side_sign = 1.0 if recovery_side == "UP" else -1.0
                    feature_state = {
                        "recoverySideIsUp": float(recovery_side == "UP"),
                        "trackingError": float(error), "absTrackingError": abs(float(error)),
                        "targetNet": float(target_net), "actualNet": float(actual_net),
                        "actualMakerNet": float(actual.maker_up - actual.maker_down),
                        "actualCombinedGross": float(combined_up + combined_down),
                        "desiredMakerUp": float(desired_maker["UP"]), "desiredMakerDown": float(desired_maker["DOWN"]),
                        "recoveryBid": recovery_bid, "recoveryAsk": recovery_ask,
                        "recoverySpreadTicks": (recovery_ask - recovery_bid) / mod.GRID,
                        "otherBid": other_bid, "otherAsk": other_ask,
                        "pairAskSum": recovery_ask + other_ask, "pairBidSum": recovery_bid + other_bid,
                        "secondsLeft": float(ps.get("secondsLeft")) if ps.get("secondsLeft") is not None else None,
                        "directionScore": float(ps.get("directionScore")) if ps.get("directionScore") is not None else None,
                        "spotReturn1sBps": float(ps.get("spotReturn1sBps")) if ps.get("spotReturn1sBps") is not None else None,
                        "spotReturn3sBps": float(ps.get("spotReturn3sBps")) if ps.get("spotReturn3sBps") is not None else None,
                        "spotQueueImbalance": float(ps.get("spotQueueImbalance")) if ps.get("spotQueueImbalance") is not None else None,
                        "spotTakerImbalance1s": float(ps.get("spotTakerImbalance1s")) if ps.get("spotTakerImbalance1s") is not None else None,
                        "futuresReturn1sBps": float(ps.get("futuresReturn1sBps")) if ps.get("futuresReturn1sBps") is not None else None,
                        "futuresReturn3sBps": float(ps.get("futuresReturn3sBps")) if ps.get("futuresReturn3sBps") is not None else None,
                        "futuresQueueImbalance": float(ps.get("futuresQueueImbalance")) if ps.get("futuresQueueImbalance") is not None else None,
                        "futuresTakerImbalance1s": float(ps.get("futuresTakerImbalance1s")) if ps.get("futuresTakerImbalance1s") is not None else None,
                        "directionTowardRecovery": (float(ps.get("directionScore")) * side_sign) if ps.get("directionScore") is not None else None,
                        "spotReturn1sTowardRecovery": (float(ps.get("spotReturn1sBps")) * side_sign) if ps.get("spotReturn1sBps") is not None else None,
                        "spotReturn3sTowardRecovery": (float(ps.get("spotReturn3sBps")) * side_sign) if ps.get("spotReturn3sBps") is not None else None,
                        "spotQueueTowardRecovery": (float(ps.get("spotQueueImbalance")) * side_sign) if ps.get("spotQueueImbalance") is not None else None,
                        "spotTaker1sTowardRecovery": (float(ps.get("spotTakerImbalance1s")) * side_sign) if ps.get("spotTakerImbalance1s") is not None else None,
                        "futuresReturn1sTowardRecovery": (float(ps.get("futuresReturn1sBps")) * side_sign) if ps.get("futuresReturn1sBps") is not None else None,
                        "futuresReturn3sTowardRecovery": (float(ps.get("futuresReturn3sBps")) * side_sign) if ps.get("futuresReturn3sBps") is not None else None,
                        "futuresQueueTowardRecovery": (float(ps.get("futuresQueueImbalance")) * side_sign) if ps.get("futuresQueueImbalance") is not None else None,
                        "futuresTaker1sTowardRecovery": (float(ps.get("futuresTakerImbalance1s")) * side_sign) if ps.get("futuresTakerImbalance1s") is not None else None,
                        "lastMakerFillSideIsRecovery": float(last_maker_fill_side == recovery_side),
                        "lastMakerFillAgeMs": (int(t) - int(last_maker_fill_ms)) if last_maker_fill_ms is not None else None,
                        **child_features,
                    }
                    intervention = {
                        "atMs": int(t), "side": recovery_side, "shares": qty, "observedAsk": ask,
                        "targetNet": target_net, "actualNet": actual_net, "trackingError": error,
                        "orderNum": num, "features": feature_state,
                    }

            for side in ("UP", "DOWN"):
                submit_maker_side(side, t)

        terminal = int(feed_meta["lastReceivedMs"])
        if int(bt.current_timestamp // 1_000_000) <= terminal:
            ex.advance_to(bt, terminal)
        harvest_maker(terminal); harvest_taker(terminal)
        if last_t is not None:
            dt = max(0, terminal - last_t) / 1000.0
            maker_exposure_area += abs(actual.maker_up - actual.maker_down) * dt
            combined_net = (actual.maker_up + actual.taker_up) - (actual.maker_down + actual.taker_down)
            combined_exposure_area += abs(combined_net) * dt
            target_net = (desired_maker["UP"] + desired_taker["UP"]) - (desired_maker["DOWN"] + desired_taker["DOWN"])
            target_error_area += abs(combined_net - target_net) * dt

        combined_up = actual.maker_up + actual.taker_up
        combined_down = actual.maker_down + actual.taker_down
        target_up = desired_maker["UP"] + desired_taker["UP"]
        target_down = desired_maker["DOWN"] + desired_taker["DOWN"]
        payout = combined_up if win == "UP" else combined_down if win == "DOWN" else None
        total_cost = actual.maker_up_cost + actual.maker_down_cost + actual.taker_up_cost + actual.taker_down_cost + taker_fees
        pnl = float(payout - total_cost) if payout is not None else None
        rec = None
        if intervention is not None:
            om = taker_meta[int(intervention["orderNum"])]
            snap = ex.order_snapshot(bt, int(intervention["orderNum"]))
            rec = {**intervention, "filledShares": float(om.get("prevCum") or 0.0), "terminalStatus": snap.get("status")}
        return {
            "marketId": mid, "winner": win, "intervention": rec,
            "targetUp": target_up, "targetDown": target_down,
            "combinedUp": combined_up, "combinedDown": combined_down,
            "finalTrackingError": (combined_up - combined_down) - (target_up - target_down),
            "finalAbsTrackingError": abs((combined_up - combined_down) - (target_up - target_down)),
            "combinedFinalAbsNet": abs(combined_up - combined_down),
            "combinedExposureAreaShareSeconds": combined_exposure_area,
            "targetErrorAreaShareSeconds": target_error_area,
            "realizedPnl": pnl, "takerFeesUsdt": taker_fees,
        }
    finally:
        book.close(); bt.close()


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--market-ids", required=True); a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(",") if x.strip()]
    rows = []
    for i, mid in enumerate(mids, 1):
        b = run_recovery(mid, enable_intervention=False)
        r = run_recovery(mid, enable_intervention=True)
        row = {
            "marketId": mid, "baselinePnl": b["realizedPnl"], "recoveryPnl": r["realizedPnl"],
            "deltaPnl": (float(r["realizedPnl"]) - float(b["realizedPnl"])) if b["realizedPnl"] is not None and r["realizedPnl"] is not None else None,
            "baselineFinalAbsNet": b["combinedFinalAbsNet"], "recoveryFinalAbsNet": r["combinedFinalAbsNet"],
            "deltaFinalAbsNet": float(r["combinedFinalAbsNet"]) - float(b["combinedFinalAbsNet"]),
            "baselineExposureArea": b["combinedExposureAreaShareSeconds"], "recoveryExposureArea": r["combinedExposureAreaShareSeconds"],
            "deltaExposureArea": float(r["combinedExposureAreaShareSeconds"]) - float(b["combinedExposureAreaShareSeconds"]),
            "baselineFinalAbsTrackingError": b["finalAbsTrackingError"], "recoveryFinalAbsTrackingError": r["finalAbsTrackingError"],
            "deltaFinalAbsTrackingError": float(r["finalAbsTrackingError"]) - float(b["finalAbsTrackingError"]),
            "baselineTargetErrorArea": b["targetErrorAreaShareSeconds"], "recoveryTargetErrorArea": r["targetErrorAreaShareSeconds"],
            "deltaTargetErrorArea": float(r["targetErrorAreaShareSeconds"]) - float(b["targetErrorAreaShareSeconds"]),
            "intervention": r["intervention"],
        }
        rows.append(row)
        print(json.dumps({"progress":i,"marketId":mid,"deltaPnl":row["deltaPnl"],"deltaAbsNet":row["deltaFinalAbsNet"],"intervention":row["intervention"]},ensure_ascii=False),flush=True)
    valid = [x for x in rows if x["intervention"] is not None]
    agg = {
        "markets": len(rows), "intervened": len(valid),
        "sumDeltaPnl": sum(float(x["deltaPnl"]) for x in valid if x["deltaPnl"] is not None),
        "beneficialPnlMarkets": sum(float(x["deltaPnl"]) > EPS for x in valid if x["deltaPnl"] is not None),
        "harmfulPnlMarkets": sum(float(x["deltaPnl"]) < -EPS for x in valid if x["deltaPnl"] is not None),
        "sumDeltaExposureArea": sum(float(x["deltaExposureArea"]) for x in valid),
        "sumDeltaFinalAbsNet": sum(float(x["deltaFinalAbsNet"]) for x in valid),
        "sumDeltaTargetErrorArea": sum(float(x["deltaTargetErrorArea"]) for x in valid),
        "sumDeltaFinalAbsTrackingError": sum(float(x["deltaFinalAbsTrackingError"]) for x in valid),
        "trackingBeneficialMarkets": sum(float(x["deltaTargetErrorArea"]) < -EPS for x in valid),
        "trackingHarmfulMarkets": sum(float(x["deltaTargetErrorArea"]) > EPS for x in valid),
        "filledRecoveryShares": sum(float((x["intervention"] or {}).get("filledShares") or 0.0) for x in valid),
    }
    out = OUT / "pair_completion_counterfactual_v0.json"
    out.write_text(json.dumps({
        "version":"PAIR_COMPLETION_COUNTERFACTUAL_V0","researchOnly":True,"liveTradingChanges":False,
        "dreamFillAllowed":False,"strategyIntentSource":"FROZEN_R2_PAPER_INTENT_TAPE",
        "actionSpace":["KEEP_BASELINE","ONE_SHOT_OPPOSITE_TAKER_18_AT_FIRST_ONE_CHUNK_TARGET_NET_ERROR"],
        "labelBoundary":"Primary execution teacher is counterfactual target-error-area improvement; winner/end-PnL is offline diagnostic only. Runtime inputs are strict-past target/actual execution state and public market state.",
        "aggregate":agg,"rows":rows,
    },ensure_ascii=False,indent=2,allow_nan=True),encoding="utf-8")
    print(json.dumps({"ok":True,"report":str(out),"aggregate":agg},ensure_ascii=False))

if __name__ == "__main__":
    main()
