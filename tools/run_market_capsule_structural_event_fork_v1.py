from __future__ import annotations

import argparse, json, math, os, sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path.cwd().resolve() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools import hftbacktest_execution_shift_audit_v0 as ex
try:
    from tools.hft_execution_event_cache_v1 import ExecutionEventCache
except ImportError:
    from hft_execution_event_cache_v1 import ExecutionEventCache

ENTRY_LATENCY_MS=1092; RESPONSE_LATENCY_MS=273; QUEUE_MODEL="risk"; TRADE_OFFSET="mid"; EPS=1e-9
API_WAIT_NS=1_000_000_000_000
TERMINAL={"FILLED","CANCELED","EXPIRED","REJECTED"}

def finite(v, default=0.0):
    try:
        x=float(v); return x if math.isfinite(x) else default
    except Exception: return default

def book(bt):
    d=bt.depth(0); snap=d.snapshot()
    try:
        bids={}; asks={}
        for row in snap:
            q=float(row["qty"])
            if q<=EPS: continue
            p=round(float(row["px"]),12); ev=int(row["ev"])
            if ev & int(ex.BUY_EVENT): bids[p]=q
            elif ev & int(ex.SELL_EVENT): asks[p]=q
        return bids,asks
    finally: d.snapshot_free(snap)

def quotes(bt):
    bids,asks=book(bt)
    if not bids or not asks: return None
    nb=max(bids); na=min(asks)
    q={"UP":{"bid":nb,"ask":na},"DOWN":{"bid":1-na,"ask":1-nb}}
    return q if all(0<q[s][k]<1 for s in q for k in q[s]) else None

def ceil_cent(v): return math.ceil(v*100-1e-12)/100
def legal_qty(p,q): return max(ceil_cent(q),ceil_cent(1/max(.01,min(.99,p))))

def candidate_actions(seam):
    bid=finite(seam["receipt_strict_best_bid"]); ask=finite(seam["receipt_strict_best_ask"])
    if not (0<bid<ask<1): raise RuntimeError("invalid strict book")
    ob={"UP":bid,"DOWN":1-ask}; oa={"UP":ask,"DOWN":1-bid}
    out=[{"name":"WAIT","kind":"WAIT"}]
    for qty in (2.,5.,10.):
        for side in ("UP","DOWN"):
            p=ob[side]; out.append({"name":f"PASSIVE_{side}_{int(qty)}","kind":"PASSIVE","side":side,"price":p,"baseQty":qty,"qty":legal_qty(p,qty)})
    for side in ("UP","DOWN"):
        a=oa[side]; p=min(.99,a+.02); out.append({"name":f"ACTIVE_{side}_2","kind":"ACTIVE","side":side,"price":p,"referenceAsk":a,"baseQty":2.,"qty":legal_qty(p,2.)})
    return out

def submit(bt,oid,a):
    if a["kind"]=="PASSIVE": return ex.submit_native(bt,oid,a["side"],a["price"],a["qty"])
    ns,np=ex.native_order(a["side"],a["price"])
    return int(bt.submit_buy_order(0,oid,np,a["qty"],ex.hbt.GTC,ex.LIMIT,False)) if ns=="BUY" else int(bt.submit_sell_order(0,oid,np,a["qty"],ex.hbt.GTC,ex.LIMIT,False))

def econ(u,d,c,side,q,p):
    u+=q if side=="UP" else 0; d+=q if side=="DOWN" else 0; c+=q*p; pu=u-c; pd=d-c
    return {"postUpShares":u,"postDownShares":d,"postNetCost":c,"postPnlIfUp":pu,"postPnlIfDown":pd,"postFloor":min(pu,pd),"postUpside":max(pu,pd)}

def changed(q0,q1,side): return abs(q0[side]["bid"]-q1[side]["bid"])>EPS or abs(q0[side]["ask"]-q1[side]["ask"])>EPS

def obs_ms(snap,fallback):
    try:
        x=int(snap.get("localTs")); return x//1_000_000 if x>0 else fallback
    except Exception: return fallback

