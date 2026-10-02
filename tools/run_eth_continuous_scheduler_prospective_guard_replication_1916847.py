from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=1916847

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

guard=sibling('prospective_guard_replication_base',Path(__file__).resolve().with_name('run_eth_continuous_scheduler_prospective_ownership_transition_guard_v1_1916869.py'))
v2=guard.v2;base=guard.base;front=guard.front;v38=guard.v38;compact=guard.compact
Candidate=guard.ProspectiveOwnershipTransitionGuardCandidate
try:
    from tools.eth_repair_modular import economic_continuous_scheduler_only_v1_profile
except ImportError:
    from eth_repair_modular import economic_continuous_scheduler_only_v1_profile

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='prospective_guard_rep_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'PROSPECTIVE_GUARD_REPLICATION','ts':time.time(),'market':FIXED}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'PROSPECTIVE_GUARD_REPLICATION_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=economic_continuous_scheduler_only_v1_profile())
        b=mk(v2.SchedulerAuthoritativeRepairFrontierV2)
        try:br=b.run_candidate_v2(models,cr['winner'])
        finally:b.close()
        c=mk(Candidate)
        try:rr=c.run_candidate_guard(models,cr['winner'])
        finally:c.close()
        bs=front.safety(br);ss=front.safety(rr);sm=rr.get('schedulerAdapter') or {}
        events=rr.get('prospectiveTransitionEvents') or []
        same_side_blocks=[x for x in events if x.get('event')=='PROSPECTIVE_THESIS_BIRTH_BLOCKED_REPAIR_FIRST']
        later_adm=[x for x in (rr.get('v83Admissions') or []) if bool(x.get('submit'))]
        gates={
            'guardModuleActive':rr.get('prospectiveOwnershipTransitionGuard')=='prospective_ownership_transition_guard_v1',
            'prospectiveTransitionExercised':int(rr.get('prospectiveTransitionChecks') or 0)>0,
            'sameSideConflictBlocked':len(same_side_blocks)>0,
            'zeroCandidateTruthMismatch':float(ss['truthMismatch'])==0,
            'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,
            'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,
            'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,
            'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,
            'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,
            'zeroSharedOverfill':ss['sharedOverfill']<=EPS,
            'schedulerStillExercised':int(sm.get('reevaluations') or 0)>0,
        }
        if not all(gates.values()):decision='REPLICATION_REJECT_OR_DIAGNOSE_PROSPECTIVE_GUARD'
        elif int(sm.get('admissionAllowsFromScheduler') or 0)>0:decision='REPLICATION_KEEP_PROSPECTIVE_GUARD_WITH_SCHEDULER_ACTION'
        else:decision='REPLICATION_KEEP_PROSPECTIVE_GUARD_SAFETY_CLOCK_ONLY'
        out={'version':'ETH_CONTINUOUS_SCHEDULER_PROSPECTIVE_GUARD_REPLICATION_1916847','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'marketId':FIXED,'winnerPostHocOnly':cr['winner'],'decision':decision,'gates':gates,'controlFrontierV2':compact(br),'candidateGuard':compact(rr),'controlSafety':bs,'candidateSafety':ss,'schedulerAdapter':sm,'prospectiveTransitionChecks':rr.get('prospectiveTransitionChecks'),'prospectiveTransitionBlocks':rr.get('prospectiveTransitionBlocks'),'sameSideBlockedEvents':same_side_blocks[:100],'schedulerOriginAdmissions':later_adm[:50],'prospectiveTransitionEvents':events[:300],'candidateV83Admissions':(rr.get('v83Admissions') or [])[:320],'boundary':['conditional preregistered replication','ProspectiveOwnershipTransitionGuard only relative to FrontierV2 control','Ownership/Transition policies unchanged','continuous scheduler and authoritative Repair frontier frozen','all accounting/admission/qty/price/execution modules frozen','realistic HFT only','winner/PnL post-hoc only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'gates':gates,'control':out['controlFrontierV2'],'candidate':out['candidateGuard'],'controlSafety':bs,'candidateSafety':ss,'prospectiveChecks':out['prospectiveTransitionChecks'],'prospectiveBlocks':out['prospectiveTransitionBlocks'],'schedulerOriginAdmissions':len(later_adm),'scheduler':{k:v for k,v in sm.items() if k!='events'}},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
