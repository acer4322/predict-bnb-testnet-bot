from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET_ID = 1513668
ENGINE_DB = ROOT / "data" / "echtgeld_engine_v1.db"
LIVE_DB = ROOT / "data" / "strategy_cap100_echtgeld_v1.db"
PAPER_DB = ROOT / "data" / "strategy_target_compare_v1.db"
OUT = ROOT / "data" / "research" / "cap100_market_1513668_execution_audit_v1.json"
PAPER_VERSION = "UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_FORWARD_PAPER"
LIVE_VERSION = "UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_ECHTGELD_V1"


def rows(db: Path, sql: str, args=()):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def decision_rows(db: Path, version: str):
    out = rows(db, "SELECT * FROM our_decisions WHERE market_id=? AND strategy_version=? ORDER BY decision_ms", (MARKET_ID, version))
    for r in out:
        for k in ("supporting_reasons_json", "arbitration_state_json", "payload_json", "portfolio_state_json"):
            try: r[k[:-5] if k.endswith("_json") else k] = json.loads(r.get(k) or "{}")
            except Exception: r[k[:-5] if k.endswith("_json") else k] = {}
    return out


def fill_rows(db: Path, version: str):
    return rows(db, "SELECT * FROM our_fills WHERE market_id=? AND strategy_version=? ORDER BY filled_at_ms", (MARKET_ID, version))


def order_rows(db: Path, version: str):
    return rows(db, "SELECT * FROM our_orders WHERE market_id=? AND strategy_version=? ORDER BY placed_at_ms", (MARKET_ID, version))


