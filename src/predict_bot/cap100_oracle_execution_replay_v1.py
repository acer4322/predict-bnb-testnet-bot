from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import unified_controller_cap100_shadow_v1 as base

ROOT = Path(__file__).resolve().parents[2]
LIVE_RECORDER_DB = ROOT / "data" / "strategy_cap100_echtgeld_v1.db"
ENGINE_DB = ROOT / "data" / "echtgeld_engine_v1.db"


class NullRecorder:
    path = Path(":memory:")
    def close(self) -> None: return
    def record_order_cancel(self, **_kw: Any) -> None: return
    def record_order_fill(self, **_kw: Any) -> None: return
    def record_order_placement(self, **_kw: Any) -> None: return
    def record_taker_fill(self, **_kw: Any) -> None: return
    def record_decision(self, **_kw: Any) -> None: return


class OracleExecutionReplayController(base.UnifiedControllerCap100ShadowV1):
    """Diagnostic replay using the first Echtgeld market's Maker lifecycle as oracle.

    This is NOT a deployable simulator.  It exists to prove that the historical
    closed-loop harness can reproduce the Echtgeld strategy path when Maker fills are
    supplied at the venue-confirmed times.  Taker timing is never forced.
    """

    def __init__(self, market_id: int, event_visibility_lead_ms: int = 100) -> None:
        live_flag = os.environ.get("PREDICT_LIVE_ENABLED")
        os.environ["PREDICT_LIVE_ENABLED"] = "false"
        try:
            super().__init__()
        finally:
            if live_flag is None:
                os.environ.pop("PREDICT_LIVE_ENABLED", None)
            else:
                os.environ["PREDICT_LIVE_ENABLED"] = live_flag
        try: self.recorder.close()
        except Exception: pass
        self.recorder = NullRecorder()
        self.market_id_oracle = int(market_id)
        self.event_visibility_lead_ms = int(event_visibility_lead_ms)
        self.active_intervention_required = False
        self.active_intervention_reason: str | None = None
        self.sim_takers: list[dict[str, Any]] = []
        self.applied_events: list[dict[str, Any]] = []
        self.unmatched_intents: list[dict[str, Any]] = []
        self.actual_to_local: dict[str, str] = {}
        self.local_to_actual: dict[str, str] = {}
        self.used_actual_orders: set[str] = set()
        self.event_cursor = 0
        self.actual_orders, self.actual_events = self._load_oracle()

    def _load_oracle(self):
        db = sqlite3.connect(str(LIVE_RECORDER_DB)); db.row_factory = sqlite3.Row
        orders = [dict(r) for r in db.execute(
            """select order_id,side,price,shares,placed_at_ms,status,filled_at_ms,cancelled_at_ms,cancel_reason
               from our_orders where market_id=? and channel='MAKER' order by placed_at_ms,order_id""",
            (self.market_id_oracle,),
        )]
        db.close()
        edb = sqlite3.connect(str(ENGINE_DB)); edb.row_factory = sqlite3.Row
        events = [dict(r) for r in edb.execute(
            """select seq,occurred_at_ms,event_type,client_order_id,role,side,state,delta_shares,delta_usdt,fill_price,detail
               from engine_cap100_events where source_market_id=? and role='MAKER' order by occurred_at_ms,seq""",
            (self.market_id_oracle,),
        )]
        edb.close()
        return orders, events

    def _reset_market(self, market_id: int, sampled_at: int) -> None:
        super()._reset_market(market_id, sampled_at)
        self.active_intervention_required = False
        self.active_intervention_reason = None
        self.event_cursor = 0
        self.actual_to_local.clear(); self.local_to_actual.clear(); self.used_actual_orders.clear()
        self.applied_events.clear(); self.unmatched_intents.clear(); self.sim_takers.clear()

    def _active_intervention_required(self) -> bool:
        return bool(self.active_intervention_required)
    def _on_active_intervention_required(self, reason: str, now: int) -> None:
        self.active_intervention_required = True; self.active_intervention_reason = str(reason)
    def _on_active_intervention_satisfied(self) -> None:
        self.active_intervention_required = False; self.active_intervention_reason = None

    def _best_actual_match(self, side: str, price: float, now: int) -> dict[str, Any] | None:
        cand = []
        for row in self.actual_orders:
            oid = str(row["order_id"])
            if oid in self.used_actual_orders: continue
            if str(row["side"]).upper() != str(side).upper(): continue
            if abs(float(row["price"]) - float(price)) > 1e-9: continue
            dt = abs(int(row["placed_at_ms"]) - int(now))
            if dt <= 6000: cand.append((dt,row))
        return min(cand,key=lambda x:x[0])[1] if cand else None

    def _add_order(self, side: str, now: int, snapshot_ns: int, decision_id: str, reason: str, p: float, snapshot: dict[str, Any], allow_stack: bool=True, bypass_guard: bool=False) -> bool:
        # First let frozen CAP100 determine the actual quote it wants.
        before = set(self.orders)
        made = super()._add_order(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=allow_stack,bypass_guard=bypass_guard)
        if not made: return False
        new_keys = [k for k in self.orders if k not in before]
        if len(new_keys) != 1: return False
        key = new_keys[0]; order = self.orders[key]
        match = self._best_actual_match(order.side, order.price, now)
        if match is None:
            # Oracle harness says this venue intent did not exist in the real run.
            self.orders.pop(key,None)
            self.last_closed[key] = int(now)
            self.current_metrics["makerPlacements"] = max(0,int(self.current_metrics["makerPlacements"])-1)
            self.run_metrics["makerPlacements"] = max(0,int(self.run_metrics["makerPlacements"])-1)
            self.unmatched_intents.append({"atMs":int(now),"side":order.side,"price":float(order.price),"reason":reason})
            return False
        actual_id = str(match["order_id"])
        self.used_actual_orders.add(actual_id)
        self.actual_to_local[actual_id] = order.id
        self.local_to_actual[order.id] = actual_id
        # 8787 receives a synchronous REJECTED result from 8781 and rolls the
        # inherited paper placement back before _add_order returns.  Returning
        # False is important: the parent controller then does NOT consume the
        # subsequent burst RNG draw.
        if str(match.get("status") or "").upper() == "REJECTED":
            self.orders.pop(key, None)
            self.last_closed[key] = int(now)
            self.current_metrics["makerPlacements"] = max(0, int(self.current_metrics["makerPlacements"]) - 1)
            self.run_metrics["makerPlacements"] = max(0, int(self.run_metrics["makerPlacements"]) - 1)
            return False
        return True

    def _find_key_local(self, local_id: str):
        for key,o in self.orders.items():
            if o.id == local_id: return key,o
        return None,None

    def _apply_oracle_fill(self, event: dict[str, Any], now: int) -> dict[str, Any] | None:
        actual_id = str(event.get("client_order_id") or "")
        local_id = self.actual_to_local.get(actual_id)
        if not local_id: return None
        key,order = self._find_key_local(local_id)
        if order is None: return None
        qty = float(base.number(event.get("delta_shares")) or 0.0)
        if qty <= base.EPS: return None
        price = float(base.number(event.get("fill_price")) or order.price)
        pre_net = self.inventory.maker_up-self.inventory.maker_down
        pre_g = self.inventory.maker_up+self.inventory.maker_down
        pre_pc = 2*min(self.inventory.maker_up,self.inventory.maker_down)/pre_g if pre_g>base.EPS else 1.0
        self.inventory.apply({"event_ms":int(event["occurred_at_ms"]),"role":"MAKER","side":order.side,"price":price,"shares":qty})
        self.maker_spent_notional += float(base.number(event.get("delta_usdt")) or (price*qty))
        self.current_metrics["makerFills"] += 1; self.run_metrics["makerFills"] += 1
        state = str(event.get("state") or "").upper()
        if state == "FILLED":
            if key is not None: self.orders.pop(key,None); self.last_closed[key]=int(event["occurred_at_ms"])
        else:
            order.shares=max(0.0,float(order.shares)-qty)
        post_net=self.inventory.maker_up-self.inventory.maker_down
        if order.occupied_before:
            predom="UP" if pre_net>base.EPS else "DOWN" if pre_net<-base.EPS else None
            if predom==order.side and abs(pre_net)>=base.SHARES-base.EPS and abs(post_net)>abs(pre_net)+1:
                self.episode={"kind":"OVERLAP","side":order.side,"risk_start_ms":int(event["occurred_at_ms"]),"risk_pre_abs":abs(pre_net),"start_ms":int(event["occurred_at_ms"]),"pre_abs":abs(pre_net),"start_abs":abs(post_net),"expansion":abs(post_net)-abs(pre_net),"pre_pc":pre_pc,"unresolved":False}
                self.readiness=False; self.current_metrics["excursions"]+=1; self.run_metrics["excursions"]+=1
        row={"atMs":int(event["occurred_at_ms"]),"side":order.side,"price":price,"shares":qty,"state":state,"actualOrderId":actual_id}
        self.applied_events.append(row); return row

    def _fill_orders(self, snapshot: dict[str, Any], now: int) -> list[dict[str, Any]]:
        out=[]
        while self.event_cursor < len(self.actual_events) and int(self.actual_events[self.event_cursor]["occurred_at_ms"]) <= int(now) + self.event_visibility_lead_ms:
            e=self.actual_events[self.event_cursor]; self.event_cursor += 1
            if str(e.get("event_type") or "").upper()=="FILL_DELTA":
                row=self._apply_oracle_fill(e,now)
                if row: out.append({"side":row["side"],"price":row["price"],"shares":row["shares"],"oracle":True})
            elif str(e.get("event_type") or "").upper()=="ORDER_REJECTED":
                aid=str(e.get("client_order_id") or ""); lid=self.actual_to_local.get(aid)
                if lid:
                    key,o=self._find_key_local(lid)
                    if key is not None: self.orders.pop(key,None); self.last_closed[key]=int(e["occurred_at_ms"])
        return out

    def _record_taker(self, side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str,Any], p1: float,p3: float,ppass: float,pred_effect: str) -> bool:
        ok=super()._record_taker(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect)
        if ok:
            self.sim_takers.append({"atMs":int(now),"side":side,"price":float(price),"decisionId":decision_id})
            self._on_active_intervention_satisfied()
        return ok


