from __future__ import annotations
import argparse, importlib.util, json, os, shutil, sys, tempfile, threading, time, zipfile
from pathlib import Path
import joblib

ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
if str(ROOT/"tools") not in sys.path: sys.path.insert(0,str(ROOT/"tools"))
EPS=1e-9

def load_sibling(name,filename):
    p=Path(__file__).with_name(filename); s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None: raise ImportError(p)
    m=importlib.util.module_from_spec(s); sys.modules[name]=m; s.loader.exec_module(m); return m

old=load_sibling("initial_epoch_x_parallel_dep","run_eth_v83_same_parent_parallel_repair_hft_smoke_1916869.py")
base=old.base; v38=old.v38; v80=old.v80

class InitialRepairEpochMixin:
    def __init__(self,*a,**kw):
        self.initialRepairEpochEvents=[]; self.initialRepairEpochAttempts=0
        super().__init__(*a,**kw)

    def _ensure_initial_repair_epoch(self,t):
        rp=getattr(self,"repairParent",None)
        if not isinstance(rp,dict): return False
        pid=int(rp.get("id")); side=str(rp.get("side"))
        if side not in ("UP","DOWN"): return False
        if pid in getattr(self,"generationEpochByParent",{}): return False
        self.initialRepairEpochAttempts+=1
        pay=self._current_payoffs()
        debt=float(self._manager_debt_for_parent(pid,float(pay.get("gap") or 0.0)))
        if debt<=EPS:
            self.initialRepairEpochEvents.append({"t":int(t),"event":"INITIAL_REPAIR_EPOCH_NOT_STARTED","parentId":pid,"side":side,"reason":"NO_AUTHORITATIVE_PARENT_DEBT","managerDebt":debt})
            return False
        fill_now=float(self._parent_actual_fill(pid)); churn_now=int(self._parent_churn_count(pid)); paid_now=float(self._paid_total(pid))
        st=base.ResponsibilityGenerationEpochState(parent_id=pid,parent_side=side)
        ep=st.start_new_parent_responsibility(initial_debt=debt,parent_fill_now=fill_now,churn_now=churn_now,paid_total_now=paid_now)
        self.generationEpochByParent[pid]=st
        if hasattr(self,"_armedParents"): self._armedParents.add(pid)
        if hasattr(self,"armFillBase"): self.armFillBase[pid]=fill_now
        self.initialRepairEpochEvents.append({"t":int(t),"event":"INITIAL_REPAIR_RESPONSIBILITY_EXECUTION_EPOCH_START","parentId":pid,"side":side,"epoch":int(ep),"managerDebt":debt,"parentFillBase":fill_now,"churnBase":churn_now,"paidBase":paid_now})
        return True

    def _activate_pending_after_process(self,t):
        r=super()._activate_pending_after_process(t)
        self._ensure_initial_repair_epoch(int(t))
        return r

    def _maybe_hard_active(self,t):
        self._ensure_initial_repair_epoch(int(t))
        return super()._maybe_hard_active(t)

class InitialEpochExistingHFT(InitialRepairEpochMixin,base.ExistingParentResponsibilityGenerationEpochHFT):
    pass
class InitialEpochParallelHFT(InitialRepairEpochMixin,old.SameParentParallelRepairHFT):
    pass

def make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47):
    return cls(tape,"BOOK_IMBALANCE",models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())

def safety(r): return old.birth.base.front.safety(r)

def alloc(sim,r):
    cons=abs(float(r.get("v84CompositeFillQty") or 0)-float(r.get("v84RepairAllocatedQty") or 0)-float(r.get("v84OverflowAllocatedQty") or 0))<=1e-7
    parents=r.get("allocationV2Parents") or {}
    if not parents and hasattr(sim,"allocationLedgerV2"):
        parents={str(pid):sim.allocationLedgerV2.describe_parent(pid) for pid in getattr(sim.allocationLedgerV2,"parents",{})}
    bounded=all(float(p.get("repairPaid") or 0)<=float(p.get("initialDebt") or 0)+1e-7 and float(p.get("remainingDebt") or 0)>=-EPS for p in parents.values() if p)
    return cons,bounded,parents