def main():
    engine_events = rows(ENGINE_DB, "SELECT * FROM engine_cap100_events WHERE source_market_id=? ORDER BY seq", (MARKET_ID,))
    engine_orders = rows(ENGINE_DB, "SELECT * FROM engine_cap100_orders WHERE source_market_id=? ORDER BY created_at_ms", (MARKET_ID,))
    live_decisions = decision_rows(LIVE_DB, LIVE_VERSION)
    paper_decisions = decision_rows(PAPER_DB, PAPER_VERSION)
    paper_fills = fill_rows(PAPER_DB, PAPER_VERSION)
    paper_orders = order_rows(PAPER_DB, PAPER_VERSION)

    maker_up = maker_down = taker_up = taker_down = spent = 0.0
    maker_path = []
    for e in engine_events:
        if str(e.get("event_type")) != "FILL_DELTA":
            continue
        sh = float(e.get("delta_shares") or 0)
        usdt = float(e.get("delta_usdt") or 0)
        role = str(e.get("role") or "")
        side = str(e.get("side") or "")
        if role == "MAKER":
            if side == "UP": maker_up += sh
            else: maker_down += sh
            maker_path.append({"ms":e["occurred_at_ms"],"side":side,"deltaShares":sh,"up":maker_up,"down":maker_down,"net":maker_up-maker_down,"seq":e["seq"]})
        elif role == "TAKER":
            if side == "UP": taker_up += sh
            else: taker_down += sh
        spent += usdt

    paper_maker_up = paper_maker_down = 0.0
    paper_path = []
    for f in paper_fills:
        if f["channel"] != "MAKER":
            continue
        sh = float(f["shares"])
        if f["side"] == "UP": paper_maker_up += sh
        else: paper_maker_down += sh
        paper_path.append({"ms":f["filled_at_ms"],"side":f["side"],"deltaShares":sh,"up":paper_maker_up,"down":paper_maker_down,"net":paper_maker_up-paper_maker_down})

    first_paper_fill = min((f["filled_at_ms"] for f in paper_fills if f["channel"]=="MAKER"), default=None)
    first_live_fill = min((e["occurred_at_ms"] for e in engine_events if e["event_type"]=="FILL_DELTA" and e["role"]=="MAKER"), default=None)
    first_live_partial = next((e for e in engine_events if e["event_type"]=="FILL_DELTA" and e["role"]=="MAKER"), None)

    wake = None; unresolved = None; taker_dec = None
    wait_samples = []
    passive_events = []
    for d in live_decisions:
        payload = d.get("payload") or {}
        actions = payload.get("actions") or []
        models = payload.get("models") or {}
        arb = d.get("arbitration_state") or {}
        names = [a.get("action") for a in actions if isinstance(a,dict)]
        if wake is None and "RESIDUAL_ARBITRATION_WAKE" in names: wake = d["decision_ms"]
        if unresolved is None and "UNRESOLVED_GUARD_ENTER" in names: unresolved = d["decision_ms"]
        if "PASSIVE_REPAIR_PRIORITY" in names: passive_events.append({"ms":d["decision_ms"],"actions":actions})
        if d["execution_choice"] == "TAKER" and taker_dec is None: taker_dec = d["decision_ms"]
        if bool(arb.get("readiness")) and d["execution_choice"] == "WAIT":
            wait_samples.append({"ms":d["decision_ms"],"pTaker1s":models.get("pTaker1s"),"pTaker3s":models.get("pTaker3s"),"actions":names})

    taker_received = next((e["occurred_at_ms"] for e in engine_events if e["event_type"]=="ORDER_PLANNED" and e["role"]=="TAKER"), None)
    taker_accepted = next((e["occurred_at_ms"] for e in engine_events if e["event_type"]=="ORDER_ACCEPTED" and e["role"]=="TAKER"), None)
    taker_fill = next((e for e in engine_events if e["event_type"]=="FILL_DELTA" and e["role"]=="TAKER"), None)
    cancel_error = next((e for e in engine_events if e["event_type"]=="CANCEL_REQUEST_ERROR"), None)
    cancel_terminal = next((e for e in engine_events if e["event_type"]=="ORDER_CANCELED"), None)

    paper_taker = next((f for f in paper_fills if f["channel"]=="TAKER"), None)
    first_live_order = engine_orders[0] if engine_orders else None
    real_up = maker_up+taker_up; real_down=maker_down+taker_down
    winner="UP"
    pnl=(real_up if winner=="UP" else real_down)-spent

    report = {
        "marketId": MARKET_ID,
        "rootCause": {
            "firstStateDivergenceMs": first_paper_fill,
            "paperFirstMakerFill": {"ms":first_paper_fill,"shares":18.0,"semantics":"QUEUECLEAR_PASS_FULL_ORDER"},
            "liveFirstMakerFill": {"ms":first_live_fill,"shares":first_live_partial.get("delta_shares") if first_live_partial else None,"state":first_live_partial.get("state") if first_live_partial else None,"semantics":"8781_VENUE_CONFIRMED_DELTA"},
            "why": "paper inventory jumps by full planned order before Echtgeld venue has confirmed the same inventory; subsequent controller state therefore differs",
        },
        "paper": {
            "orders": len(paper_orders), "fills": len(paper_fills), "makerPath": paper_path,
            "taker": paper_taker,
        },
        "echtgeld": {
            "engineOrders": len(engine_orders), "engineEvents": len(engine_events), "makerPath": maker_path,
            "makerUp":maker_up,"makerDown":maker_down,"takerUp":taker_up,"takerDown":taker_down,
            "finalUpShares":real_up,"finalDownShares":real_down,"spentUsdt":spent,"winner":winner,"pnlUsdt":pnl,
            "takerDecisionMs":taker_dec,"takerIntentEnginePlannedMs":taker_received,"takerAcceptedMs":taker_accepted,
            "takerFillMs":taker_fill.get("occurred_at_ms") if taker_fill else None,"takerFillPrice":taker_fill.get("fill_price") if taker_fill else None,
        },
        "arbitration": {
            "residualWakeMs":wake,"unresolvedGuardMs":unresolved,"takerDecisionMs":taker_dec,
            "wakeToTakerMs": (taker_dec-wake) if wake and taker_dec else None,
            "unresolvedToTakerMs": (taker_dec-unresolved) if unresolved and taker_dec else None,
            "waitWhileReady":wait_samples,
            "passiveRepairPriorityEvents":passive_events,
            "existingGateSemantics":"readiness && (not passive_draw || unresolved) && rng<pTaker1s && last_taker_cooldown",
        },
        "rollover": {
            "cancelError":cancel_error,"terminalCancel":cancel_terminal,
            "orphanDurationMs": (cancel_terminal["occurred_at_ms"]-cancel_error["occurred_at_ms"]) if cancel_error and cancel_terminal else None,
        },
        "deterministicReplayInput": {
            "source":"engine_cap100_events FILL_DELTA/order-state + controller decisions",
            "paperQueueClearUsed":False,
        },
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps({
        "out":str(OUT),"paperFirstFill":first_paper_fill,"liveFirstFill":first_live_fill,"wake":wake,"unresolved":unresolved,"takerDecision":taker_dec,
        "realUp":real_up,"realDown":real_down,"spent":spent,"pnl":pnl,"cancelOrphanMs":report["rollover"]["orphanDurationMs"]
    },ensure_ascii=False,indent=2))

if __name__ == "__main__": main()
