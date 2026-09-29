from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=1916869

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

base=sibling('continuous_scheduler_frontier_base',Path(__file__).resolve().with_name('run_eth_continuous_scheduler_only_behavior_smoke_1916869.py'))
front=base.front;v38=base.v38;v80=base.v80;compact=base.compact
try:
    from tools.eth_repair_modular import economic_continuous_scheduler_only_v1_profile
    from tools.eth_repair_modular.responsibility_frontier import AuthoritativeRepairResponsibilityFrontierV2,RepairResponsibilityFrontierInput
except ImportError:
    from eth_repair_modular import economic_continuous_scheduler_only_v1_profile
    from eth_repair_modular.responsibility_frontier import AuthoritativeRepairResponsibilityFrontierV2,RepairResponsibilityFrontierInput

class SchedulerAuthoritativeRepairFrontierV2(base.ContinuousSchedulerOnlyCandidate):
    def __init__(self,*a,**kw):
        self.repairResponsibilityFrontier=AuthoritativeRepairResponsibilityFrontierV2();self.frontierV2Binds=0;self.frontierV2Events=[]
        super().__init__(*a,**kw)
    def _live_repair_debt_by_side(self):
        debt,rows=super()._live_repair_debt_by_side()
        rp=getattr(self,'repairParent',None);side=rp.get('side') if isinstance(rp,dict) else None
        current=max(0.0,float(getattr(self,'_coordDebt',0.0) or 0.0)) if isinstance(rp,dict) else 0.0
        dec=self.repairResponsibilityFrontier.evaluate(RepairResponsibilityFrontierInput(side,current,float(debt.get('UP') or 0.0),float(debt.get('DOWN') or 0.0)))
        if dec.current_parent_bound:
            self.frontierV2Binds+=1
            ev={'event':'AUTHORITATIVE_CURRENT_REPAIR_PARENT_BOUND','parentId':rp.get('id') if isinstance(rp,dict) else None,'repairSide':side,'currentParentDebtEvidence':current,'overflowDebtBefore':dict(debt),'transitionDebtAfter':{'UP':dec.debt_up,'DOWN':dec.debt_down}}
            self.frontierV2Events.append(ev);rows=list(rows)+[ev]
        return {'UP':dec.debt_up,'DOWN':dec.debt_down},rows
    def run_candidate_v2(self,models,winner):
        r=self.run_candidate(models,winner);r.update({'repairResponsibilityFrontier':self.repairResponsibilityFrontier.name,'frontierV2Binds':self.frontierV2Binds,'frontierV2Events':self.frontierV2Events[:240]});return r

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='scheduler_frontier_v2_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'SCHEDULER_FRONTIER_V2','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'SCHEDULER_FRONTIER_V2_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=economic_continuous_scheduler_only_v1_profile())
        b=mk(base.ContinuousSchedulerOnlyCandidate)
        try:br=b.run_candidate(models,cr['winner'])
        finally:b.close()
        c=mk(SchedulerAuthoritativeRepairFrontierV2)
        try:rr=c.run_candidate_v2(models,cr['winner'])
        finally:c.close()
        bs=front.safety(br);ss=front.safety(rr);sm=rr.get('schedulerAdapter') or {}
        conflict_t=1788450016707
        conflict_rows=[x for x in (rr.get('v83Admissions') or []) if int(x.get('t') or -1)==conflict_t]
        conflict_submit=any(bool(x.get('submit')) for x in conflict_rows)
        gates={
            'controlReproducesTruthMismatch':float(bs['truthMismatch'])>=1,
            'frontierModuleActive':rr.get('repairResponsibilityFrontier')=='authoritative_current_parent_plus_overflow_repair_frontier_v2',
            'currentParentBindingExercised':int(rr.get('frontierV2Binds') or 0)>0,
            'knownConflictBlocked':not conflict_submit,
            'zeroCandidateTruthMismatch':float(ss['truthMismatch'])==0,
            'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,
            'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,
            'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,
            'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,
            'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,
            'zeroSharedOverfill':ss['sharedOverfill']<=EPS,
            'schedulerStillExercised':int(sm.get('reevaluations') or 0)>0,
        }
        decision='KEEP_AUTHORITATIVE_REPAIR_FRONTIER_V2_FOR_SCHEDULER_REPLICATION' if all(gates.values()) else 'REJECT_OR_DIAGNOSE_AUTHORITATIVE_REPAIR_FRONTIER_V2'
        out={'version':'ETH_CONTINUOUS_SCHEDULER_AUTHORITATIVE_REPAIR_FRONTIER_V2_1916869','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'controlRejectedScheduler':compact(br),'candidateFrontierV2':compact(rr),'controlSafety':bs,'candidateSafety':ss,'schedulerAdapter':sm,'frontierV2Binds':rr.get('frontierV2Binds'),'frontierV2Events':rr.get('frontierV2Events',[]),'knownConflictRows':conflict_rows,'candidateV83Admissions':(rr.get('v83Admissions') or [])[:260],'boundary':['single module replacement relative to rejected scheduler candidate: RepairResponsibilityStateFrontier only','RepairFirstResponsibilityTransitionV1 rule unchanged','frontier debt is ownership evidence only; AllocationLedger amounts unchanged','continuous scheduler frozen','all downstream admission/qty/price/execution modules frozen','realistic HFT only','winner/PnL post-hoc only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'control':out['controlRejectedScheduler'],'candidate':out['candidateFrontierV2'],'controlSafety':bs,'candidateSafety':ss,'frontierBinds':out['frontierV2Binds'],'scheduler':{k:v for k,v in sm.items() if k!='events'}},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