def run_cell(label,cls,parallel,tape,models,life,cap,tim,econ,price,sur,t44,t47,winner):
    sim=make(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47)
    try:
        r=sim.run_parallel(models,winner) if parallel else sim.run_candidate(models,winner)
        cons,bounded,parents=alloc(sim,r)
        ss=safety(r)
        out={"label":label,"fills":int(r.get("actualFillEvents") or 0),"rounds":int(r.get("v70dSemanticRounds") or r.get("rounds") or 0),"pnlDiagnosticOnly":float(r.get("pnlDiagnosticOnly") or 0),"floor":float(r.get("floor") or 0),"repairParentBirths":int(r.get("repairParentBirths") or 0),"initialEpochStarts":sum(e.get("event")=="INITIAL_REPAIR_RESPONSIBILITY_EXECUTION_EPOCH_START" for e in getattr(sim,"initialRepairEpochEvents",[])),"initialEpochEvents":getattr(sim,"initialRepairEpochEvents",[])[:80],"generationEpochActiveSubmits":int(r.get("generationEpochActiveSubmits") or 0),"generationEpochActiveFillQty":float(r.get("generationEpochActiveFillQty") or 0),"parallelRepairChecks":int(r.get("parallelRepairChecks") or 0),"parallelRepairEligible":int(r.get("parallelRepairEligible") or 0),"parallelRepairSubmits":int(r.get("parallelRepairSubmits") or 0),"parallelRepairActiveFillQty":float(r.get("parallelRepairActiveFillQty") or 0),"overflowAllocatedQty":float(r.get("v84OverflowAllocatedQty") or 0),"overflowPaidQty":float(r.get("v84OverflowPaidQty") or 0),"allocationConservation":cons,"sharedParentDebtBounded":bounded,"safety":ss,"safetyZero":all(float(v or 0)<=EPS for v in ss.values()),"parallelRepairEvents":(r.get("parallelRepairEvents") or [])[:160],"generationEpochRouterEvents":(r.get("generationEpochRouterEvents") or [])[:160],"allocationParents":parents}
        return out
    finally: sim.close()

