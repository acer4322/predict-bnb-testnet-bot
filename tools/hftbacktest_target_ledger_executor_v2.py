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
from src.predict_bot import unified_controller_paper_v2 as mod

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
EPS = 1e-9


def r2_passive_quote(book: dict[str, dict[float, float]], side: str, opposite_price: float | None) -> float | None:
    bf = mod.outcome_book(book, None)
    if not bf:
        return None
    bid = float(bf["up_bid"] if side == "UP" else bf["down_bid"])
    tick = int(math.floor((bid + 1e-9) / mod.GRID)) - 1
    tick = max(int(round(mod.MIN_PRICE / mod.GRID)), tick)
    price = round(tick * mod.GRID, 2)
    if opposite_price is not None:
        while price + float(opposite_price) > mod.MAX_PAIR_PRICE_SUM + EPS:
            tick -= 1
            if tick < int(round(mod.MIN_PRICE / mod.GRID)):
                return None
            price = round(tick * mod.GRID, 2)
    return price


def run_market(mid: int) -> dict[str, Any]:
    paper = load_reference_paper(mid)
    intents = [
        {"atMs": int(o["placed_at_ms"]), "side": str(o["side"]).upper(), "shares": float(o.get("shares") or mod.SHARES)}
        for o in paper["orders"]
    ]
    intents.sort(key=lambda x: x["atMs"])
    snaps = load_public_snapshots(mid)
    snap_times = sorted({int(s["sampledAtMs"]) for s in snaps})
    intent_times = sorted({int(x["atMs"]) for x in intents})
    timeline = sorted(set(snap_times + intent_times))
    by_time: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for x in intents:
        by_time[int(x["atMs"])].append(x)

    events, _, meta = tape_v1.build_archive_events(mid, trade_offset="mid")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    book = mod.PublicBookTailer(mod.BOOK_DB)
    win = winners([mid]).get(mid)

    desired = {"UP": 0.0, "DOWN": 0.0}
    actual = {"UP": 0.0, "DOWN": 0.0}
    cost = 0.0
    active: dict[str, int | None] = {"UP": None, "DOWN": None}
    meta_by_num: dict[int, dict[str, Any]] = {}
    next_num = 1
    submits = 0
    rejects = 0
    exposure_area = 0.0
    last_t: int | None = None

    def harvest(now: int) -> None:
        nonlocal cost
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
                actual[side] += delta
                cost += delta * px
                om["prevCum"] = cum
            if snap.get("status") in {"FILLED", "REJECTED", "EXPIRED", "CANCELED"}:
                active[side] = None

    try:
        for t in timeline:
            if int(bt.current_timestamp // 1_000_000) <= t:
                ex.advance_to(bt, t)
            harvest(t)
            if last_t is not None:
                exposure_area += abs(actual["UP"] - actual["DOWN"]) * max(0, t - last_t) / 1000.0
            last_t = t
            book.advance(mid, t)
            for x in by_time.get(t, []):
                desired[x["side"]] += float(x["shares"])

            # Persistent target ledger: at most one working child per side.
            # New strategy intents only increase desired target; they do not create duplicate working orders.
            target_net = desired["UP"] - desired["DOWN"]
            actual_net = actual["UP"] - actual["DOWN"]
            tracking_error = actual_net - target_net
            for side in ("UP", "DOWN"):
                if active[side] is not None:
                    continue
                # Only open a new child if its fill moves actual net toward the Strategy target net.
                # Existing children are never canceled here, preserving queue position.
                if tracking_error > EPS and side == "UP":
                    continue
                if tracking_error < -EPS and side == "DOWN":
                    continue
                deficit = desired[side] - actual[side]
                if deficit <= EPS:
                    continue
                opp = "DOWN" if side == "UP" else "UP"
                opp_price = None
                if active[opp] is not None:
                    opp_price = float(meta_by_num[int(active[opp])]["price"])
                px = r2_passive_quote(book.book, side, opp_price)
                if px is None:
                    continue
                qty = min(float(mod.SHARES), deficit)
                num = next_num
                next_num += 1
                rc = ex.submit_native(bt, num, side, px, qty)
                submits += 1
                if rc != 0:
                    rejects += 1
                meta_by_num[num] = {"side": side, "price": px, "qty": qty, "prevCum": 0.0, "submittedAtMs": t, "submitRc": int(rc)}
                active[side] = num

        terminal = int(meta["lastReceivedMs"])
        if int(bt.current_timestamp // 1_000_000) <= terminal:
            ex.advance_to(bt, terminal)
        harvest(terminal)
        if last_t is not None:
            exposure_area += abs(actual["UP"] - actual["DOWN"]) * max(0, terminal - last_t) / 1000.0

        total_desired = desired["UP"] + desired["DOWN"]
        total_actual = actual["UP"] + actual["DOWN"]
        payout = actual[win] if win in {"UP", "DOWN"} else None
        pnl = (float(payout) - cost) if payout is not None else None
        return {
            "marketId": mid, "winner": win,
            "paperMakerIntents": len(intents),
            "desiredUp": desired["UP"], "desiredDown": desired["DOWN"],
            "actualUp": actual["UP"], "actualDown": actual["DOWN"],
            "desiredShares": total_desired, "filledShares": total_actual,
            "realizationRate": total_actual / total_desired if total_desired > EPS else None,
            "finalAbsNet": abs(actual["UP"] - actual["DOWN"]),
            "targetFinalAbsNet": abs(desired["UP"] - desired["DOWN"]),
            "exposureAreaShareSeconds": exposure_area,
            "submits": submits, "rejects": rejects,
            "makerOnlyPnl": pnl,
        }
    finally:
        book.close()
        bt.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", required=True)
    args = ap.parse_args()
    mids = [int(x) for x in args.market_ids.split(",") if x.strip()]
    rows = []
    for i, mid in enumerate(mids, 1):
        r = run_market(mid)
        rows.append(r)
        print(json.dumps({"progress": i, "marketId": mid, "realization": r["realizationRate"], "absNet": r["finalAbsNet"], "pnl": r["makerOnlyPnl"]}, ensure_ascii=False), flush=True)
    pnls = [float(r["makerOnlyPnl"]) for r in rows if r["makerOnlyPnl"] is not None]
    desired = sum(float(r["desiredShares"]) for r in rows)
    filled = sum(float(r["filledShares"]) for r in rows)
    agg = {
        "markets": len(rows), "paperMakerIntents": sum(int(r["paperMakerIntents"]) for r in rows),
        "desiredShares": desired, "filledShares": filled,
        "realizationRate": filled / desired if desired > EPS else None,
        "totalMakerOnlyPnl": sum(pnls),
        "wins": sum(x > EPS for x in pnls), "losses": sum(x < -EPS for x in pnls),
        "winRate": sum(x > EPS for x in pnls) / len(pnls) if pnls else None,
        "meanFinalAbsNet": sum(float(r["finalAbsNet"]) for r in rows) / len(rows) if rows else None,
        "maxFinalAbsNet": max((float(r["finalAbsNet"]) for r in rows), default=None),
        "totalExposureAreaShareSeconds": sum(float(r["exposureAreaShareSeconds"]) for r in rows),
        "submits": sum(int(r["submits"]) for r in rows), "rejects": sum(int(r["rejects"]) for r in rows),
    }
    out = OUT / "target_ledger_executor_v2.json"
    out.write_text(json.dumps({
        "version": "TARGET_LEDGER_EXECUTOR_V2_TARGET_NET_PRIORITY", "researchOnly": True, "liveTradingChanges": False,
        "strategyIntentsFrozen": True, "dreamFillUsedForPnl": False,
        "executionPolicy": "CUMULATIVE_TARGET_ONE_ACTIVE_CHILD_PER_SIDE_TARGET_NET_PRIORITY_FIXED_R2_PASSIVE_QUOTE",
        "aggregate": agg, "rows": rows,
    }, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(out), "aggregate": agg}, ensure_ascii=False))


if __name__ == "__main__":
    main()
