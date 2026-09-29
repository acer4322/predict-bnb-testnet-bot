from __future__ import annotations
import argparse, importlib.util, json, math, os, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
EPS=1e-9; MID=1912961

def sib(name, filename):
    p=Path(__file__).with_name(filename); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

active=sib("active_for_parent5_funnel","run_eth_alignment_residual_repair_active_handoff_behavior_smoke_1912961.py")
base=active.base; v90=active.v90; v80=active.v80; v38=active.v38; v1=active.v1

class Parent5ExecutionFunnel(active.ResidualRepairActiveHandoffBehavior):
    def __init__(self,*a,**kw):
        self.parent5Rows=[]; self._parent5Sig=None
        super().__init__(*a,**kw)

    def _active_overflow_birth(self):
        rows=[]
        for key,s in getattr(self,"v84Composite",{}).items():
            if s.get("lane")!="ALIGNMENT_RESIDUAL_REPAIR_ACTIVE_HANDOFF": continue
            born=s.get("overflowBornAt"); debt=float(s.get("overflowDebt") or 0); paid=float(s.get("overflowPaid") or 0)
            if born is None or debt-paid<=EPS: continue
            rows.append((int(born),str(key),str(s.get("side")),debt-paid))
        return min(rows) if rows else None

    def _capture_parent5(self,t:int,source:str):
        ob=self._active_overflow_birth()
        if ob is None: return
        born,source_key,source_side,overflow_rem=ob
        ai=self.auth_inv(); weak="UP" if float(ai["UP"])<float(ai["DOWN"])-EPS else "DOWN" if float(ai["DOWN"])<float(ai["UP"])-EPS else None
        rp=getattr(self,"repairParent",None)
        correct=bool(isinstance(rp,dict) and weak in ("UP","DOWN") and str(rp.get("side"))==weak and int(rp.get("bornAt") or -1)>=born)
        pid=int(rp.get("id") or -1) if isinstance(rp,dict) else -1
        parent_side=str(rp.get("side")) if isinstance(rp,dict) else None
        children=[]
        if pid>=0:
            self._refresh_carrier_ledger(int(t))
            for key,e in self.carrierLedger.items():
                try: ep=int(e.get("parentId") or -1)
                except Exception: ep=-1
                if ep!=pid or str(e.get("objectiveRole") or "")!="REPAIR": continue
                o=self.orders.get(key); snap={}
                try: snap=self.snap(o) if o is not None else {}
                except Exception: pass
                live=bool(o is not None and v1.live(snap.get("status")))
                try: rem=float(self._ledger_remaining(e))
                except Exception: rem=max(0.0,float(e.get("submittedQty") or 0)-float(e.get("actualFilled") or 0))
                children.append({"key":str(key),"lane":e.get("lane"),"status":snap.get("status"),"live":live,"terminalConfirmed":bool(e.get("terminalConfirmed")),"submittedQty":float(e.get("submittedQty") or 0),"actualFilled":float(e.get("actualFilled") or 0),"remaining":rem,"price":e.get("price")})
        passive=[x for x in children if not str(x.get("lane") or "").startswith("ACTIVE_") and x.get("lane")!="ALIGNMENT_RESIDUAL_REPAIR_ACTIVE_HANDOFF"]
        live_passive=[x for x in passive if x["live"] and x["remaining"]>EPS]
        armed=pid in getattr(self,"_armedParents",set()) if pid>=0 else False
        opp=[x for x in getattr(self,"postArmCarrierOpportunities",[]) if int(x.get("parentId") or -1)==pid] if pid>=0 else []
        first2=opp[:2]
        first2_disconnected=bool(len(first2)>=2 and all(not bool(x.get("connected")) for x in first2))
        arm_base=float(getattr(self,"armFillBase",{}).get(pid,self._parent_actual_fill(pid) if pid>=0 else 0.0)) if pid>=0 else 0.0
        parent_fill=float(self._parent_actual_fill(pid)) if pid>=0 else 0.0
        progress=max(0.0,parent_fill-arm_base)
        active_owned=pid in getattr(self,"activeByParent",{}) if pid>=0 else False
        hard=pid in getattr(self,"hardConfirmed",set()) if pid>=0 else False
        qv=v1.quotes(self.book); ask=None; legal=math.inf
        if qv and parent_side in ("UP","DOWN") and qv.get(parent_side,{}).get("ask") is not None:
            ask=float(qv[parent_side]["ask"]); legal=1.0/ask if ask>EPS else math.inf
        floor,u,d,cost=self._raw_floor(); gap=abs(float(ai["UP"])-float(ai["DOWN"]))
        if not correct: cls="NO_CORRECT_PARENT_MATERIALIZATION"
        elif not passive: cls="NO_PASSIVE_CHILD"
        elif live_passive:
            if not armed: cls="NO_PARENT_ARM"
            elif len(opp)<2 or not first2_disconnected: cls="INSUFFICIENT_DISCONNECT_EVIDENCE"
            elif progress>EPS: cls="PAYMENT_PROGRESS_BLOCK"
            elif active_owned: cls="ACTIVE_ALREADY_OWNED"
            else:
                prem=max(x["remaining"] for x in live_passive)
                cls="EXECUTION_HANDOFF_ELIGIBLE" if math.isfinite(legal) and legal<=gap+EPS and legal<=prem+EPS else "NO_LEGAL_ACTIVE_SLICE"
        else:
            cls="ACTIVE_ALREADY_OWNED" if active_owned else "OTHER_PASSIVE_RELEASED_WITHOUT_ACTIVE"
        phase=max(0.0,min(1.0,1.0-(int(self.capEnd)-int(t))/300000.0))
        row={"t":int(t),"source":source,"normalizedPhase":phase,"secondsLeftDiagnosticOnly": (int(self.capEnd)-int(t))/1000.0,
             "activeOverflow":{"sourceKey":source_key,"bornAt":born,"sourceSide":source_side,"remaining":overflow_rem},
             "weakSide":weak,"correctParent":correct,"parentId":pid,"parentSide":parent_side,"parentBornAt":int(rp.get("bornAt") or -1) if isinstance(rp,dict) else None,
             "children":children,"armed":armed,"postArmOpportunityCount":len(opp),"firstTwoDisconnected":first2_disconnected,
             "firstTwoOpportunities":[{"t":x.get("t"),"connected":x.get("connected"),"reason":x.get("reason")} for x in first2],
             "parentFillAtArm":arm_base,"parentFillNow":parent_fill,"paymentProgress":progress,"hardConfirmed":hard,"activeOwned":active_owned,
             "floor":float(floor),"gap":gap,"liveAsk":ask,"legalActiveQty":legal if math.isfinite(legal) else None,"classification":cls}
        sig=(cls,pid,tuple((x["key"],x["status"],round(x["remaining"],9),x["live"]) for x in children),armed,len(opp),first2_disconnected,round(progress,9),active_owned,round(ask or -1,4))
        if sig!=self._parent5Sig or len(self.parent5Rows)<5:
            self.parent5Rows.append(row); self._parent5Sig=sig

    def process(self,t):
        super().process(t); self._capture_parent5(int(t),"process")
    def cancel_expired(self,t):
        super().cancel_expired(t); self._capture_parent5(int(t),"cancel_expired")
    def run_funnel(self,models,winner):
        r=self.run_alignment(models,winner); r["parent5ExecutionFunnelRows"]=self.parent5Rows[:480]; return r