def main():
    ap=argparse.ArgumentParser()
    for n in ["bundle","lifecycle-model","capability-model","dagger-cache","timing-model","economic-model","price-model","surplus-model","v44-model","v47-model"]: ap.add_argument("--"+n,required=True)
    ap.add_argument("--market-id",type=int,required=True); ap.add_argument("--output",required=True); a=ap.parse_args(); mid=int(a.market_id)
    outp=Path(os.environ["BTC5M_LAN_RESULT_DIR"])/"result.json" if a.output.upper()=="AUTO" else Path(a.output); outp.parent.mkdir(parents=True,exist_ok=True)
    tmp=Path(tempfile.mkdtemp(prefix=f"initial_epoch_2x2_{mid}_")); stop=threading.Event()
    def hb():
        while not stop.wait(15): print(json.dumps({"heartbeat":"INITIAL_EPOCH_X_PARALLEL_2X2","market":mid,"ts":time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start(); print(json.dumps({"heartbeat":"INITIAL_EPOCH_X_PARALLEL_2X2_START","market":mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp); cohort={int(x["marketId"]):x for x in json.load(open(tmp/"cohort.json",encoding="utf-8"))["rows"]}; cr=cohort[mid]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a); t44=joblib.load(a.v44_model)["models"]["EVENT_VALUE_NORM"]; t47=joblib.load(a.v47_model)["models"]["GENERATION_AWARE_NORM"]; tape=tmp/"tapes"/f"{mid}.json.xz"
        cells=[]
        cells.append(run_cell("A_NO_INITIAL_EPOCH_NO_PARALLEL",base.ExistingParentResponsibilityGenerationEpochHFT,False,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr["winner"]))
        cells.append(run_cell("B_NO_INITIAL_EPOCH_WITH_PARALLEL",old.SameParentParallelRepairHFT,True,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr["winner"]))
        cells.append(run_cell("C_INITIAL_EPOCH_BASE_ROUTER",InitialEpochExistingHFT,False,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr["winner"]))
        cells.append(run_cell("D_INITIAL_EPOCH_WITH_PARALLEL",InitialEpochParallelHFT,True,tape,models,life,cap,tim,econ,price,sur,t44,t47,cr["winner"]))
        A,B,C,D=cells
        allsafe=all(x["safetyZero"] and x["allocationConservation"] and x["sharedParentDebtBounded"] for x in cells)
        epoch_effect=(C["initialEpochStarts"]>0 and D["initialEpochStarts"]>0)
        cphys=C["generationEpochActiveFillQty"]>EPS or C["rounds"]>A["rounds"] or C["fills"]>A["fills"]
        dphys=D["parallelRepairActiveFillQty"]>EPS or D["generationEpochActiveFillQty"]>EPS or D["rounds"]>B["rounds"] or D["fills"]>B["fills"]
        floorok=C["floor"]>=A["floor"]-1e-7 and D["floor"]>=B["floor"]-1e-7
        if not allsafe: decision="REJECT_INITIAL_EPOCH_INTERACTION_SAFETY_OR_ACCOUNTING"
        elif not epoch_effect: decision="REJECT_INITIAL_EPOCH_NOT_MATERIALIZED"
        elif not cphys and not dphys: decision="INITIAL_EPOCH_REACHES_ROUTER_BUT_NO_PHYSICAL_EFFECT"
        elif dphys and not floorok: decision="REJECT_PHYSICAL_EFFECT_FLOOR_REGRESSION"
        elif dphys: decision="INITIAL_EPOCH_X_PARALLEL_FUNCTIONAL_PASS"
        else: decision="INITIAL_EPOCH_ALONE_SUFFICIENT_PARALLEL_NOT_INCREMENTAL"
        out={"version":"ETH_INITIAL_REPAIR_EPOCH_X_PARALLEL_EXECUTION_2X2_V1","date":"2026-09-04","researchOnly":True,"runtimeAuthority":False,"marketId":mid,"winnerPostHocOnly":cr["winner"],"decision":decision,"gates":{"allSafetyAccountingZero":allsafe,"initialEpochMaterialized":epoch_effect,"C_physicalEffect":cphys,"D_physicalEffect":dphys,"floorNonWorse":floorok},"cells":cells,"boundary":["single module interaction: initial Repair responsibility execution epoch x same-parent Passive/Active shared budget","new parent epoch captures fill/churn/payment evidence at birth; no pre-birth evidence reuse","same physical parent identity preserved","AllocationLedger V2 Repair-first/overflow-second frozen","inherited economic ceiling and legal-min geometry frozen","payment progress/Active ownership fences frozen","<=180s new Active exposure fence frozen","no threshold/qty/price/delay tuning","winner post-hoc only; no Target runtime input; no dream fill; no 8781"]}
        outp.write_text(json.dumps(out,indent=2),encoding="utf-8"); print(json.dumps({"ok":True,"decision":decision,"gates":out["gates"],"cells":[{k:x[k] for k in ["label","fills","rounds","pnlDiagnosticOnly","floor","repairParentBirths","initialEpochStarts","generationEpochActiveSubmits","generationEpochActiveFillQty","parallelRepairChecks","parallelRepairEligible","parallelRepairSubmits","parallelRepairActiveFillQty","safetyZero"]} for x in cells]},ensure_ascii=False),flush=True)
    finally: stop.set(); shutil.rmtree(tmp,ignore_errors=True)
if __name__=="__main__": main()