def run_action(seam,a,cache,store):
    mid=int(seam["market_id"]); t0=int(seam["action_event_ms"])
    if mid not in cache: cache[mid]=store.get(mid)
    events,meta=cache[mid]; bt=ex.new_bt(events,entry_latency_ms=ENTRY_LATENCY_MS,response_latency_ms=RESPONSE_LATENCY_MS,queue_model=QUEUE_MODEL); ex.initialize_bt(bt)
    try:
        if int(bt.current_timestamp//1_000_000)>t0 or not ex.advance_to(bt,t0): return {"error":"cannot_reach_decision","marketId":mid}
        q_decision=quotes(bt)
        if q_decision is None: return {"error":"no_book_at_decision","marketId":mid}
        pu=finite(seam.get("pre_up_shares")); pd=finite(seam.get("pre_down_shares")); pc=finite(seam.get("pre_net_cost")); pf=finite(seam.get("pre_floor")); pb=finite(seam.get("pre_upside"))

        # WAIT is a no-order structural baseline only; it is excluded from execution-teacher rows.
        if a["kind"]=="WAIT":
            ek=None; q1=None; te=None; steps=0
            while True:
                r=int(bt.wait_next_feed(True,API_WAIT_NS))
                if r==1: break
                steps+=1; now=int(bt.current_timestamp//1_000_000); q1=quotes(bt)
                if q1 is not None and (changed(q_decision,q1,"UP") or changed(q_decision,q1,"DOWN")):
                    ek="QUOTE_STATE_CHANGE"; te=now; break
            if ek is None: ek="CENSORED"; te=int(bt.current_timestamp//1_000_000); q1=quotes(bt)
            post=econ(pu,pd,pc,"UP",0,0)
            return {"marketId":mid,"seamId":seam["seam_id"],"decisionMs":t0,"action":a,"submitRc":None,"strictBookAtDecision":q_decision,"materializationMs":None,"preMaterializationQuoteChanged":None,"eventKind":ek,"eventMs":te,"eventLagMs":te-t0,"feedStepsToEvent":steps,"fillQtyAtEvent":0.0,"orderStatusAtEvent":None,"candidateAcknowledgedAtEvent":None,"eventBeforeExchangeAck":None,"eventBook":q1,"deltaFloorAtEvent":0.0,"deltaUpsideAtEvent":0.0,**post,"tape":meta}

        oid=1; side=a["side"]; rc=submit(bt,oid,a)
        # This is a physical execution-model boundary, not a learned policy timer: wait until the
        # candidate's configured entry+response latency has elapsed so the controller can observe
        # whether the carrier materialized/filled/rejected. Pre-materialization market drift is
        # recorded only as context/diagnostic and is never itself the action label.
        materialization_ms=t0+ENTRY_LATENCY_MS+RESPONSE_LATENCY_MS
        if not ex.advance_to(bt,materialization_ms):
            return {"error":"feed_exhausted_before_materialization","marketId":mid,"decisionMs":t0,"materializationMs":materialization_ms}
        q_mat=quotes(bt)
        if q_mat is None: return {"error":"no_book_at_materialization","marketId":mid,"decisionMs":t0}
        pre_drift=changed(q_decision,q_mat,side)
        snap=ex.order_snapshot(bt,oid)
        ek=None; te=None; q1=q_mat; steps=0
        if finite(snap.get("cumExecQty"))>EPS:
            ek="FILL"; te=materialization_ms
        elif str(snap.get("status")) in TERMINAL:
            ek="TERMINAL"; te=materialization_ms
        else:
            while True:
                r=int(bt.wait_next_feed(True,API_WAIT_NS))
                if r==1: break
                steps+=1; now=int(bt.current_timestamp//1_000_000); q1=quotes(bt); snap=ex.order_snapshot(bt,oid)
                if finite(snap.get("cumExecQty"))>EPS:
                    ek="FILL"; te=now; break
                if str(snap.get("status")) in TERMINAL:
                    ek="TERMINAL"; te=now; break
                if q1 is not None and changed(q_mat,q1,side):
                    ek="QUOTE_STATE_CHANGE"; te=now; break
        if ek is None: ek="CENSORED"; te=int(bt.current_timestamp//1_000_000); snap=ex.order_snapshot(bt,oid); q1=quotes(bt)
        fq=finite((snap or {}).get("cumExecQty")); post=econ(pu,pd,pc,side,fq,a["price"]); df=post["postFloor"]-pf; du=post["postUpside"]-pb
        ack=False
        try: ack=int((snap or {}).get("exchangeTs"))>0 and int((snap or {}).get("exchangeTs"))<=te*1_000_000
        except Exception: pass
        return {"marketId":mid,"seamId":seam["seam_id"],"decisionMs":t0,"action":a,"submitRc":rc,"strictBookAtDecision":q_decision,"materializationMs":materialization_ms,"materializationLagMsDiagnostic":materialization_ms-t0,"bookAtMaterialization":q_mat,"preMaterializationQuoteChanged":pre_drift,"eventKind":ek,"eventMs":te,"eventLagMs":te-materialization_ms,"eventLagFromDecisionMsDiagnostic":te-t0,"feedStepsToEvent":steps,"fillQtyAtEvent":fq,"orderStatusAtEvent":(snap or {}).get("status"),"candidateAcknowledgedAtEvent":ack,"eventBeforeExchangeAck":not ack,"eventBook":q1,"deltaFloorAtEvent":df,"deltaUpsideAtEvent":du,**post,"tape":meta}
    finally: bt.close()

def validate(s):
    e=[]; t=int(s["action_event_ms"])
    if not int(s["receipt_strict_book_received_ms"])<t: e.append("receipt_not_past")
    if not int(s["public_sampled_at_ms"])<t: e.append("public_not_past")
    if finite(s.get("seconds_left"))<=180: e.append("seconds_left")
    return e

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--seams",required=True); ap.add_argument("--tape-dir",required=True); ap.add_argument("--output",required=True); ap.add_argument("--limit",type=int); ns=ap.parse_args()
    src=json.loads(Path(ns.seams).read_text(encoding="utf-8")); seams=list(src.get("rows") or [])[:ns.limit] if ns.limit else list(src.get("rows") or [])
    store=ExecutionEventCache(tape_dir=Path(ns.tape_dir),cache_dir=ROOT/"data"/"research"/"hft_execution_event_cache_v1",trade_offset=TRADE_OFFSET); cache={}; rows=[]; audits=[]
    for i,s in enumerate(seams,1):
        errs=validate(s); audits.append({"marketId":int(s["market_id"]),"seamId":s["seam_id"],"errors":errs})
        if not errs:
            for a in candidate_actions(s):
                try: rows.append(run_action(s,a,cache,store))
                except Exception as x: rows.append({"marketId":int(s["market_id"]),"seamId":s["seam_id"],"action":a,"error":f"{type(x).__name__}: {x}"})
        print(json.dumps({"progress":i,"total":len(seams),"marketId":int(s["market_id"])}),flush=True)
    valid=[r for r in rows if "error" not in r]; acts=[r for r in valid if r.get("action",{}).get("kind")!="WAIT"]; c=Counter(str(r.get("eventKind")) for r in acts); l=sorted(r["eventLagMs"] for r in acts if r.get("eventKind")!="CENSORED")
    gate={"allSeamsStrictPast":all(not x["errors"] for x in audits),"zeroRunnerErrors":not any("error" in r for r in rows),"noDreamFill":True,"noFixedPrimaryHorizon":True,"eventObserved":any(r.get("eventKind") in {"FILL","TERMINAL","QUOTE_STATE_CHANGE"} for r in acts)}; gate["pass"]=all(gate.values())
    outp={"version":"MARKET_CAPSULE_STRUCTURAL_EVENT_FORK_V1_RESULT_20260907","researchOnly":True,"seams":len(seams),"runs":len(rows),"validRuns":len(valid),"actionRuns":len(acts),"eventCounts":dict(c),"medianEventLagMsDiagnostic":l[len(l)//2] if l else None,"errors":[r for r in rows if "error" in r],"seamAudits":audits,"rows":rows,"plumbingGate":gate,"execution":{"fixedPrimaryHorizonMs":None,"activeFixedTtlUsedForLabel":False,"apiWaitTimeoutNsNotPolicy":API_WAIT_NS,"dreamFillAllowed":False}}
    out=Path(os.environ["BTC5M_LAN_RESULT_DIR"])/"result.json" if ns.output.upper()=="AUTO" else Path(ns.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(outp,indent=2,ensure_ascii=False),encoding="utf-8"); print(json.dumps({"ok":True,"eventCounts":dict(c),"medianEventLagMsDiagnostic":outp["medianEventLagMsDiagnostic"],"plumbingGate":gate},indent=2),flush=True); return 0
if __name__=="__main__": raise SystemExit(main())