def main():
    ap=argparse.ArgumentParser()
    for n in ["bundle","lifecycle-model","capability-model","dagger-cache","timing-model","economic-model","price-model","surplus-model","v44-model","v47-model"]: ap.add_argument("--"+n,required=True)
    ap.add_argument("--market-id",type=int,required=True); ap.add_argument("--output",required=True); a=ap.parse_args()
    if a.market_id!=MID: raise ValueError(a.market_id)
    outp=Path(os.environ["BTC5M_LAN_RESULT_DIR"])/"result.json" if a.output.upper()=="AUTO" else Path(a.output); outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix="eth_parent5_funnel_")); stop=threading.Event()
    def hb():
        while not stop.wait(10): print(json.dumps({"heartbeat":"OVERFLOW_PARENT5_EXECUTION_FUNNEL","ts":time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({"heartbeat":"OVERFLOW_PARENT5_EXECUTION_FUNNEL_START","market":MID}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cohort={int(x["marketId"]):x for x in json.load(open(tmp/"cohort.json",encoding="utf-8"))["rows"]}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); teacher=joblib.load(a.v44_model)["models"]["EVENT_VALUE_NORM"]; gen=joblib.load(a.v47_model)["models"]["GENERATION_AWARE_NORM"]
        sim=base.make_simulator(Parent5ExecutionFunnel,tmp/"tapes"/f"{MID}.json.xz",models,life,cap,tim,econ,price,sur,teacher,gen)
        try:r=sim.run_funnel(models,cohort[MID]["winner"])
        finally:sim.close()
        rows=r.get("parent5ExecutionFunnelRows",[]) or []; counts={}
        for x in rows: counts[x["classification"]]=counts.get(x["classification"],0)+1
        priority=["NO_CORRECT_PARENT_MATERIALIZATION","NO_PASSIVE_CHILD","PASSIVE_STILL_LIVE","NO_PARENT_ARM","INSUFFICIENT_DISCONNECT_EVIDENCE","PAYMENT_PROGRESS_BLOCK","NO_LEGAL_ACTIVE_SLICE","ACTIVE_ALREADY_OWNED","EXECUTION_HANDOFF_ELIGIBLE","OTHER_PASSIVE_RELEASED_WITHOUT_ACTIVE"]
        dominant=max(counts,key=counts.get) if counts else "NO_ROWS"
        first_eligible=next((x for x in rows if x["classification"]=="EXECUTION_HANDOFF_ELIGIBLE"),None)
        out={"version":"OVERFLOW_PARENT5_EXECUTION_HANDOFF_FUNNEL_1912961","date":"2026-09-04","researchOnly":True,"runtimeAuthority":False,"behaviorMutation":False,"marketId":MID,
             "decision":"PASS_EXISTING_V36_HANDOFF_REACHABLE" if first_eligible else "LOCALIZED_PARENT5_EXECUTION_BLOCKER","summary":{"rows":len(rows),"classificationCounts":counts,"dominantClassification":dominant,"firstEligible":first_eligible,"fills":r.get("actualFillEvents"),"floor":r.get("floor"),"pnlDiagnosticOnly":r.get("pnlDiagnosticOnly"),"v36BlockedNoLegalSlice":r.get("v36BlockedNoLegalSlice"),"v36BlockedPaymentProgress":r.get("v36BlockedPaymentProgress"),"v36ActiveSubmitCount":r.get("v36ActiveSubmitCount")},
             "rows":rows,"safety":v90.safety_summary(r),"boundary":["behavior-inert funnel over existing residual Active candidate","event/progress + normalized phase manager semantics","wall-clock age diagnostic only","no Target runtime input","no dream fill","no 8781"]}
        outp.write_text(json.dumps(out,indent=2),encoding="utf-8"); print(json.dumps({"ok":True,"decision":out["decision"],"summary":out["summary"],"safety":out["safety"]},ensure_ascii=False),flush=True)
    finally: stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=="__main__": main()
