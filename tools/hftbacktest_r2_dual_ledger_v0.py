from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import HftBookAdapter, load_public_snapshots, load_reference_paper, new_controller
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from src.predict_bot import unified_controller_paper_v2 as mod

OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
EPS = 1e-9


def run_market(mid: int) -> dict[str, Any]:
    snaps = load_public_snapshots(mid)
    if not snaps:
        raise RuntimeError(f"no snapshots for {mid}")
    paper = load_reference_paper(mid)
    win = winners([mid]).get(mid)
    a = HftBookAdapter(mid, 1092, 273, "risk", "mid")
    c = new_controller(a)

    # Strategy/commitment ledger: c.inventory + c.orders use the frozen paper semantics.
    # Actual ledger: only real HftBacktest fills are applied here.
    actual = mod.Inventory()
    actual_taker_fees = 0.0
    exec_orders: dict[int, dict[str, Any]] = {}
    fill_events: list[dict[str, Any]] = []
    submit_events: list[dict[str, Any]] = []

    orig_add = c._add_order
    orig_taker = c._record_taker

    def submit_exec(role: str, side: str, price: float, shares: float, at_ms: int, intent_id: str) -> None:
        num = int(a.next_num)
        a.next_num += 1
        if role == "MAKER":
            rc = ex.submit_native(a.bt, num, side, price, shares)
        else:
            max_price = min(0.99, float(price) + 0.02)
            native_side, native_price = ex.native_order(side, max_price)
            if native_side == "BUY":
                rc = int(a.bt.submit_buy_order(0, num, native_price, float(shares), ex.hbt.GTC, ex.LIMIT, False))
            else:
                rc = int(a.bt.submit_sell_order(0, num, native_price, float(shares), ex.hbt.GTC, ex.LIMIT, False))
        exec_orders[num] = {
            "role": role, "side": side, "price": float(price), "shares": float(shares),
            "atMs": int(at_ms), "intentId": intent_id, "prevCum": 0.0, "submitRc": int(rc),
        }
        submit_events.append({"orderNum": num, **exec_orders[num]})

    def add_wrap(side: str, now: int, snapshot_ns: int, decision_id: str, reason: str, p: float,
                 snapshot: dict[str, Any], allow_stack: bool = True, bypass_guard: bool = False) -> bool:
        before = set(c.orders)
        made = orig_add(side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack, bypass_guard)
        if made:
            new_keys = list(set(c.orders) - before)
            if new_keys:
                order = c.orders[new_keys[0]]
                submit_exec("MAKER", order.side, order.price, order.shares, now, order.id)
        return made

    def taker_wrap(side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any],
                   p1: float, p3: float, ppass: float, pred_effect: str) -> None:
        # Preserve exactly what Strategy Brain used to believe happened.
        orig_taker(side, price, now, decision_id, snapshot, raw, p1, p3, ppass, pred_effect)
        submit_exec("TAKER", side, price, mod.SHARES, now, decision_id + ":TAKER_INTENT")

    c._add_order = add_wrap
    c._record_taker = taker_wrap

    def harvest(observed_ms: int) -> None:
        nonlocal actual_taker_fees
        for num, om in exec_orders.items():
            snap = ex.order_snapshot(a.bt, num)
            cum = float(snap.get("cumExecQty") or 0.0)
            old = float(om.get("prevCum") or 0.0)
            if cum <= old + EPS:
                continue
            delta = cum - old
            native_px = snap.get("execPrice")
            px = float(om["price"])
            if native_px is not None and math.isfinite(float(native_px)):
                px = float(native_px) if om["side"] == "UP" else 1.0 - float(native_px)
            fill_ms = int((snap.get("exchangeTs") or observed_ms * 1_000_000) // 1_000_000)
            actual.apply({"event_ms": fill_ms, "role": om["role"], "side": om["side"], "price": px, "shares": delta})
            if om["role"] == "TAKER":
                actual_taker_fees += mod.taker_fee(delta, px, mod.FEE_BPS)
            om["prevCum"] = cum
            fill_events.append({
                "orderNum": num, "intentId": om["intentId"], "role": om["role"], "side": om["side"],
                "atMs": fill_ms, "observedAtMs": int(observed_ms), "price": px, "deltaShares": delta,
                "cumShares": cum, "status": snap.get("status"),
            })

    try:
        for s in snaps:
            # Strategy clock drives both ledgers; actual fills never feed back into c.inventory.
            c._step(dict(s))
            harvest(int(s["sampledAtMs"]))
        terminal = int(a.meta["lastReceivedMs"])
        if int(a.bt.current_timestamp // 1_000_000) <= terminal:
            ex.advance_to(a.bt, terminal)
        harvest(terminal)

        shadow = c.inventory.features(terminal)
        shadow.pop("_combined_net", None)
        actual_features = actual.features(terminal)
        actual_features.pop("_combined_net", None)

        maker_intents = [x for x in exec_orders.values() if x["role"] == "MAKER"]
        taker_intents = [x for x in exec_orders.values() if x["role"] == "TAKER"]
        maker_desired = sum(float(x["shares"]) for x in maker_intents)
        maker_filled = sum(float(x["deltaShares"]) for x in fill_events if x["role"] == "MAKER")
        taker_filled = sum(float(x["deltaShares"]) for x in fill_events if x["role"] == "TAKER")
        maker_rejects = sum(int(x["submitRc"] != 0) for x in maker_intents)
        taker_rejects = sum(int(x["submitRc"] != 0) for x in taker_intents)

        pnl = None
        if win in {"UP", "DOWN"}:
            payout = (actual.maker_up + actual.taker_up) if win == "UP" else (actual.maker_down + actual.taker_down)
            pnl = float(actual.cash + payout - actual_taker_fees)

        return {
            "marketId": mid, "winner": win,
            "paperReference": {"makerOrders": len(paper["orders"]), "takerFills": len(paper["takers"])},
            "shadowStrategy": {
                "makerPlacements": int(c.run_metrics["makerPlacements"]),
                "makerVirtualFills": int(c.run_metrics["makerFills"]),
                "takerVirtualFills": int(c.run_metrics["takerFills"]),
                "finalPortfolio": shadow,
            },
            "actualExecution": {
                "makerIntents": len(maker_intents), "takerIntents": len(taker_intents),
                "makerDesiredShares": maker_desired, "makerFilledShares": maker_filled,
                "makerRealizationRate": maker_filled / maker_desired if maker_desired > EPS else None,
                "takerFilledShares": taker_filled, "makerSubmitRejects": maker_rejects,
                "takerSubmitRejects": taker_rejects, "takerFeesUsdt": actual_taker_fees,
                "realizedPnl": pnl, "finalPortfolio": actual_features,
            },
            "intentCountDeltaVsPaper": int(c.run_metrics["makerPlacements"]) - len(paper["orders"]),
            "fills": fill_events,
            "submits": submit_events,
        }
    finally:
        a.close()


def max_drawdown(xs: list[float]) -> float:
    peak = 0.0
    cur = 0.0
    dd = 0.0
    for x in xs:
        cur += x
        peak = max(peak, cur)
        dd = max(dd, peak - cur)
    return dd


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
            "progress": i, "marketId": mid,
            "paperIntents": r["paperReference"]["makerOrders"],
            "shadowIntents": r["shadowStrategy"]["makerPlacements"],
            "actualMakerRealization": r["actualExecution"]["makerRealizationRate"],
            "pnl": r["actualExecution"]["realizedPnl"],
        }, ensure_ascii=False), flush=True)

    pnls = [float(r["actualExecution"]["realizedPnl"]) for r in rows if r["actualExecution"]["realizedPnl"] is not None]
    desired = sum(float(r["actualExecution"]["makerDesiredShares"]) for r in rows)
    filled = sum(float(r["actualExecution"]["makerFilledShares"]) for r in rows)
    wins_n = sum(x > EPS for x in pnls)
    losses_n = sum(x < -EPS for x in pnls)
    agg = {
        "markets": len(rows),
        "paperMakerIntents": sum(int(r["paperReference"]["makerOrders"]) for r in rows),
        "shadowMakerIntents": sum(int(r["shadowStrategy"]["makerPlacements"]) for r in rows),
        "intentCountDeltaVsPaper": sum(int(r["intentCountDeltaVsPaper"]) for r in rows),
        "makerDesiredShares": desired, "makerFilledShares": filled,
        "makerRealizationRate": filled / desired if desired > EPS else None,
        "totalPnl": sum(pnls), "wins": wins_n, "losses": losses_n,
        "winRate": wins_n / len(pnls) if pnls else None,
        "maxCumulativeDrawdown": max_drawdown(pnls),
        "preliminaryGraduationThreshold": {"positivePnl": True, "winRateAtLeast": 0.50},
        "preliminaryPassedOnThisDiagnostic": bool(pnls and sum(pnls) > 0 and wins_n / len(pnls) >= 0.50),
    }
    out = OUT / "r2_dual_ledger_v0.json"
    out.write_text(json.dumps({
        "version": "R2_DUAL_LEDGER_V0", "researchOnly": True, "liveTradingChanges": False,
        "strategyLedgerUsesFrozenPaperSemantics": True,
        "actualLedgerUsesOnlyHftBacktestFills": True,
        "actualFillsFeedStrategyBrain": False,
        "dreamFillUsedForPnL": False,
        "aggregate": agg, "rows": rows,
    }, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(out), "aggregate": agg}, ensure_ascii=False))


if __name__ == "__main__":
    main()
