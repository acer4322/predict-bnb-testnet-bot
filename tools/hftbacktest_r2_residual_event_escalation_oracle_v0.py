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


def run_recovery(mid: int, enable_intervention: bool = False, candidate_delay_ms: int = 0, passive_priority: bool = False, event_escalation: str | None = None) -> dict[str, Any]:
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
    pending_escalation: dict[str, Any] | None = None
    route_escalation_side: str | None = None
    priority_recovery_side: str | None = None
    candidate_at_ms: int | None = None
    asymmetry_since_ms: int | None = None
    candidate_asymmetry_since_ms: int | None = None
    candidate_recovery_side: str | None = None
    candidate_qty: float = 0.0
    candidate_recovery_ask: float | None = None
    candidate_original_child_num: int | None = None
    candidate_original_child_cum: float = 0.0
    local_horizons_ms = (5000, 10000, 20000)
    local_target_error_area = {h: 0.0 for h in local_horizons_ms}
    local_horizon_recovery_ask: dict[int, float | None] = {h: None for h in local_horizons_ms}
    fill_log: list[dict[str, Any]] = []
    maker_exposure_area = combined_exposure_area = target_error_area = 0.0
    last_t: int | None = None
    last_maker_fill_side: str | None = None
    last_maker_fill_ms: int | None = None
    priority_started_ms: int | None = None
    priority_child_seen = False
    event_trigger_at_ms: int | None = None

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
                fill_log.append({"eventMs": fill_ms, "role": "MAKER", "side": side, "price": px, "shares": delta, "fee": 0.0, "orderNum": int(num)})
                last_maker_fill_side = side
                last_maker_fill_ms = fill_ms
                om["prevCum"] = cum
            if snap.get("status") in {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}:
                active_maker[side] = None

    def harvest_taker(now: int) -> None:
        nonlocal taker_fees, route_escalation_side
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
                fee_delta = mod.taker_fee(delta, px, mod.FEE_BPS)
                taker_fees += fee_delta
                fill_log.append({"eventMs": fill_ms, "role": "TAKER", "side": om["side"], "price": px, "shares": delta, "fee": fee_delta})
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
            if om.get("kind") == "PAIR_COMPLETION_REPLACE" and snap.get("status") in {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}:
                if route_escalation_side == om.get("side"):
                    route_escalation_side = None

    def submit_maker_side(side: str, t: int) -> None:
        nonlocal next_num
        if route_escalation_side == side:
            return
        if passive_priority and priority_recovery_side is not None and side != priority_recovery_side:
            return
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
                err_abs = abs(combined_net - target_net)
                target_error_area += err_abs * dt
                if candidate_at_ms is not None:
                    for h in local_horizons_ms:
                        seg_start = max(int(last_t), int(candidate_at_ms))
                        seg_end = min(int(t), int(candidate_at_ms) + int(h))
                        if seg_end > seg_start:
                            local_target_error_area[h] += err_abs * (seg_end - seg_start) / 1000.0
            last_t = t

            book.advance(mid, t)
            if candidate_at_ms is not None and candidate_recovery_side is not None:
                bf_h = mod.outcome_book(book.book, None)
                if bf_h:
                    ask_h = float(bf_h["up_ask"] if candidate_recovery_side == "UP" else bf_h["down_ask"])
                    for h in local_horizons_ms:
                        if local_horizon_recovery_ask[h] is None and int(t) >= int(candidate_at_ms) + int(h):
                            local_horizon_recovery_ask[h] = ask_h
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
            if asymmetric_fill_state:
                if asymmetry_since_ms is None:
                    asymmetry_since_ms = int(t)
            else:
                asymmetry_since_ms = None
            candidate_ready = asymmetric_fill_state and asymmetry_since_ms is not None and int(t) - int(asymmetry_since_ms) >= int(candidate_delay_ms)
            if passive_priority:
                desired_net_now = (desired_maker["UP"] + desired_taker["UP"]) - (desired_maker["DOWN"] + desired_taker["DOWN"])
                actual_net_now = (actual.maker_up + actual.taker_up) - (actual.maker_down + actual.taker_down)
                err_now = actual_net_now - desired_net_now
                needed_side = "DOWN" if err_now > EPS else "UP" if err_now < -EPS else None
                if priority_recovery_side is not None and (needed_side is None or needed_side != priority_recovery_side):
                    priority_recovery_side = None
                if priority_recovery_side is None and candidate_ready:
                    priority_recovery_side = "DOWN" if error > 0 else "UP"
                    if priority_started_ms is None:
                        priority_started_ms = int(t)
            escalation_ready = False
            if enable_intervention and event_escalation is not None and priority_recovery_side is not None:
                _rs = str(priority_recovery_side)
                _rn = active_maker.get(_rs)
                if _rn is not None:
                    priority_child_seen = True
                    _rss = ex.order_snapshot(bt, int(_rn))
                    _cum = float(_rss.get("cumExecQty") or 0.0)
                    _leaves = float(_rss.get("leavesQty") or 0.0)
                    if event_escalation == "FIRST_PARTIAL" and _cum > EPS and _leaves > EPS:
                        escalation_ready = True
                    elif event_escalation == "NO_PROGRESS_5S" and priority_started_ms is not None and int(t) - int(priority_started_ms) >= 5000 and _cum <= EPS:
                        escalation_ready = True
                elif priority_child_seen and event_escalation == "CHILD_DISAPPEARED":
                    escalation_ready = True
                if escalation_ready and event_trigger_at_ms is None:
                    event_trigger_at_ms = int(t)

            if candidate_at_ms is None and candidate_ready:
                bf0 = mod.outcome_book(book.book, None)
                if bf0:
                    candidate_at_ms = int(t)
                    candidate_asymmetry_since_ms = int(asymmetry_since_ms) if asymmetry_since_ms is not None else int(t)
                    candidate_recovery_side = "DOWN" if error > 0 else "UP"
                    candidate_qty = min(CHUNK, abs(error))
                    candidate_recovery_ask = float(bf0["up_ask"] if candidate_recovery_side == "UP" else bf0["down_ask"])
                    candidate_original_child_num = active_maker[candidate_recovery_side]
                    if candidate_original_child_num is not None:
                        _cs0 = ex.order_snapshot(bt, int(candidate_original_child_num))
                        candidate_original_child_cum = float(_cs0.get("cumExecQty") or 0.0)
            if enable_intervention and intervention is None and ((event_escalation is None and candidate_ready and candidate_at_ms == int(t)) or (event_escalation is not None and escalation_ready)):
                recovery_side = "DOWN" if error > 0 else "UP"
                bf = mod.outcome_book(book.book, None)
                if bf:
                    ask = float(bf["down_ask"] if recovery_side == "DOWN" else bf["up_ask"])
                    qty = min(CHUNK, abs(error))
                    num = None
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
                    # Strict-past economics of the currently unmatched/surplus chunk.
                    # Use the most recent opposite-side fills up to one strategy chunk, not full-position average.
                    surplus_side = other_side
                    need_chunk = float(qty)
                    chunk_cost = 0.0
                    chunk_shares = 0.0
                    for ev0 in reversed(fill_log):
                        if str(ev0.get("side")) != surplus_side or need_chunk <= EPS:
                            continue
                        q0 = min(need_chunk, float(ev0.get("shares") or 0.0))
                        if q0 <= EPS:
                            continue
                        fee_ps0 = float(ev0.get("fee") or 0.0) / max(float(ev0.get("shares") or 0.0), EPS)
                        chunk_cost += q0 * float(ev0.get("price") or 0.0) + q0 * fee_ps0
                        chunk_shares += q0
                        need_chunk -= q0
                    marginal_surplus_avg_cost = (chunk_cost / chunk_shares) if chunk_shares > EPS else None
                    recovery_fee_per_share = mod.taker_fee(1.0, recovery_ask, mod.FEE_BPS)
                    locked_pair_edge_per_share = (1.0 - marginal_surplus_avg_cost - recovery_ask - recovery_fee_per_share) if marginal_surplus_avg_cost is not None else None
                    # Strict-past execution-regime state. This describes how well the market has
                    # actually been completing our Maker targets so far; it is not future fill data.
                    actual_maker_up = float(actual.maker_up)
                    actual_maker_down = float(actual.maker_down)
                    actual_maker_gross = actual_maker_up + actual_maker_down
                    desired_maker_gross = float(desired_maker["UP"] + desired_maker["DOWN"])
                    recovery_actual_maker = actual_maker_up if recovery_side == "UP" else actual_maker_down
                    recovery_desired_maker = float(desired_maker[recovery_side])
                    other_actual_maker = actual_maker_up if other_side == "UP" else actual_maker_down
                    other_desired_maker = float(desired_maker[other_side])
                    maker_submitted_shares_total = float(sum(float(m.get("qty") or 0.0) for m in maker_meta.values()))
                    maker_fill_events = [ev1 for ev1 in fill_log if str(ev1.get("role")) == "MAKER" and int(ev1.get("eventMs") or 0) <= int(t)]
                    def recent_fill_shares(ms: int, side: str | None = None) -> float:
                        lo = int(t) - int(ms)
                        return float(sum(float(ev1.get("shares") or 0.0) for ev1 in maker_fill_events if int(ev1.get("eventMs") or 0) >= lo and (side is None or str(ev1.get("side")) == side)))
                    def recent_submit_shares(ms: int) -> float:
                        lo = int(t) - int(ms)
                        return float(sum(float(m.get("qty") or 0.0) for m in maker_meta.values() if int(m.get("submittedAtMs") or 0) >= lo and int(m.get("submittedAtMs") or 0) <= int(t)))
                    mf5, mf10, mf20 = recent_fill_shares(5000), recent_fill_shares(10000), recent_fill_shares(20000)
                    ms5, ms10, ms20 = recent_submit_shares(5000), recent_submit_shares(10000), recent_submit_shares(20000)
                    feature_state = {
                        "recoverySideIsUp": float(recovery_side == "UP"),
                        "trackingError": float(error), "absTrackingError": abs(float(error)),
                        "targetNet": float(target_net), "actualNet": float(actual_net),
                        "actualMakerNet": float(actual.maker_up - actual.maker_down),
                        "actualMakerUp": actual_maker_up, "actualMakerDown": actual_maker_down,
                        "actualMakerGross": actual_maker_gross,
                        "actualCombinedGross": float(combined_up + combined_down),
                        "desiredMakerUp": float(desired_maker["UP"]), "desiredMakerDown": float(desired_maker["DOWN"]),
                        "desiredMakerGross": desired_maker_gross,
                        "makerTargetRealizationRatio": (actual_maker_gross / desired_maker_gross) if desired_maker_gross > EPS else None,
                        "recoveryDesiredMaker": recovery_desired_maker, "recoveryActualMaker": recovery_actual_maker,
                        "recoveryMakerRealizationRatio": (recovery_actual_maker / recovery_desired_maker) if recovery_desired_maker > EPS else None,
                        "otherDesiredMaker": other_desired_maker, "otherActualMaker": other_actual_maker,
                        "otherMakerRealizationRatio": (other_actual_maker / other_desired_maker) if other_desired_maker > EPS else None,
                        "recoveryMakerDeficit": max(0.0, recovery_desired_maker - recovery_actual_maker),
                        "otherMakerDeficit": max(0.0, other_desired_maker - other_actual_maker),
                        "makerChildrenSubmittedCount": float(len(maker_meta)),
                        "makerSubmittedSharesTotal": maker_submitted_shares_total,
                        "makerExecutionFillRatio": (actual_maker_gross / maker_submitted_shares_total) if maker_submitted_shares_total > EPS else None,
                        "makerFillEventCount": float(len(maker_fill_events)),
                        "makerFillShares5s": mf5, "makerFillShares10s": mf10, "makerFillShares20s": mf20,
                        "makerSubmitShares5s": ms5, "makerSubmitShares10s": ms10, "makerSubmitShares20s": ms20,
                        "makerRecentFillToSubmitRatio20s": (mf20 / ms20) if ms20 > EPS else None,
                        "recoveryMakerFillShares10s": recent_fill_shares(10000, recovery_side),
                        "otherMakerFillShares10s": recent_fill_shares(10000, other_side),
                        "recoveryBid": recovery_bid, "recoveryAsk": recovery_ask,
                        "recoverySpreadTicks": (recovery_ask - recovery_bid) / mod.GRID,
                        "otherBid": other_bid, "otherAsk": other_ask,
                        "pairAskSum": recovery_ask + other_ask, "pairBidSum": recovery_bid + other_bid,
                        "marginalSurplusChunkShares": chunk_shares,
                        "marginalSurplusChunkAvgCost": marginal_surplus_avg_cost,
                        "recoveryTakerFeePerShare": recovery_fee_per_share,
                        "lockedPairEdgePerShare": locked_pair_edge_per_share,
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
                    if child_num is not None:
                        route_escalation_side = recovery_side
                        pending_escalation = {
                            "side": recovery_side, "requestedQty": qty, "triggerAtMs": int(t),
                            "originalChildNum": int(child_num), "cancelRequested": False,
                        }
                        action_mode = "CANCEL_PASSIVE_THEN_TAKER"
                    else:
                        num = submit_taker(recovery_side, qty, ask, t, "PAIR_COMPLETION_REPLACE")
                        route_escalation_side = recovery_side
                        action_mode = "TAKER_DIRECT_NO_PASSIVE_CHILD"
                    intervention = {
                        "atMs": int(t), "side": recovery_side, "shares": qty, "observedAsk": ask,
                        "targetNet": target_net, "actualNet": actual_net, "trackingError": error,
                        "orderNum": num, "actionMode": action_mode, "features": feature_state,
                    }

            if pending_escalation is not None:
                side = str(pending_escalation["side"])
                child_num = active_maker[side]
                if child_num is not None:
                    cs = ex.order_snapshot(bt, int(child_num))
                    cur = bt.orders(0).get(int(child_num))
                    if (not pending_escalation.get("cancelRequested")) and cs.get("status") in {"NEW", "PARTIALLY_FILLED"} and cur is not None and bool(cur.cancellable):
                        try:
                            bt.cancel(0, int(child_num), False)
                            pending_escalation["cancelRequested"] = True
                            if intervention is not None:
                                intervention["cancelRequestedAtMs"] = int(t)
                        except Exception:
                            pass
                if active_maker[side] is None:
                    combined_up2 = actual.maker_up + actual.taker_up
                    combined_down2 = actual.maker_down + actual.taker_down
                    actual_net2 = combined_up2 - combined_down2
                    target_net2 = (desired_maker["UP"] + desired_taker["UP"]) - (desired_maker["DOWN"] + desired_taker["DOWN"])
                    error2 = actual_net2 - target_net2
                    still_needed = (error2 > EPS and side == "DOWN") or (error2 < -EPS and side == "UP")
                    qty2 = min(CHUNK, abs(error2)) if still_needed else 0.0
                    if qty2 > EPS:
                        bf2 = mod.outcome_book(book.book, None)
                        if bf2:
                            ask2 = float(bf2["down_ask"] if side == "DOWN" else bf2["up_ask"])
                            num2 = submit_taker(side, qty2, ask2, t, "PAIR_COMPLETION_REPLACE")
                            if intervention is not None:
                                intervention["orderNum"] = num2
                                intervention["submittedAtMs"] = int(t)
                                intervention["submittedShares"] = qty2
                                intervention["submitAsk"] = ask2
                    else:
                        route_escalation_side = None
                        if intervention is not None:
                            intervention["resolvedDuringCancel"] = True
                    pending_escalation = None

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
            err_abs = abs(combined_net - target_net)
            target_error_area += err_abs * dt
            if candidate_at_ms is not None:
                for h in local_horizons_ms:
                    seg_start = max(int(last_t), int(candidate_at_ms))
                    seg_end = min(int(terminal), int(candidate_at_ms) + int(h))
                    if seg_end > seg_start:
                        local_target_error_area[h] += err_abs * (seg_end - seg_start) / 1000.0

        combined_up = actual.maker_up + actual.taker_up
        combined_down = actual.maker_down + actual.taker_down
        target_up = desired_maker["UP"] + desired_taker["UP"]
        target_down = desired_maker["DOWN"] + desired_taker["DOWN"]
        payout = combined_up if win == "UP" else combined_down if win == "DOWN" else None
        total_cost = actual.maker_up_cost + actual.maker_down_cost + actual.taker_up_cost + actual.taker_down_cost + taker_fees
        pnl = float(payout - total_cost) if payout is not None else None
        def completion_cost_at(h: int) -> float | None:
            if candidate_at_ms is None or candidate_recovery_side is None or candidate_qty <= EPS:
                return None
            ask_h = local_horizon_recovery_ask.get(h)
            if ask_h is None:
                bf_end = mod.outcome_book(book.book, None)
                if bf_end:
                    ask_h = float(bf_end["up_ask"] if candidate_recovery_side == "UP" else bf_end["down_ask"])
            if ask_h is None:
                return None
            remaining = float(candidate_qty)
            realized_cost = 0.0
            cutoff = int(candidate_at_ms) + int(h)
            for ev in sorted(fill_log, key=lambda z: int(z["eventMs"])):
                if int(ev["eventMs"]) < int(candidate_at_ms) or int(ev["eventMs"]) > cutoff:
                    continue
                if str(ev["side"]) != candidate_recovery_side or remaining <= EPS:
                    continue
                q = min(remaining, float(ev["shares"]))
                fee_per_share = float(ev.get("fee") or 0.0) / max(float(ev["shares"]), EPS)
                realized_cost += q * float(ev["price"]) + q * fee_per_share
                remaining -= q
            if remaining > EPS:
                realized_cost += remaining * float(ask_h) + mod.taker_fee(remaining, float(ask_h), mod.FEE_BPS)
            return realized_cost

        candidate_child_labels = None
        if candidate_at_ms is not None and candidate_original_child_num is not None:
            future_child = [ev1 for ev1 in fill_log if str(ev1.get("role")) == "MAKER" and int(ev1.get("orderNum") or -1) == int(candidate_original_child_num) and int(ev1.get("eventMs") or 0) > int(candidate_at_ms)]
            candidate_child_labels = {
                "orderNum": int(candidate_original_child_num),
                "side": candidate_recovery_side,
                "cumAtCheckpoint": float(candidate_original_child_cum),
                "anyFill1s": int(any(int(ev1["eventMs"]) <= int(candidate_at_ms)+1000 for ev1 in future_child)),
                "anyFill3s": int(any(int(ev1["eventMs"]) <= int(candidate_at_ms)+3000 for ev1 in future_child)),
                "anyFill5s": int(any(int(ev1["eventMs"]) <= int(candidate_at_ms)+5000 for ev1 in future_child)),
                "fillShares1s": float(sum(float(ev1.get("shares") or 0.0) for ev1 in future_child if int(ev1["eventMs"]) <= int(candidate_at_ms)+1000)),
                "fillShares3s": float(sum(float(ev1.get("shares") or 0.0) for ev1 in future_child if int(ev1["eventMs"]) <= int(candidate_at_ms)+3000)),
                "fillShares5s": float(sum(float(ev1.get("shares") or 0.0) for ev1 in future_child if int(ev1["eventMs"]) <= int(candidate_at_ms)+5000)),
            }

        rec = None
        if intervention is not None:
            onum = intervention.get("orderNum")
            if onum is not None:
                om = taker_meta[int(onum)]
                snap = ex.order_snapshot(bt, int(onum))
                rec = {**intervention, "filledShares": float(om.get("prevCum") or 0.0), "terminalStatus": snap.get("status")}
            else:
                rec = {**intervention, "filledShares": 0.0, "terminalStatus": "NO_TAKER_SUBMITTED"}
        return {
            "marketId": mid, "winner": win, "intervention": rec,
            "targetUp": target_up, "targetDown": target_down,
            "combinedUp": combined_up, "combinedDown": combined_down,
            "finalTrackingError": (combined_up - combined_down) - (target_up - target_down),
            "finalAbsTrackingError": abs((combined_up - combined_down) - (target_up - target_down)),
            "combinedFinalAbsNet": abs(combined_up - combined_down),
            "combinedExposureAreaShareSeconds": combined_exposure_area,
            "targetErrorAreaShareSeconds": target_error_area,
            "candidateAtMs": candidate_at_ms, "eventEscalation": event_escalation, "eventTriggerAtMs": event_trigger_at_ms, "priorityStartedMs": priority_started_ms,
            "candidateDelayMs": int(candidate_delay_ms),
            "candidateAsymmetrySinceMs": candidate_asymmetry_since_ms,
            "candidateAsymmetryAgeMs": (int(candidate_at_ms)-int(candidate_asymmetry_since_ms)) if candidate_at_ms is not None and candidate_asymmetry_since_ms is not None else None,
            "asymmetrySinceMs": asymmetry_since_ms,
            "candidateRecoverySide": candidate_recovery_side,
            "candidateQty": candidate_qty,
            "candidateRecoveryAsk": candidate_recovery_ask,
            "candidateOriginalChildNum": candidate_original_child_num,
            "candidateChildKeepLabels": candidate_child_labels,
            "targetErrorArea5s": local_target_error_area[5000],
            "targetErrorArea10s": local_target_error_area[10000],
            "targetErrorArea20s": local_target_error_area[20000],
            "completionCost5s": completion_cost_at(5000),
            "completionCost10s": completion_cost_at(10000),
            "completionCost20s": completion_cost_at(20000),
            "horizonRecoveryAsk5s": local_horizon_recovery_ask[5000],
            "horizonRecoveryAsk10s": local_horizon_recovery_ask[10000],
            "horizonRecoveryAsk20s": local_horizon_recovery_ask[20000],
            "realizedPnl": pnl, "takerFeesUsdt": taker_fees,
            "fillLog": fill_log,
        }
    finally:
        book.close(); bt.close()


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--market-ids", required=True); a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(",") if x.strip()]
    rows=[]
    for i,mid in enumerate(mids,1):
        b=run_recovery(mid, enable_intervention=False, passive_priority=False)
        r=run_recovery(mid, enable_intervention=False, passive_priority=True)
        row={
            "marketId":mid,
            "baselinePnl":b["realizedPnl"],"priorityPnl":r["realizedPnl"],
            "deltaPnl":(float(r["realizedPnl"])-float(b["realizedPnl"])) if b["realizedPnl"] is not None and r["realizedPnl"] is not None else None,
            "baselineFinalAbsNet":b["combinedFinalAbsNet"],"priorityFinalAbsNet":r["combinedFinalAbsNet"],
            "deltaFinalAbsNet":float(r["combinedFinalAbsNet"])-float(b["combinedFinalAbsNet"]),
            "baselineExposureArea":b["combinedExposureAreaShareSeconds"],"priorityExposureArea":r["combinedExposureAreaShareSeconds"],
            "deltaExposureArea":float(r["combinedExposureAreaShareSeconds"])-float(b["combinedExposureAreaShareSeconds"]),
            "baselineTargetErrorArea":b["targetErrorAreaShareSeconds"],"priorityTargetErrorArea":r["targetErrorAreaShareSeconds"],
            "deltaTargetErrorArea":float(r["targetErrorAreaShareSeconds"])-float(b["targetErrorAreaShareSeconds"]),
            "baselineFinalAbsTrackingError":b["finalAbsTrackingError"],"priorityFinalAbsTrackingError":r["finalAbsTrackingError"],
            "deltaFinalAbsTrackingError":float(r["finalAbsTrackingError"])-float(b["finalAbsTrackingError"]),
            "candidateAtMs":r.get("candidateAtMs"),"candidateRecoverySide":r.get("candidateRecoverySide")
        };rows.append(row)
        print(json.dumps({"progress":i,"marketId":mid,"deltaTargetErrorArea":row["deltaTargetErrorArea"],"deltaExposureArea":row["deltaExposureArea"],"deltaPnl":row["deltaPnl"]},ensure_ascii=False),flush=True)
    valid=[x for x in rows if x["candidateAtMs"] is not None]
    agg={
      "markets":len(rows),"eligible":len(valid),
      "trackingImproved":sum(float(x["deltaTargetErrorArea"]) < -EPS for x in valid),
      "trackingWorsened":sum(float(x["deltaTargetErrorArea"]) > EPS for x in valid),
      "sumDeltaTargetErrorArea":sum(float(x["deltaTargetErrorArea"]) for x in valid),
      "sumDeltaExposureArea":sum(float(x["deltaExposureArea"]) for x in valid),
      "sumDeltaFinalAbsNet":sum(float(x["deltaFinalAbsNet"]) for x in valid),
      "sumDeltaFinalAbsTrackingError":sum(float(x["deltaFinalAbsTrackingError"]) for x in valid),
      "sumDeltaPnlDiagnostic":sum(float(x["deltaPnl"]) for x in valid if x["deltaPnl"] is not None)
    }
    out=OUT/"r2_residual_opposite_priority_clean_v0.json"
    out.write_text(json.dumps({
      "version":"R2_RESIDUAL_OPPOSITE_PRIORITY_CLEAN_V0","researchOnly":True,"liveTradingChanges":False,"dreamFillAllowed":False,
      "basePolicy":"FROZEN_R2_INTENT_TAPE",
      "residualAction":"When one strategy-chunk tracking asymmetry exists, preserve all existing orders and suppress only NEW surplus-side Maker children while the opposite R2 responsibility remains under-realized; release automatically when recovery-side need resolves. No new exposure, no cancel, no extra Taker.",
      "primaryEvaluation":"execution-local target-error-area / exposure-area; end-market PnL diagnostic only",
      "guardrails":["No threshold sweep","Natural 18-share R2 chunk defines asymmetry eligibility","No Target/winner/PnL runtime input","Existing queue preserved","Actual HftBacktest fills only"],
      "aggregate":agg,"rows":rows
    },ensure_ascii=False,indent=2,allow_nan=True),encoding="utf-8")
    print(json.dumps({"ok":True,"report":str(out),"aggregate":agg},ensure_ascii=False))

if __name__ == "__main__":
    main()
