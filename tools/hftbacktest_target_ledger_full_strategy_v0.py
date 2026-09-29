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
from src.predict_bot import unified_controller_paper_v2 as mod

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
EPS = 1e-9
TAKER_CONFIRM_MS = 2200
MAKER_SKEW_MODE: str | None = None


def run_market(mid: int) -> dict[str, Any]:
    paper = load_reference_paper(mid)
    maker_intents = [
        {"atMs": int(o["placed_at_ms"]), "side": str(o["side"]).upper(), "shares": float(o.get("shares") or mod.SHARES)}
        for o in paper["orders"]
    ]
    taker_intents = [
        {"atMs": int(x["filled_at_ms"]), "side": str(x["side"]).upper(), "shares": float(x.get("shares") or mod.SHARES),
         "observedPrice": float(x["price"]), "decisionId": str(x.get("decision_id") or "")}
        for x in paper["takers"] if x.get("filled_at_ms") is not None and x.get("price") is not None
    ]
    maker_intents.sort(key=lambda x: x["atMs"])
    taker_intents.sort(key=lambda x: x["atMs"])
    snaps = load_public_snapshots(mid)

    by_maker: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_taker: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_taker_expiry: dict[int, list[int]] = defaultdict(list)
    for x in maker_intents:
        by_maker[int(x["atMs"])].append(x)
    for i, x in enumerate(taker_intents, 1):
        x["intentNum"] = i
        by_taker[int(x["atMs"])].append(x)
        by_taker_expiry[int(x["atMs"]) + TAKER_CONFIRM_MS].append(i)

    timeline = sorted(set(
        [int(s["sampledAtMs"]) for s in snaps]
        + [int(x["atMs"]) for x in maker_intents]
        + [int(x["atMs"]) for x in taker_intents]
        + list(by_taker_expiry.keys())
    ))

    events, _, feed_meta = tape_v1.build_archive_events(mid, trade_offset="mid")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    book = mod.PublicBookTailer(mod.BOOK_DB)
    win = winners([mid]).get(mid)

    desired_maker = {"UP": 0.0, "DOWN": 0.0}
    actual = mod.Inventory()
    taker_fees = 0.0

    active_maker: dict[str, int | None] = {"UP": None, "DOWN": None}
    maker_meta: dict[int, dict[str, Any]] = {}
    taker_meta: dict[int, dict[str, Any]] = {}
    taker_num_by_intent: dict[int, int] = {}
    next_num = 1
    maker_submits = maker_rejects = 0
    taker_submits = taker_rejects = taker_cancel_requests = 0
    maker_exposure_area = combined_exposure_area = 0.0
    last_t: int | None = None

    def harvest_maker(now: int) -> None:
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
                om["prevCum"] = cum
            if snap.get("status") in {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}:
                active_maker[side] = None

    def harvest_taker(now: int) -> None:
        nonlocal taker_fees
        for num, om in taker_meta.items():
            snap = ex.order_snapshot(bt, num)
            cum = float(snap.get("cumExecQty") or 0.0)
            old = float(om.get("prevCum") or 0.0)
            if cum <= old + EPS:
                continue
            delta = cum - old
            native_px = snap.get("execPrice")
            px = float(om["observedPrice"])
            if native_px is not None and math.isfinite(float(native_px)):
                px = float(native_px) if om["side"] == "UP" else 1.0 - float(native_px)
            fill_ms = int((snap.get("exchangeTs") or now * 1_000_000) // 1_000_000)
            actual.apply({"event_ms": fill_ms, "role": "TAKER", "side": om["side"], "price": px, "shares": delta})
            taker_fees += mod.taker_fee(delta, px, mod.FEE_BPS)
            om["prevCum"] = cum

    def submit_maker_side(side: str, t: int) -> None:
        nonlocal next_num, maker_submits, maker_rejects
        if active_maker[side] is not None:
            return
        actual_side = actual.maker_up if side == "UP" else actual.maker_down
        deficit = desired_maker[side] - actual_side
        if deficit <= EPS:
            return
        opp = "DOWN" if side == "UP" else "UP"
        opp_price = float(maker_meta[int(active_maker[opp])]["price"]) if active_maker[opp] is not None else None
        qty = min(float(mod.SHARES), deficit)
        if MAKER_SKEW_MODE == "TRACK_SKEW_0_3_SIZE_HALF":
            bf = mod.outcome_book(book.book, None)
            if not bf:
                return
            maker_err = (actual.maker_up - actual.maker_down) - (desired_maker["UP"] - desired_maker["DOWN"])
            recovery = "DOWN" if maker_err > float(mod.SHARES) - EPS else "UP" if maker_err < -float(mod.SHARES) + EPS else None
            off = 1 if recovery is None else (0 if side == recovery else 3)
            bid = float(bf["up_bid"] if side == "UP" else bf["down_bid"])
            tick = int(math.floor((bid + 1e-9) / mod.GRID)) - off
            tick = max(int(round(mod.MIN_PRICE / mod.GRID)), tick)
            px = round(tick * mod.GRID, 2)
            if opp_price is not None:
                while px + float(opp_price) > mod.MAX_PAIR_PRICE_SUM + EPS:
                    tick -= 1
                    if tick < int(round(mod.MIN_PRICE / mod.GRID)):
                        return
                    px = round(tick * mod.GRID, 2)
            if recovery is not None and side != recovery:
                qty = min(qty, float(mod.SHARES) * 0.5)
        else:
            px = r2_passive_quote(book.book, side, opp_price)
        if px is None:
            return
        num = next_num; next_num += 1
        rc = ex.submit_native(bt, num, side, px, qty)
        maker_submits += 1
        if rc != 0:
            maker_rejects += 1
        maker_meta[num] = {"side": side, "price": px, "qty": qty, "prevCum": 0.0, "submittedAtMs": t, "submitRc": int(rc)}
        active_maker[side] = num

    def submit_taker_intent(x: dict[str, Any]) -> None:
        nonlocal next_num, taker_submits, taker_rejects
        side = str(x["side"])
        max_price = min(0.99, float(x["observedPrice"]) + 0.02)
        num = next_num; next_num += 1
        native_side, native_price = ex.native_order(side, max_price)
        if native_side == "BUY":
            rc = int(bt.submit_buy_order(0, num, native_price, float(x["shares"]), ex.hbt.GTC, ex.LIMIT, False))
        else:
            rc = int(bt.submit_sell_order(0, num, native_price, float(x["shares"]), ex.hbt.GTC, ex.LIMIT, False))
        taker_submits += 1
        if rc != 0:
            taker_rejects += 1
        taker_num_by_intent[int(x["intentNum"])] = num
        taker_meta[num] = {**x, "orderNum": num, "maxPrice": max_price, "prevCum": 0.0, "submitRc": int(rc), "cancelRequested": False}

    try:
        for t in timeline:
            if int(bt.current_timestamp // 1_000_000) <= t:
                ex.advance_to(bt, t)
            harvest_maker(t)
            harvest_taker(t)

            if last_t is not None:
                dt = max(0, t - last_t) / 1000.0
                maker_exposure_area += abs(actual.maker_up - actual.maker_down) * dt
                combined_exposure_area += abs((actual.maker_up + actual.taker_up) - (actual.maker_down + actual.taker_down)) * dt
            last_t = t

            book.advance(mid, t)
            for x in by_maker.get(t, []):
                desired_maker[x["side"]] += float(x["shares"])
            for x in by_taker.get(t, []):
                submit_taker_intent(x)

            # Venue-confirm timeout for Taker intent. Request cancellation of any remaining quantity.
            for intent_num in by_taker_expiry.get(t, []):
                num = taker_num_by_intent.get(int(intent_num))
                if num is None:
                    continue
                om = taker_meta[num]
                snap = ex.order_snapshot(bt, num)
                if snap.get("status") in {"NEW", "PARTIALLY_FILLED"}:
                    cur = bt.orders(0).get(num)
                    if cur is not None and bool(cur.cancellable):
                        try:
                            bt.cancel(0, num, False)
                            om["cancelRequested"] = True
                            taker_cancel_requests += 1
                        except Exception:
                            pass

            for side in ("UP", "DOWN"):
                submit_maker_side(side, t)

        terminal = int(feed_meta["lastReceivedMs"])
        if int(bt.current_timestamp // 1_000_000) <= terminal:
            ex.advance_to(bt, terminal)
        harvest_maker(terminal)
        harvest_taker(terminal)
        if last_t is not None:
            dt = max(0, terminal - last_t) / 1000.0
            maker_exposure_area += abs(actual.maker_up - actual.maker_down) * dt
            combined_exposure_area += abs((actual.maker_up + actual.taker_up) - (actual.maker_down + actual.taker_down)) * dt

        maker_desired = desired_maker["UP"] + desired_maker["DOWN"]
        maker_filled = actual.maker_up + actual.maker_down
        taker_requested = sum(float(x["shares"]) for x in taker_intents)
        taker_filled = actual.taker_up + actual.taker_down
        combined_up = actual.maker_up + actual.taker_up
        combined_down = actual.maker_down + actual.taker_down
        payout = combined_up if win == "UP" else combined_down if win == "DOWN" else None
        total_cost = actual.maker_up_cost + actual.maker_down_cost + actual.taker_up_cost + actual.taker_down_cost + taker_fees
        pnl = float(payout - total_cost) if payout is not None else None
        return {
            "marketId": mid, "winner": win,
            "paperMakerIntents": len(maker_intents), "paperTakerIntents": len(taker_intents),
            "makerDesiredShares": maker_desired, "makerFilledShares": maker_filled,
            "makerRealizationRate": maker_filled / maker_desired if maker_desired > EPS else None,
            "takerRequestedShares": taker_requested, "takerFilledShares": taker_filled,
            "takerRealizationRate": taker_filled / taker_requested if taker_requested > EPS else None,
            "makerUp": actual.maker_up, "makerDown": actual.maker_down,
            "takerUp": actual.taker_up, "takerDown": actual.taker_down,
            "combinedUp": combined_up, "combinedDown": combined_down,
            "makerFinalAbsNet": abs(actual.maker_up - actual.maker_down),
            "combinedFinalAbsNet": abs(combined_up - combined_down),
            "makerExposureAreaShareSeconds": maker_exposure_area,
            "combinedExposureAreaShareSeconds": combined_exposure_area,
            "makerSubmits": maker_submits, "makerRejects": maker_rejects,
            "takerSubmits": taker_submits, "takerRejects": taker_rejects, "takerCancelRequests": taker_cancel_requests,
            "takerFeesUsdt": taker_fees, "realizedPnl": pnl,
        }
    finally:
        book.close(); bt.close()


def max_drawdown(xs: list[float]) -> float:
    peak = 0.0; cur = 0.0; dd = 0.0
    for x in xs:
        cur += x; peak = max(peak, cur); dd = max(dd, peak - cur)
    return dd


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--market-ids", required=True); a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(",") if x.strip()]
    rows = []
    for i, mid in enumerate(mids, 1):
        r = run_market(mid); rows.append(r)
        print(json.dumps({"progress": i, "marketId": mid, "makerReal": r["makerRealizationRate"], "takerReal": r["takerRealizationRate"], "combinedAbsNet": r["combinedFinalAbsNet"], "pnl": r["realizedPnl"]}, ensure_ascii=False), flush=True)
    pnls = [float(r["realizedPnl"]) for r in rows if r["realizedPnl"] is not None]
    md = sum(float(r["makerDesiredShares"]) for r in rows); mf = sum(float(r["makerFilledShares"]) for r in rows)
    tr = sum(float(r["takerRequestedShares"]) for r in rows); tf = sum(float(r["takerFilledShares"]) for r in rows)
    wins_n = sum(x > EPS for x in pnls); losses_n = sum(x < -EPS for x in pnls)
    agg = {
        "markets": len(rows), "paperMakerIntents": sum(int(r["paperMakerIntents"]) for r in rows),
        "paperTakerIntents": sum(int(r["paperTakerIntents"]) for r in rows),
        "makerDesiredShares": md, "makerFilledShares": mf, "makerRealizationRate": mf / md if md > EPS else None,
        "takerRequestedShares": tr, "takerFilledShares": tf, "takerRealizationRate": tf / tr if tr > EPS else None,
        "totalRealizedPnl": sum(pnls), "wins": wins_n, "losses": losses_n,
        "winRate": wins_n / len(pnls) if pnls else None, "maxCumulativeDrawdown": max_drawdown(pnls),
        "meanMakerFinalAbsNet": sum(float(r["makerFinalAbsNet"]) for r in rows) / len(rows) if rows else None,
        "meanCombinedFinalAbsNet": sum(float(r["combinedFinalAbsNet"]) for r in rows) / len(rows) if rows else None,
        "maxCombinedFinalAbsNet": max((float(r["combinedFinalAbsNet"]) for r in rows), default=None),
        "makerExposureAreaShareSeconds": sum(float(r["makerExposureAreaShareSeconds"]) for r in rows),
        "combinedExposureAreaShareSeconds": sum(float(r["combinedExposureAreaShareSeconds"]) for r in rows),
        "takerFeesUsdt": sum(float(r["takerFeesUsdt"]) for r in rows),
        "preliminaryThreshold": {"positivePnl": True, "winRateAtLeast": 0.5},
        "preliminaryPassedOnDiagnostic": bool(pnls and sum(pnls) > 0 and wins_n / len(pnls) >= 0.5),
    }
    out = OUT / "target_ledger_full_strategy_v0.json"
    out.write_text(json.dumps({
        "version": "TARGET_LEDGER_FULL_STRATEGY_V0", "researchOnly": True, "liveTradingChanges": False,
        "strategyIntentSource": "FROZEN_R2_PAPER_INTENT_TAPE", "dreamFillUsedForPnl": False,
        "makerExecution": "CUMULATIVE_TARGET_ONE_ACTIVE_CHILD_PER_SIDE_FIXED_R2_PASSIVE_QUOTE",
        "takerExecution": "HFTBACKTEST_MARKETABLE_LIMIT_PLUS_2C_CONFIRM_2200MS",
        "aggregate": agg, "rows": rows,
    }, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(out), "aggregate": agg}, ensure_ascii=False))


if __name__ == "__main__": main()
