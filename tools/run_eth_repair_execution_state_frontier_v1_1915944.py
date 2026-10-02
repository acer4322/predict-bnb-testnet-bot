from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import run_eth_repair_modular_allocation_v2_generic_hft as g

def sibling_module(name,filename):
    p=Path(__file__).resolve().with_name(filename);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
frontier_mod=sibling_module('execution_state_frontier_v1_runtime','execution_state_frontier.py')
ExecutionStateFrontier=frontier_mod.ExecutionStateFrontier

EPS=1e-9
FIXED=1915944
v38=g.v38;v80=g.v80

class FrontierCandidate(g.ModularAllocationLedgerV2):
    def __init__(self,*a,**kw):
        self.executionStateFrontier=ExecutionStateFrontier();self.frontierEvents=[];self.frontierReconcileClocks=0;self.frontierBlocks=0
        super().__init__(*a,**kw)
    def _score_state(self,t,after_kind):
        n0=len(getattr(self,'allocationV2Events',[]))
        self._refresh_carrier_ledger(int(t))
        new=[x for x in getattr(self,'allocationV2Events',[])[n0:] if int(x.get('t',-1))==int(t) and str(x.get('event'))=='ALLOCATION_V2_FILL']
        if new:
            self.frontierReconcileClocks+=1
            for x in new:
                inc=float(x.get('fillInc') or 0.0);alloc=float(x.get('repairInc') or 0.0)+float(x.get('overflowInc') or 0.0)
                self.executionStateFrontier.observe_material_fill(int(t),inc);self.executionStateFrontier.commit_allocation(int(t),alloc)
            d=self.executionStateFrontier.decision(int(t));self.frontierEvents.append({'t':int(t),'event':'FRONTIER_RECONCILE_BEFORE_MANAGER','fills':new,'frontier':self.executionStateFrontier.snapshot(int(t))})
            if not d.allow_management:
                self.frontierBlocks+=1;self.frontierEvents.append({'t':int(t),'event':'MANAGEMENT_BLOCKED_UNRECONCILED','frontier':self.executionStateFrontier.snapshot(int(t))});return None
        return super()._score_state(t,after_kind)
    def run_frontier(self,models,winner):
        r=self.run_allocation_v2(models,winner);r.update({'executionStateFrontier':self.executionStateFrontier.name,'frontierReconcileClocks':self.frontierReconcileClocks,'frontierBlocks':self.frontierBlocks,'frontierEvents':self.frontierEvents[:120]});return r

def safety(r):
    legacy=int(r.get('overOwnedSubmitViolations') or 0);comps=int(r.get('v84CompositeSubmits') or 0);drift=int(r.get('repairToExpandAtFirstFill') or 0);covered=int(r.get('allocationV2PureOverflowFirstFills') or 0)
    return {'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'unexplainedOverOwned':max(0,legacy-comps),'unexplainedRepairDrift':max(0,drift-covered),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0),'preBirthLeak':float(r.get('v70dPreBirthPaymentLeak') or 0)+float(r.get('v84PreBirthPaymentLeak') or 0),'duplicateDebt':float(r.get('v70dDuplicateGenerationDebt') or 0)+float(r.get('v84DuplicateDebt') or 0),'sharedOverfill':float(r.get('v36SharedRealizedOverfill') or 0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='eth_frontier_v1_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'EXECUTION_STATE_FRONTIER_V1','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'EXECUTION_STATE_FRONTIER_V1_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        b=mk(g.ModularAllocationLedgerV2)
        try:br=b.run_allocation_v2(models,cr['winner'])
        finally:b.close()
        c=mk(FrontierCandidate)
        try:rr=c.run_frontier(models,cr['winner'])
        finally:c.close()
        ss=safety(rr);cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        gates={'marketCompletes':True,'zeroTruthMismatch':ss['truthMismatch']==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS,'allocationConservation':cons,'frontierReconciliationExercised':int(rr.get('frontierReconcileClocks') or 0)>0}
        decision='KEEP_EXECUTION_STATE_FRONTIER_V1_FOR_FRESH_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_EXECUTION_STATE_FRONTIER_V1'
        out={'version':'ETH_REPAIR_EXECUTION_STATE_FRONTIER_V1_1915944','date':'2026-09-04','researchOnly':True,'marketId':FIXED,'decision':decision,'gates':gates,'safety':ss,'baseline':{'pnl':br.get('pnlDiagnosticOnly'),'floor':br.get('floor'),'fills':br.get('actualFillEvents'),'truthMismatch':br.get('authorizedSubmitWithTruthRoleMismatch'),'v83Admissions':br.get('v83Admissions',[])},'candidate':{'pnl':rr.get('pnlDiagnosticOnly'),'floor':rr.get('floor'),'fills':rr.get('actualFillEvents'),'truthMismatch':rr.get('authorizedSubmitWithTruthRoleMismatch'),'frontierReconcileClocks':rr.get('frontierReconcileClocks'),'frontierBlocks':rr.get('frontierBlocks'),'v83Admissions':rr.get('v83Admissions',[])},'frontierEvents':rr.get('frontierEvents',[]),'boundary':['single deterministic module replacement: ExecutionStateFrontier only','Manager/Ownership/Router/AllocationLedger frozen','no cooldown','no price/qty/threshold change','winner post-hoc only','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baseline'],'candidate':out['candidate'],'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
