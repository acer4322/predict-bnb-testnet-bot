from __future__ import annotations
import argparse, importlib.util, json, os, socket, sys
from pathlib import Path

R95 = Path(r"C:/BTC5M-worker/.lan_worker_v1/staging/btc5m_repair_add_factorial_20260922_r95")
if str(R95) not in sys.path:
    sys.path.insert(0, str(R95))

def module(name, path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

class NeedDecision(RuntimeError):
    def __init__(self,snapshot):
        super().__init__("NEED_DECISION")
        self.snapshot=snapshot

def clean_owner(x):
    return {k:x.get(k) for k in ("key","side","route","qty","limit","state","transport") if k in x}

def compact_book(b):
    if not b: return None
    return {
        "source_ms":b.get("source_ms"),"received_ms":b.get("received_ms"),
        "best_bid":b.get("best_bid"),"best_ask":b.get("best_ask"),
        "bids":(b.get("bids") or [])[:5],"asks":(b.get("asks") or [])[:5],
    }

def prompt_signature(o, classified, ev):
    # Management epoch = confirmed portfolio / owner lifecycle plus natural economic-state changes.
    # Pure quote flicker is ignored, but we re-open the decision when candidate floor economics
    # or side belief crosses a natural sign boundary.
    inv=o.get("inv") or {}
    owners=tuple(sorted((str(x.get("key")),str(x.get("side")),str(x.get("route")),str(x.get("state")),round(float(x.get("qty") or 0),4))
        for x in (o.get("owners") or [])))
    pi=(ev or {}).get("pi") or {}
    roles=[]
    for x in classified:
        g=x.get("geometry") or {}
        df=float(g.get("d_floor") or 0); db=float(g.get("d_best") or 0)
        econ=("FLOOR_IMPROVE" if df>1e-8 else "FLOOR_NEUTRAL" if df>=-1e-8 else "FLOOR_SPEND_UPSIDE" if db>1e-8 else "DOMINATED")
        favored=float(pi.get(x["op"].get("side"),.5))>.5+1e-12
        roles.append((x["role"],str(x["op"].get("side")),str(x["op"].get("route")),econ,favored))
    return (round(float(inv.get("UP") or 0),4),round(float(inv.get("DOWN") or 0),4),round(float(o.get("cost") or 0),4),owners,tuple(sorted(roles)))

def make_manual_policy(parent):
    class ManualPolicy(parent):
        last_instance=None
        supplied=[]
        def __init__(self,arm,latency_ms):
            super().__init__(arm,latency_ms)
            self.manual_i=0
            self.manual_log=[]
            self.last_prompt_sig=None
            type(self).last_instance=self

        def _classify(self,o,ops):
            inv=o.get("inv") or {}
            u=float(inv.get("UP") or 0); d=float(inv.get("DOWN") or 0)
            weak="DOWN" if u>d+1e-8 else "UP" if d>u+1e-8 else None
            strong="UP" if weak=="DOWN" else "DOWN" if weak=="UP" else None
            current_pay={k:float(v) for k,v in (o.get("payoff") or {}).items()}
            current_floor=min(current_pay.values()) if current_pay else 0.
            current_best=max(current_pay.values()) if current_pay else 0.
            out=[]
            for op in ops:
                if op.get("kind")!="NEW": continue
                side=op.get("side")
                if weak and side==weak: role="REPAIR"
                elif strong and side==strong: role="REEXPAND"
                else: role="FORMATION"
                q=float(op.get("qty") or 0); p=float(op.get("price") or 0); route=str(op.get("route"))
                fee=0.
                if route=="ACTIVE":
                    from net_world import share_fee
                    fee=float(share_fee(q,p))
                net=q-fee
                after_inv={"UP":u,"DOWN":d}; after_inv[side]+=net
                after_cost=float(o.get("cost") or 0)+q*p
                after_pay={k:after_inv[k]-after_cost for k in ("UP","DOWN")}
                geometry={"after_payoff":after_pay,"after_floor":min(after_pay.values()),"after_best":max(after_pay.values()),
                    "d_floor":min(after_pay.values())-current_floor,"d_best":max(after_pay.values())-current_best,
                    "net_received_qty":net,"conditional_fee_shares":fee}
                out.append({"role":role,"op":dict(op),"geometry":geometry})
            return weak,strong,out

        def _snapshot(self,o,ops,ev,classified):
            inv={k:float(v) for k,v in (o.get("inv") or {}).items()}
            payoff={k:float(v) for k,v in (o.get("payoff") or {}).items()}
            weak,strong,_=self._classify(o,ops)
            candidates=[]
            for z in classified:
                op=z["op"]
                candidates.append({
                    "role":z["role"],"kind":op.get("kind"),"side":op.get("side"),"route":op.get("route"),
                    "price":op.get("price"),"qty":op.get("qty"),"value":op.get("value"),
                    "blocked":op.get("blocked"),"geometry":z.get("geometry"),
                })
            return {
                "decision_index":self.manual_i,
                "now_ms":int(o.get("now_ms")),
                "remaining_seconds":float(o.get("remaining_seconds")),
                "inventory":inv,"gross_inventory":o.get("gross_inv"),
                "cost":float(o.get("cost") or 0),"payoff":payoff,
                "floor":min(payoff.values()) if payoff else None,
                "best":max(payoff.values()) if payoff else None,
                "weak_side":weak,"strong_side":strong,
                "reserved_cash":float(o.get("reserved_cash") or 0),
                "available_cash":o.get("available_cash"),
                "source_age_seconds":o.get("source_age_seconds"),
                "receive_age_seconds":o.get("receive_age_seconds"),
                "book":compact_book(o.get("book")),
                "owners":[clean_owner(x) for x in (o.get("owners") or [])],
                "candidates":candidates,
                "automatic_cancels":[dict(x) for x in ops if x.get("kind")=="CANCEL"],
                "native_evidence":{
                    "major":ev.get("major") if isinstance(ev,dict) else None,
                    "sigma":ev.get("sigma") if isinstance(ev,dict) else None,
                    "age_charge":ev.get("age_charge") if isinstance(ev,dict) else None,
                    "tau":ev.get("tau") if isinstance(ev,dict) else None,
                    "pi":ev.get("pi") if isinstance(ev,dict) else None,
                    "active":ev.get("active") if isinstance(ev,dict) else None,
                    "rejections":ev.get("rejections") if isinstance(ev,dict) else None,
                },
                "allowed_actions":["NATIVE","HOLD","REPAIR","REEXPAND"],
                "hidden":["TARGET_ACTIONS","TARGET_INVENTORY","WINNER","SETTLEMENT","FUTURE_BOOK","FUTURE_FILLS"],
            }

        def decide(self,observation):
            ops,ev=super().decide(observation)
            weak,strong,classified=self._classify(observation,ops)
            meaningful=(weak is not None and any(z["role"] in ("REPAIR","REEXPAND") for z in classified))
            if not meaningful:
                return ops,ev
            sig=prompt_signature(observation,classified,ev)
            if sig==self.last_prompt_sig:
                # Persist the previous management stance until confirmed portfolio/owner lifecycle changes.
                if self.manual_log:
                    prev=self.manual_log[-1]["choice"]
                    cancels=[x for x in ops if x.get("kind")=="CANCEL"]
                    if prev=="NATIVE": return ops,dict(ev,manual_persisted=prev)
                    if prev=="HOLD": return cancels,dict(ev,manual_persisted=prev)
                    chosen=[z["op"] for z in classified if z["role"]==prev]
                    return cancels+chosen,dict(ev,manual_persisted=prev)
                return ops,ev
            self.last_prompt_sig=sig
            if self.manual_i>=len(type(self).supplied):
                raise NeedDecision(self._snapshot(observation,ops,ev,classified))
            choice=str(type(self).supplied[self.manual_i]).upper()
            if choice not in ("NATIVE","HOLD","REPAIR","REEXPAND"):
                raise ValueError("bad manual choice "+choice)
            snap=self._snapshot(observation,ops,ev,classified)
            cancels=[x for x in ops if x.get("kind")=="CANCEL"]
            if choice=="NATIVE": selected=ops
            elif choice=="HOLD": selected=cancels
            else: selected=cancels+[z["op"] for z in classified if z["role"]==choice]
            self.manual_log.append({
                "index":self.manual_i,"choice":choice,"state":snap,
                "selected_new":[dict(x) for x in selected if x.get("kind")=="NEW"],
            })
            self.manual_i+=1
            return selected,dict(ev,manual_choice=choice,manual_decision_index=self.manual_i-1)
    return ManualPolicy

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--public",required=True)
    ap.add_argument("--execution",required=True)
    ap.add_argument("--decisions",required=True)
    ap.add_argument("--trade-bucket",choices=["early","mid","late"],default="mid")
    ap.add_argument("--phase-ms",type=int,default=500)
    ap.add_argument("--entry-ms",type=int,default=1092)
    ap.add_argument("--response-ms",type=int,default=273)
    a=ap.parse_args()
    if socket.gethostname().upper()!="DESKTOP-JIERAGF":
        raise RuntimeError("native second-PC only")
    sys.path.insert(0,str(R95))
    r95=module("_blind_r95",R95/"worker.py")
    plan,spec,s93,parent,r91,r88,backend,parents,BasePolicy,realism=r95.prepare()
    loop=module("_blind_r95_loop",s93/"hft_loop.py")
    Manual=make_manual_policy(BasePolicy)
    supplied=json.loads(Path(a.decisions).read_text(encoding="utf-8"))
    if isinstance(supplied,dict): supplied=supplied.get("decisions",[])
    if not isinstance(supplied,list): raise ValueError("decisions must be list")
    Manual.supplied=[str(x).upper() for x in supplied]
    loop.Controller=Manual
    public=r88.zread(Path(a.public)); execution=r88.zread(Path(a.execution))
    if public["market_id"]!=2484820 or execution["market_id"]!=2484820:
        raise ValueError("wrong blind market")
    h,binary=loop.native_modules(backend)
    arr,feed=loop.build_events(execution,public,h,"SOURCE_RECEIVE",a.trade_bucket)
    base=spec["arms"][0]["config"]
    out=Path(os.environ["BTC5M_LAN_RESULT_DIR"]); out.mkdir(parents=True,exist_ok=True)
    try:
        trace=loop.run(public,arr,h,binary,base,a.phase_ms,a.entry_ms,a.response_ms)
    except NeedDecision as nd:
        p=Manual.last_instance
        result={
            "status":"NEED_DECISION","market_id":2484820,
            "decisions_supplied":Manual.supplied,
            "decision_log":p.manual_log if p else [],
            "next":nd.snapshot,
            "feed":feed,
            "strict_past":True,"target_used":False,"winner_used":False,
            "live_changes":0,"runtime_authority":False,
        }
        (out/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"status":"NEED_DECISION","index":nd.snapshot["decision_index"],
            "remaining":nd.snapshot["remaining_seconds"],"floor":nd.snapshot["floor"],"best":nd.snapshot["best"],
            "weak":nd.snapshot["weak_side"],"candidates":nd.snapshot["candidates"]},ensure_ascii=False),flush=True)
        return
    p=Manual.last_instance
    final=trace["final"]["observation"]
    result={
        "status":"COMPLETE","market_id":2484820,
        "decisions_supplied":Manual.supplied,"decision_log":p.manual_log if p else [],
        "final":{"inventory":final["inv"],"cost":final["cost"],"payoff":final["payoff"],
            "floor":min(final["payoff"].values()),"best":max(final["payoff"].values()),
            "owners":final["owners"]},
        "trace_summary":{"new_orders":trace["new_orders"],"receipts":trace["receipts"],"cancels":trace["cancels"],
            "unresolved":trace["unresolved"],"worst_floor":trace["worst_floor"],
            "negative_floor_area":trace["negative_floor_area"],"replay":trace["replay"]},
        "feed":feed,"strict_past":True,"target_used":False,"winner_used":False,
        "live_changes":0,"runtime_authority":False,
    }
    (out/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":"COMPLETE","final":result["final"],"summary":result["trace_summary"]},ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
