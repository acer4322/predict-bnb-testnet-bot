from __future__ import annotations

import argparse
import bisect
import json
import math
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import load_reference_paper, load_public_snapshots
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import train_passive_queue_selection_v0 as qs
from src.predict_bot import unified_controller_paper_v2 as mod

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
ART = OUT / "passive_queue_selection_v0.joblib"
EPS = 1e-9
REP_OFFSET = {"AHEAD": 0, "AT_BID": 0, "ONE_BEHIND": 1, "TWO_THREE_BEHIND": 2, "FOUR_PLUS_BEHIND": 4}


def build_intents(mid: int, bundle: dict[str, Any]) -> list[dict[str, Any]]:
    paper = load_reference_paper(mid)
    snaps = load_public_snapshots(mid)
    times = [int(s["sampledAtMs"]) for s in snaps]
    tailer = mod.PublicBookTailer(mod.BOOK_DB)
    history: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    try:
        for i, order in enumerate(paper["orders"], 1):
            t = int(order["placed_at_ms"])
            j = bisect.bisect_left(times, t) - 1
            if j < 0:
                continue
            snap = snaps[j]
            cp = int(snap["sampledAtMs"])
            if t - cp > 1500 or t < cp:
                continue
            if not tailer.advance(mid, cp):
                continue
            bf = mod.outcome_book(tailer.book, None)
            if not bf:
                continue
            side = str(order["side"]).upper()
            raw: dict[str, Any] = {}
            raw.update(qs.snapshot_features(snap, bf, side))
            raw.update(qs.placement_features(history, cp, side))
            x = np.asarray([[float(raw.get(f, math.nan)) if raw.get(f) is not None else math.nan for f in bundle["features"]]], dtype=float)
            probs = np.asarray(bundle["model"].predict_proba(x)[0], dtype=float)
            classes = [str(c) for c in bundle["classes"]]
            k = int(np.argmax(probs))
            pred_class = classes[k]
            offset = REP_OFFSET[pred_class]
            bid = bf["up_bid"] if side == "UP" else bf["down_bid"]
            pred_price = round(max(mod.MIN_PRICE, float(bid) - offset * mod.GRID), 2)
            out.append({
                "intentNum": i, "placedAtMs": t, "checkpointMs": cp, "side": side,
                "shares": float(order.get("shares") or mod.SHARES), "paperPrice": float(order["price"]),
                "publicBid": float(bid), "predClass": pred_class, "predOffset": offset,
                "predPriceRaw": pred_price, "probs": {classes[z]: float(probs[z]) for z in range(len(classes))},
            })
            history.append({"at_ms": t, "side": side})
    finally:
        tailer.close()
    return out


