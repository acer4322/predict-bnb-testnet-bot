from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

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
FILL_ART = OUT / "open_order_fill_lifecycle_v0.joblib"
EPS = 1e-9


def run_market(mid: int) -> dict[str, Any]:
    paper = load_reference_paper(mid)
    intents = [{"atMs": int(o["placed_at_ms"]), "side": str(o["side"]).upper(), "shares": float(o.get("shares") or mod.SHARES)} for o in paper["orders"]]
    intents.sort(key=lambda x: x["atMs"])
    snaps = load_public_snapshots(mid)
    timeline = sorted(set([int(s["sampledAtMs"]) for s in snaps] + [int(x["atMs"]) for x in intents]))
    by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for x in intents:
        by_time[int(x["atMs"])].append(x)

    fill_bundle = joblib.load(FILL_ART)
    fill_features = list(fill_bundle["features"])
    fill_model = fill_bundle["models"]["fill_5s"]

    events, _, meta = tape_v1.build_archive_events(mid, trade_offset="mid")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    book = mod.PublicBookTailer(mod.BOOK_DB)
    win = winners([mid]).get(mid)

    desired = {"UP": 0.0, "DOWN": 0.0}
    actual_inv = mod.Inventory()
    active: dict[str, int | None] = {"UP": None, "DOWN": None}
    meta_by_num: dict[int, dict[str, Any]] = {}
    next_num = 1
    submits = rejects = cancel_requests = cancel_acks = 0
    p5_values: list[float] = []
    exposure_area = 0.0
    last_t: int | None = None

    def update_depletion(changes: dict[str, Any], _source_ms: int) -> None:
        for side in ("UP", "DOWN"):
            num = active[side]
            if num is None:
                continue
            om = meta_by_num[num]
            native_side = om["nativeSide"]
            native_price = float(om["nativePrice"])
            for ch in changes.get(native_side, []) or []:
                try:
                    px = float(ch.get("price")); delta = float(ch.get("delta", 0.0))
                except Exception:
                    continue
                if abs(px - native_price) <= 1e-9 and delta < 0:
                    om["cumDepletion"] += -delta
                    om["anyDepletion"] = True

    def harvest(now: int) -> None:
        nonlocal cancel_acks
        for side in ("UP", "DOWN"):
            num = active[side]
            if num is None:
                continue
            om = meta_by_num[num]
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
                actual_inv.apply({"event_ms": fill_ms, "role": "MAKER", "side": side, "price": px, "shares": delta})
                om["prevCum"] = cum
            if snap.get("status") in {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}:
                if snap.get("status") == "CANCELED" and om.get("cancelRequested"):
                    cancel_acks += 1
                active[side] = None

    def fill_frame(side: str, num: int, now: int) -> pd.DataFrame:
        om = meta_by_num[num]
        snap = ex.order_snapshot(bt, num)
        bf = mod.outcome_book(book.book, None) or {}
        bid = bf.get("up_bid") if side == "UP" else bf.get("down_bid")
        ask = bf.get("up_ask") if side == "UP" else bf.get("down_ask")
        cum = float(snap.get("cumExecQty") or 0.0)
        leaves = snap.get("leavesQty")
        if leaves is None:
            leaves = max(0.0, float(om["qty"]) - cum)
        qty = float(om["qty"])
        init_depth = float(om["initialDepth"])
        depletion = float(om["cumDepletion"])
        port = actual_inv.features(now)
        port.pop("_combined_net", None)
        status = str(snap.get("status") or "NONE")
        vals: dict[str, Any] = {
            "side_is_up": float(side == "UP"), "order_age_ms": float(now - int(om["submittedAtMs"])),
            "quote_price": float(om["price"]), "status_none": float(status == "NONE"),
            "status_new": float(status == "NEW"), "status_partial": float(status == "PARTIALLY_FILLED"),
            "cum_exec_qty": cum, "remaining_qty": float(leaves), "remaining_ratio": float(leaves) / qty if qty > EPS else math.nan,
            "partial_fill_ratio": cum / qty if qty > EPS else 0.0, "active_same_count": 1.0,
            "active_opp_count": float(active["DOWN" if side == "UP" else "UP"] is not None),
            "quote_offset_ticks": ((float(bid) - float(om["price"])) / mod.GRID) if bid is not None else math.nan,
            "current_bid": float(bid) if bid is not None else math.nan, "current_ask": float(ask) if ask is not None else math.nan,
            "current_spread_ticks": ((float(ask) - float(bid)) / mod.GRID) if bid is not None and ask is not None else math.nan,
            "initial_depth": init_depth, "public_cum_depletion": depletion,
            "public_depletion_ratio": depletion / init_depth if init_depth > EPS else math.nan,
            "public_any_depletion": float(bool(om["anyDepletion"])),
        }
        vals.update(port)
        return pd.DataFrame([{f: vals.get(f, math.nan) for f in fill_features}], columns=fill_features)

    def submit_side(side: str, t: int) -> None:
        nonlocal next_num, submits, rejects
        deficit = desired[side] - (actual_inv.maker_up if side == "UP" else actual_inv.maker_down)
        if deficit <= EPS or active[side] is not None:
            return
        opp = "DOWN" if side == "UP" else "UP"
        opp_price = float(meta_by_num[int(active[opp])]["price"]) if active[opp] is not None else None
        px = r2_passive_quote(book.book, side, opp_price)
        if px is None:
            return
        qty = min(float(mod.SHARES), deficit)
        native_side = "bids" if side == "UP" else "asks"
        native_price = round(px if side == "UP" else 1.0 - px, 10)
        initial_depth = float(book.book.get(native_side, {}).get(native_price, 0.0))
        num = next_num; next_num += 1
        rc = ex.submit_native(bt, num, side, px, qty)
        submits += 1
        if rc != 0: rejects += 1
        meta_by_num[num] = {"side": side, "price": px, "qty": qty, "prevCum": 0.0, "submittedAtMs": t,
                            "submitRc": int(rc), "nativeSide": native_side, "nativePrice": native_price,
                            "initialDepth": initial_depth, "cumDepletion": 0.0, "anyDepletion": False,
                            "cancelRequested": False}
        active[side] = num

    try:
        for t in timeline:
            if int(bt.current_timestamp // 1_000_000) <= t:
                ex.advance_to(bt, t)
            harvest(t)
            if last_t is not None:
                exposure_area += abs(actual_inv.maker_up - actual_inv.maker_down) * max(0, t - last_t) / 1000.0
            last_t = t
            book.advance(mid, t, update_depletion)
            for x in by_time.get(t, []): desired[x["side"]] += float(x["shares"])

            # Existing child management: use learned fill lifecycle only here.
            for side in ("UP", "DOWN"):
                num = active[side]
                if num is None:
                    continue
                om = meta_by_num[num]
                snap = ex.order_snapshot(bt, num)
                if om.get("cancelRequested") or snap.get("status") not in {"NEW", "PARTIALLY_FILLED"}:
                    continue
                p5 = float(fill_model.predict_proba(fill_frame(side, num, t))[0, 1])
                p5_values.append(p5)
                if p5 < 0.5:
                    cur = bt.orders(0).get(num)
                    if cur is not None and bool(cur.cancellable):
                        try:
                            bt.cancel(0, num, False)
                            om["cancelRequested"] = True
                            cancel_requests += 1
                        except Exception:
                            pass

            for side in ("UP", "DOWN"):
                submit_side(side, t)

        terminal = int(meta["lastReceivedMs"])
        if int(bt.current_timestamp // 1_000_000) <= terminal:
            ex.advance_to(bt, terminal)
        harvest(terminal)
        if last_t is not None:
            exposure_area += abs(actual_inv.maker_up - actual_inv.maker_down) * max(0, terminal - last_t) / 1000.0

        desired_total = desired["UP"] + desired["DOWN"]
        filled_total = actual_inv.maker_up + actual_inv.maker_down
        cost = actual_inv.maker_up_cost + actual_inv.maker_down_cost
        payout = (actual_inv.maker_up if win == "UP" else actual_inv.maker_down) if win in {"UP", "DOWN"} else None
        pnl = float(payout - cost) if payout is not None else None
        return {"marketId": mid, "winner": win, "paperMakerIntents": len(intents), "desiredShares": desired_total,
                "filledShares": filled_total, "realizationRate": filled_total / desired_total if desired_total > EPS else None,
                "actualUp": actual_inv.maker_up, "actualDown": actual_inv.maker_down,
                "finalAbsNet": abs(actual_inv.maker_up - actual_inv.maker_down), "makerOnlyPnl": pnl,
                "submits": submits, "rejects": rejects, "cancelRequests": cancel_requests, "cancelAcks": cancel_acks,
                "pFill5Mean": sum(p5_values) / len(p5_values) if p5_values else None,
                "exposureAreaShareSeconds": exposure_area}
    finally:
        book.close(); bt.close()


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--market-ids", required=True); a = ap.parse_args()
    mids = [int(x) for x in a.market_ids.split(",") if x.strip()]; rows = []
    for i, mid in enumerate(mids, 1):
        r = run_market(mid); rows.append(r)
        print(json.dumps({"progress": i, "marketId": mid, "realization": r["realizationRate"], "absNet": r["finalAbsNet"], "pnl": r["makerOnlyPnl"], "cancels": r["cancelRequests"]}, ensure_ascii=False), flush=True)
    pnls = [float(r["makerOnlyPnl"]) for r in rows if r["makerOnlyPnl"] is not None]
    desired = sum(float(r["desiredShares"]) for r in rows); filled = sum(float(r["filledShares"]) for r in rows)
    agg = {"markets": len(rows), "paperMakerIntents": sum(int(r["paperMakerIntents"]) for r in rows),
           "desiredShares": desired, "filledShares": filled, "realizationRate": filled / desired if desired > EPS else None,
           "totalMakerOnlyPnl": sum(pnls), "wins": sum(x > EPS for x in pnls), "losses": sum(x < -EPS for x in pnls),
           "winRate": sum(x > EPS for x in pnls) / len(pnls) if pnls else None,
           "meanFinalAbsNet": sum(float(r["finalAbsNet"]) for r in rows) / len(rows) if rows else None,
           "maxFinalAbsNet": max((float(r["finalAbsNet"]) for r in rows), default=None),
           "totalExposureAreaShareSeconds": sum(float(r["exposureAreaShareSeconds"]) for r in rows),
           "submits": sum(int(r["submits"]) for r in rows), "rejects": sum(int(r["rejects"]) for r in rows),
           "cancelRequests": sum(int(r["cancelRequests"]) for r in rows), "cancelAcks": sum(int(r["cancelAcks"]) for r in rows)}
    out = OUT / "target_ledger_executor_v1.json"
    out.write_text(json.dumps({"version": "TARGET_LEDGER_EXECUTOR_V1_FILL_LIFECYCLE", "researchOnly": True, "liveTradingChanges": False,
                               "strategyIntentsFrozen": True, "dreamFillUsedForPnl": False,
                               "executionPolicy": "ONE_ACTIVE_CHILD_PER_SIDE_PLUS_FILL5_NATURAL_0P5_CANCEL_REPRICE",
                               "aggregate": agg, "rows": rows}, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(out), "aggregate": agg}, ensure_ascii=False))


if __name__ == "__main__": main()