def load_snapshots(market_id:int)->list[dict[str,Any]]:
    db=sqlite3.connect(str(LIVE_RECORDER_DB)); db.row_factory=sqlite3.Row
    rows=db.execute("select public_state_json from our_decisions where market_id=? order by decision_ms",(int(market_id),)).fetchall(); db.close()
    out=[]; seen=set()
    for r in rows:
        x=json.loads(str(r["public_state_json"] or "{}")); ms=int(base.number(x.get("sampledAtMs")) or 0)
        if ms and ms not in seen: seen.add(ms); out.append(x)
    return sorted(out,key=lambda x:int(base.number(x.get("sampledAtMs")) or 0))


def run_oracle(market_id:int=1513668)->dict[str,Any]:
    snaps=load_snapshots(market_id)
    ctl=OracleExecutionReplayController(market_id); ctl.excluded_deployment_market_id=-1
    try:
        for s in snaps: ctl._step(dict(s))
        start=int(base.number(snaps[0].get("bucketStartSec")) or 0)*1000
        t=ctl.sim_takers[0] if ctl.sim_takers else None
        return {"marketId":market_id,"snapshots":len(snaps),"matchedMakerOrders":len(ctl.used_actual_orders),"actualMakerOrders":len(ctl.actual_orders),"appliedFillEvents":len(ctl.applied_events),"unmatchedIntentCount":len(ctl.unmatched_intents),"unmatchedIntents":ctl.unmatched_intents[:20],"firstTakerElapsedSec":((int(t["atMs"])-start)/1000.0) if t else None,"firstTaker":t,"portfolio":{k:v for k,v in ctl.inventory.features(int(base.number(snaps[-1].get("sampledAtMs")) or 0)).items() if k!="_combined_net"}}
    finally: ctl.stop()

if __name__=="__main__": print(json.dumps(run_oracle(),ensure_ascii=False,indent=2,default=str))