def simulate(mid: int, intents: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    events, _, meta = tape_v1.build_archive_events(mid, trade_offset="mid")
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    order_meta: dict[int, dict[str, Any]] = {}
    submits: list[dict[str, Any]] = []
    try:
        for intent in intents:
            t = int(intent["placedAtMs"])
            if int(bt.current_timestamp // 1_000_000) > t:
                continue
            ex.advance_to(bt, t)
            side = str(intent["side"])
            price = float(intent["paperPrice"] if mode == "PAPER_PRICE" else intent["predPriceRaw"])
            if mode == "TARGET_QUEUE_MODEL":
                opp = "DOWN" if side == "UP" else "UP"
                opp_prices = []
                for num, om in order_meta.items():
                    if om["side"] != opp:
                        continue
                    snap = ex.order_snapshot(bt, num)
                    if snap.get("status") in {"NEW", "PARTIALLY_FILLED"} and float(snap.get("leavesQty") or 0.0) > EPS:
                        opp_prices.append(float(om["price"]))
                if opp_prices:
                    price = min(price, float(mod.MAX_PAIR_PRICE_SUM) - max(opp_prices))
                    price = round(math.floor((price + 1e-9) / mod.GRID) * mod.GRID, 2)
                price = max(float(mod.MIN_PRICE), price)
            num = int(intent["intentNum"])
            rc = ex.submit_native(bt, num, side, price, float(intent["shares"]))
            order_meta[num] = {"side": side, "price": price, "shares": float(intent["shares"]), "paperPrice": float(intent["paperPrice"]), "predClass": intent["predClass"]}
            submits.append({"intentNum": num, "atMs": t, "side": side, "price": price, "submitRc": rc, "predClass": intent["predClass"]})
        terminal = int(meta["lastReceivedMs"])
        if int(bt.current_timestamp // 1_000_000) <= terminal:
            ex.advance_to(bt, terminal)
        filled = 0.0
        cost = 0.0
        full = 0
        partial = 0
        none = 0
        rows = []
        for num, om in order_meta.items():
            snap = ex.order_snapshot(bt, num)
            qty = float(om["shares"])
            f = float(snap.get("cumExecQty") or 0.0)
            filled += f
            cost += f * float(om["price"])
            if f >= qty - EPS:
                full += 1
            elif f > EPS:
                partial += 1
            else:
                none += 1
            rows.append({"intentNum": num, **om, "fill": f, "status": snap.get("status"), "leaves": snap.get("leavesQty")})
        desired = sum(float(x["shares"]) for x in intents)
        return {
            "mode": mode, "intents": len(intents), "desiredShares": desired,
            "filledShares": filled, "realizationRate": filled / desired if desired > EPS else None,
            "fillCost": cost, "avgFillPrice": cost / filled if filled > EPS else None,
            "fullIntents": full, "partialIntents": partial, "unfilledIntents": none,
            "fullIntentRate": full / len(intents) if intents else None,
            "submits": submits, "rows": rows,
        }
    finally:
        bt.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", required=True)
    args = ap.parse_args()
    mids = [int(x) for x in args.market_ids.split(",") if x.strip()]
    bundle = joblib.load(ART)
    rows = []
    for i, mid in enumerate(mids, 1):
        intents = build_intents(mid, bundle)
        base = simulate(mid, intents, "PAPER_PRICE")
        skill = simulate(mid, intents, "TARGET_QUEUE_MODEL")
        row = {
            "marketId": mid, "intents": len(intents), "base": base, "skill": skill,
            "deltaFilledShares": float(skill["filledShares"] - base["filledShares"]),
            "deltaRealization": float(skill["realizationRate"] - base["realizationRate"]) if base["realizationRate"] is not None else None,
        }
        rows.append(row)
        print(json.dumps({"progress": i, "marketId": mid, "intents": len(intents), "baseRealization": base["realizationRate"], "skillRealization": skill["realizationRate"], "deltaShares": row["deltaFilledShares"]}, ensure_ascii=False), flush=True)
    desired = sum(r["base"]["desiredShares"] for r in rows)
    bf = sum(r["base"]["filledShares"] for r in rows)
    sf = sum(r["skill"]["filledShares"] for r in rows)
    agg = {
        "markets": len(rows), "intents": sum(r["intents"] for r in rows), "desiredShares": desired,
        "baseFilledShares": bf, "skillFilledShares": sf,
        "baseRealizationRate": bf / desired if desired > EPS else None,
        "skillRealizationRate": sf / desired if desired > EPS else None,
        "deltaFilledShares": sf - bf,
        "marketsSkillBetter": sum(r["deltaFilledShares"] > EPS for r in rows),
        "marketsBaseBetter": sum(r["deltaFilledShares"] < -EPS for r in rows),
        "marketsEqual": sum(abs(r["deltaFilledShares"]) <= EPS for r in rows),
        "baseFullIntentRate": sum(r["base"]["fullIntents"] for r in rows) / max(1, sum(r["intents"] for r in rows)),
        "skillFullIntentRate": sum(r["skill"]["fullIntents"] for r in rows) / max(1, sum(r["intents"] for r in rows)),
    }
    out = OUT / "intent_tape_queue_selection_v0.json"
    out.write_text(json.dumps({"version": "INTENT_TAPE_QUEUE_SELECTION_V0", "researchOnly": True, "liveTradingChanges": False, "dreamFillAllowed": False, "strategyIntentsFrozen": True, "aggregate": agg, "rows": rows}, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(out), "aggregate": agg}, ensure_ascii=False))


if __name__ == "__main__":
    main()
