from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;ALLOWED={1916847}

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

v2=sibling('scheduler_frontier_v2_base',Path(__file__).resolve().with_name('run_eth_continuous_scheduler_authoritative_repair_frontier_v2_1916869.py'))
base=v2.base;front=v2.front;v38=v2.v38;compact=v2.compact
Candidate=v2.SchedulerAuthoritativeRepairFrontierV2
try:
    from tools.eth_repair_modular import economic_continuous_scheduler_only_v1_profile
except ImportError:
    from eth_repair_modular import economic_continuous_scheduler_only_v1_profile

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id)
    if mid not in ALLOWED:raise ValueError(mid)
    tmp=Path(tempfile.mkdtemp(prefix='scheduler_frontier_v2_rep_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'SCHEDULER_FRONTIER_V2_REPLICATION','ts':time.time(),'market':mid}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'SCHEDULER_FRONTIER_V2_REPLICATION_START','market':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz'
        def mk(cls):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=economic_continuous_scheduler_only_v1_profile())
        b=mk(base.ContinuousSchedulerOnlyCandidate)
        try:br=b.run_candidate(models,cr['winner'])
        finally:b.close()
        c=mk(Candidate)
        try:rr=c.run_candidate_v2(models,cr['winner'])
        finally:c.close()
        bs=front.safety(br);ss=front.safety(rr);sm=rr.get('schedulerAdapter') or {}
        gates={'frontierModuleActive':rr.get('repairResponsibilityFrontier')=='authoritative_current_parent_plus_overflow_repair_frontier_v2','currentParentBindingExercised':int(rr.get('frontierV2Binds') or 0)>0,'zeroCandidateTruthMismatch':float(ss['truthMismatch'])==0,'zeroUnexplainedOverOwned':ss['unexplainedOverOwned']==0,'zeroUnexplainedRepairDrift':ss['unexplainedRepairDrift']==0,'zeroResponsibilityOverfill':ss['responsibilityOverfill']<=EPS,'zeroPreBirthLeak':ss['preBirthLeak']<=EPS,'zeroDuplicateDebt':ss['duplicateDebt']<=EPS,'zeroSharedOverfill':ss['sharedOverfill']<=EPS,'schedulerStillExercised':int(sm.get('reevaluations') or 0)>0}
        if not all(gates.values()):decision='REPLICATION_REJECT_OR_DIAGNOSE_FRONTIER_V2'
        elif int(sm.get('admissionAllowsFromScheduler') or 0)>0:decision='REPLICATION_KEEP_FRONTIER_V2_WITH_SCHEDULER_ACTION'
        else:decision='REPLICATION_KEEP_FRONTIER_V2_SAFETY_CLOCK_ONLY'
        out={'version':'ETH_CONTINUOUS_SCHEDULER_FRONTIER_V2_REPLICATION','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'controlSchedulerOnly':compact(br),'candidateFrontierV2':compact(rr),'controlSafety':bs,'candidateSafety':ss,'schedulerAdapter':sm,'frontierV2Binds':rr.get('frontierV2Binds'),'frontierV2Events':rr.get('frontierV2Events',[]),'candidateV83Admissions':(rr.get('v83Admissions') or [])[:260],'gates':gates,'boundary':['conditional preregistered replication','frontier module only relative to scheduler-only control','RepairFirst transition rule unchanged','all downstream modules frozen','realistic HFT only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'control':out['controlSchedulerOnly'],'candidate':out['candidateFrontierV2'],'controlSafety':bs,'candidateSafety':ss,'frontierBinds':out['frontierV2Binds'],'scheduler':{k:v for k,v in sm.items() if k!='events'}},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
