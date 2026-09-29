from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DB = ROOT / "data" / "echtgeld_engine_v1.db"
STRATEGY_DB = ROOT / "data" / "strategy_cap100_echtgeld_v1.db"
OUT = ROOT / "data" / "research" / "cap100_market_1513668_slow_execution_screen_v1.json"
MARKET_ID = 1513668
SETTLE_MS = 1500


def main() -> int:
    s = sqlite3.connect(STRATEGY_DB); s.row_factory = sqlite3.Row
    e = sqlite3.connect(ENGINE_DB); e.row_factory = sqlite3.Row
    try:
        plans = [dict(r) for r in s.execute(
            "SELECT order_id,side,price,shares,placed_at_ms FROM our_orders WHERE market_id=? AND channel='MAKER' ORDER BY placed_at_ms,order_id",
            (MARKET_ID,),
        )]
        eng = {str(r["client_order_id"]): dict(r) for r in e.execute(
            "SELECT * FROM engine_cap100_orders WHERE source_market_id=? AND role='MAKER'",
            (MARKET_ID,),
        )}
        selected=[]; blocked=[]; gate_until=-1
        for p in plans:
            at=int(p["placed_at_ms"])
            if at < gate_until:
                blocked.append({**p,"blockReason":"PREVIOUS_MAKER_LIFECYCLE_NOT_SETTLED","gateUntilMs":gate_until})
                continue
            row=eng.get(str(p["order_id"]))
            if row is None:
                blocked.append({**p,"blockReason":"NO_ENGINE_LIFECYCLE_ROW"})
                continue
            terminal=int(row.get("completed_at_ms") or 0)
            if terminal <= 0:
                # A resting order with no terminal proof blocks all later ordinary Maker.
                gate_until=10**30
            else:
                gate_until=terminal+SETTLE_MS
            selected.append({
                **p,
                "observedEngineState":row.get("state"),
                "observedFilledShares":float(row.get("filled_share_qty") or 0.0),
                "observedFilledUsdt":float(row.get("filled_usdt_amount") or 0.0),
                "observedTerminalMs":terminal or None,
                "nextMakerEligibleMs":gate_until if gate_until < 10**29 else None,
            })
        up=sum(x["observedFilledShares"] for x in selected if x["side"]=="UP")
        down=sum(x["observedFilledShares"] for x in selected if x["side"]=="DOWN")
        spent=sum(x["observedFilledUsdt"] for x in selected)
        maker_pnl=up-spent  # actual winner was UP
        payload={
            "marketId":MARKET_ID,
            "kind":"COUNTERFACTUAL_PACING_SCREEN_NOT_VENUE_REPLAY",
            "warning":"Uses original Echtgeld order lifecycle outcomes only to screen which original Maker intents survive serialization. Skipping earlier orders changes the counterfactual path, so retained order fills are not claimed as causal replay truth.",
            "policy":{
                "oneMakerGenerationAtATime":True,
                "newMakerBlockedWhileRestingOrPartial":True,
                "postTerminalSettleMs":SETTLE_MS,
                "activeTakerMayPreempt":True,
            },
            "originalMakerIntents":len(plans),
            "selectedMakerIntents":len(selected),
            "blockedMakerIntents":len(blocked),
            "selected":selected,
            "blocked":blocked,
            "observedLifecycleScreen":{
                "upShares":up,"downShares":down,"spentUsdt":spent,"makerOnlyPnlIfObservedFillsHeldAndWinnerUp":maker_pnl,
            },
            "activeIntervention":{
                "requiredAtMs":1787223697757,
                "controllerObservedUpAsk":0.84,
                "counterfactualVenueFillPrice":None,
                "note":"Slow variant would allow ACTIVE TAKER to preempt Maker pacing. Actual counterfactual Binance fill price is unavailable from historical data.",
            },
        }
        OUT.parent.mkdir(parents=True,exist_ok=True)
        OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({
            "out":str(OUT),"original":len(plans),"selected":len(selected),"blocked":len(blocked),
            "screenUp":up,"screenDown":down,"screenSpent":spent,"screenMakerPnl":maker_pnl,
            "selectedTimes":[x["placed_at_ms"] for x in selected],
        },ensure_ascii=False,indent=2))
    finally:
        s.close(); e.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
