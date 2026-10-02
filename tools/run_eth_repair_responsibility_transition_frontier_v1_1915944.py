from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import run_eth_repair_modular_allocation_v2_generic_hft as g

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
_local=Path(__file__).resolve().with_name('responsibility_transition.py')
trans=sibling('responsibility_transition_v1_runtime',_local if _local.exists() else ROOT/'tools'/'eth_repair_modular'/'responsibility_transition.py')
EPS=1e-9;FIXED=1915944;v38=g.v38;v80=g.v80

class ResponsibilityTransitionCandidate(g.ModularAllocationLedgerV2):
    def __init__(self,*a,**kw):
        self.transitionPolicy=trans.RepairFirstResponsibilityTransitionV1();self.transitionEvents=[];self.transitionChecks=0;self.transitionBlocks=0
        super().__init__(*a,**kw)
    def _live_repair_debt_by_side(self):
        debt={'UP':0.0,'DOWN':0.0};rows=[]
        for key,m in getattr(self,'v84Composite',{}).items():
            rem=max(0.0,float(m.get('overflowDebt') or 0.0)-float(m.get('overflowPaid') or 0.0))
            if rem<=EPS or m.get('overflowBornAt') is None:continue
            side='DOWN' if str(m.get('side')).upper()=='UP' else 'UP'
            debt[side]+=rem;rows.append({'compositeKey':key,'repairSide':side,'remainingDebt':rem,'bornAt':m.get('overflowBornAt')})
        return debt,rows
    def _score_state(self,t,after_kind):
        # Commit carrier/allocation state first; then resolve responsibility-role ownership.
        self._refresh_carrier_ledger(int(t))
        debt,rows=self._live_repair_debt_by_side();th=getattr(self,'thesis',None);side=th.get('side') if isinstance(th,dict) else None
        d=self.transitionPolicy.evaluate(trans.ResponsibilityTransitionContext(side,debt['UP'],debt['DOWN']))
        self.transitionChecks+=1
        ev={'t':int(t),'event':'RESPONSIBILITY_TRANSITION_CHECK','afterKind':after_kind,'thesisSide':side,'repairDebtBySide':debt,'debtRows':rows,'allowExpandOwnership':d.allow_expand_ownership,'bindRole':d.bind_role,'reason':d.reason,'liveRepairDebt':d.live_repair_debt}
        if after_kind=='REPAIR' and not d.allow_expand_ownership and d.bind_role=='REPAIR':
            self.transitionBlocks+=1;ev['event']='EXPAND_OWNERSHIP_SUSPENDED_REPAIR_FIRST';self.transitionEvents.append(ev);return None
        self.transitionEvents.append(ev);return super()._score_state(t,after_kind)
    def run_transition(self,models,winner):
        r=self.run_allocation_v2(models,winner);r.update({'responsibilityTransitionPolicy':self.transitionPolicy.name,'transitionChecks':self.transitionChecks,'transitionBlocks':self.transitionBlocks,'transitionEvents':self.transitionEvents[:180]});return r

def safety(r):
    legacy=int(r.get('overOwnedSubmitViolations') or 0);comps=int(r.get('v84CompositeSubmits') or 0);drift=int(r.get('repairToExpandAtFirstFill') or 0);covered=int(r.get('allocationV2PureOverflowFirstFills') or 0)
    return {'truthMismatch':float(r.get('authorizedSubmitWithTruthRoleMismatch') or 0),'unexplainedOverOwned':max(0,legacy-comps),'unexplainedRepairDrift':max(0,drift-covered),'responsibilityOverfill':float(r.get('v51ResponsibilityOverfill') or 0),'preBirthLeak':float(r.get('v70dPreBirthPaymentLeak') or 0)+float(r.get('v84PreBirthPaymentLeak') or 0),'duplicateDebt':float(r.get('v70dDuplicateGenerationDebt') or 0)+float(r.get('v84DuplicateDebt') or 0),'sharedOverfill':float(r.get('v36SharedRealizedOverfill') or 0)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='eth_resp_transition_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'RESPONSIBILITY_TRANSITION_V1','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'RESPONSIBILITY_TRANSITION_V1_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=v80.economic_v1_profile())
        b=mk(g.ModularAllocationLedgerV2)
        try:br=b.run_allocation_v2(models,cr['winner'])
        finally:b.close()
        c=mk(ResponsibilityTransitionCandidate)
        try:rr=c.run_transition(models,cr['winner'])
        finally:c.close()
        ss=safety(rr);cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        gates={'marketCompletes':True,'transitionConflictExercised':int(rr.get('transitionBlocks') or 0)>0,'baselineTruthMismatchAtLeastOne':float(br.get('authorizedSubmitWithTruthRoleMismatch') or 0)>=1,'zeroCandidateTruthMismatch':ss['truthMismatch']==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS,'allocationConservation':cons}
        decision='KEEP_RESPONSIBILITY_TRANSITION_V1_FOR_FRESH_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_RESPONSIBILITY_TRANSITION_V1'
        out={'version':'ETH_REPAIR_RESPONSIBILITY_TRANSITION_FRONTIER_V1_1915944','date':'2026-09-04','researchOnly':True,'marketId':FIXED,'decision':decision,'gates':gates,'safety':ss,'baseline':{'pnl':br.get('pnlDiagnosticOnly'),'floor':br.get('floor'),'fills':br.get('actualFillEvents'),'truthMismatch':br.get('authorizedSubmitWithTruthRoleMismatch'),'v83Admissions':br.get('v83Admissions',[])},'candidate':{'pnl':rr.get('pnlDiagnosticOnly'),'floor':rr.get('floor'),'fills':rr.get('actualFillEvents'),'truthMismatch':rr.get('authorizedSubmitWithTruthRoleMismatch'),'transitionChecks':rr.get('transitionChecks'),'transitionBlocks':rr.get('transitionBlocks'),'v83Admissions':rr.get('v83Admissions',[])},'transitionEvents':rr.get('transitionEvents',[]),'boundary':['single deterministic module replacement: ResponsibilityTransition only','existing thesis belief preserved; only conflicting Expand ownership suspended while same-side Repair debt is live','AllocationLedger V2 and RepairExecutionRouter frozen','no cooldown','no threshold/qty/price change','winner post-hoc only','no 8781']}
        Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'baseline':out['baseline'],'candidate':out['candidate'],'safety':ss,'events':out['transitionEvents'][:20]},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
