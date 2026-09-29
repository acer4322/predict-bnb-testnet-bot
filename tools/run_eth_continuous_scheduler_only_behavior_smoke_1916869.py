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

front=sibling('continuous_scheduler_behavior_front',Path(__file__).resolve().with_name('run_eth_repair_responsibility_transition_frontier_v1_1915944.py'))
g=front.g;v38=front.v38;v80=front.v80
try:
    from tools.eth_repair_modular import economic_continuous_scheduler_only_v1_profile
    from tools.eth_repair_modular.scheduler_adapter import ContinuousResponsibilitySchedulerAdapterMixin
except ImportError:
    from eth_repair_modular import economic_continuous_scheduler_only_v1_profile
    from eth_repair_modular.scheduler_adapter import ContinuousResponsibilitySchedulerAdapterMixin

class ContinuousSchedulerOnlyCandidate(ContinuousResponsibilitySchedulerAdapterMixin,front.ResponsibilityTransitionCandidate):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw);self._init_scheduler_adapter()
    def process(self,t):
        out=super().process(t)
        self._scheduler_after_process(int(t))
        return out
    def run_candidate(self,models,winner):
        r=self.run_transition(models,winner)
        r.update({'schedulerAdapter':self.scheduler_adapter_metrics()})
        return r

def compact(r):
    return {
        'fills':int(r.get('actualFillEvents') or 0),
        'submits':int(r.get('submitEvents') or r.get('submits') or 0),
        'roleCounts':dict(r.get('v53RoleCounts') or {}),
        'roleQty':dict(r.get('v53RoleQty') or {}),
        'repairExpandRepairRounds':int(r.get('repairExpandRepairRounds') or r.get('v70dSemanticRounds') or 0),
        'strictRounds':int(r.get('passiveRepairExpandActiveRepairRounds') or 0),
        'floor':float(r.get('floor') or 0.0),
        'pnlDiagnosticOnly':float(r.get('pnlDiagnosticOnly') or 0.0),
        'v83Checks':int(r.get('v83AdmissionChecks') or 0),
        'v83Allows':int(r.get('v83AdmissionAllows') or 0),
        'v83Blocks':int(r.get('v83AdmissionBlocks') or 0),
        'transitionBlocks':int(r.get('transitionBlocks') or 0),
        'generationCount':int(r.get('v70gGenerationCount') or 0),
    }

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='continuous_scheduler_behavior_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'CONTINUOUS_SCHEDULER_ONLY_BEHAVIOR','ts':time.time()}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'CONTINUOUS_SCHEDULER_ONLY_BEHAVIOR_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls,profile):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=profile)
        b=mk(front.ResponsibilityTransitionCandidate,v80.economic_v1_profile())
        try:br=b.run_transition(models,cr['winner'])
        finally:b.close()
        c=mk(ContinuousSchedulerOnlyCandidate,economic_continuous_scheduler_only_v1_profile())
        try:rr=c.run_candidate(models,cr['winner'])
        finally:c.close()
        ss=front.safety(rr);allsafe=all(float(v)<=EPS for v in ss.values());sm=rr.get('schedulerAdapter') or {}
        scheduler_allow=int(sm.get('admissionAllowsFromScheduler') or 0)
        gates={
            'marketCompletes':True,
            'schedulerModuleActive':(rr.get('v80PolicyProfile') or {}).get('scheduler')=='continuous_live_responsibility_scheduler_v1',
            'schedulerReevaluationExercised':int(sm.get('reevaluations') or 0)>0,
            'schedulerOriginAdmissionExercised':scheduler_allow>0,
            'zeroTruthMismatch':ss['truthMismatch']==0,
            'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,
            'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,
            'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,
            'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,
            'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,
            'zeroSharedOverfill':ss['sharedOverfill']<=EPS,
        }
        if not allsafe:decision='REJECT_CONTINUOUS_SCHEDULER_SAFETY'
        elif scheduler_allow<=0:decision='DIAGNOSE_SCHEDULER_ADAPTER_STATE_DRIFT'
        elif compact(rr)['fills']>compact(br)['fills'] or compact(rr)['repairExpandRepairRounds']>compact(br)['repairExpandRepairRounds']:
            decision='SUPPORT_CONTINUOUS_SCHEDULER_ACTUATOR_CLOCK_WITH_PHYSICAL_EFFECT'
        else:decision='KEEP_CONTINUOUS_SCHEDULER_CLOCK_DIAGNOSE_EXECUTION_MATERIALIZATION'
        out={'version':'ETH_CONTINUOUS_SCHEDULER_ONLY_BEHAVIOR_SMOKE_1916869','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'baselineEventClock':compact(br),'candidateContinuousClock':compact(rr),'schedulerAdapter':sm,'safety':ss,'allSafetyZero':allsafe,'candidateV83Admissions':(rr.get('v83Admissions') or [])[:240],'boundary':['single module replacement: ResponsibilityScheduler only','continuous scheduler invokes existing V83 admission seam; it does not own action authority','ResponsibilityTransition/AllocationLedgerV2/RepairExecutionRouter frozen','Completion/Ownership/Handoff/Generation frozen','pExpand threshold 0.50 frozen','qty/price/recoverability/dedup frozen','realistic HFT only','winner/PnL post-hoc only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'decision':decision,'baseline':out['baselineEventClock'],'candidate':out['candidateContinuousClock'],'scheduler':{k:v for k,v in sm.items() if k!='events'},'safety':ss,'gates':gates},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
