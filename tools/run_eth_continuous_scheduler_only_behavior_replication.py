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

base=sibling('continuous_scheduler_smoke_base',Path(__file__).resolve().with_name('run_eth_continuous_scheduler_only_behavior_smoke_1916869.py'))
front=base.front;v38=base.v38;v80=base.v80
ContinuousSchedulerOnlyCandidate=base.ContinuousSchedulerOnlyCandidate
compact=base.compact
try:
    from tools.eth_repair_modular import economic_continuous_scheduler_only_v1_profile
except ImportError:
    from eth_repair_modular import economic_continuous_scheduler_only_v1_profile

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id)
    if mid not in ALLOWED:raise ValueError(mid)
    tmp=Path(tempfile.mkdtemp(prefix='continuous_scheduler_replication_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'CONTINUOUS_SCHEDULER_REPLICATION','ts':time.time(),'market':mid}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'CONTINUOUS_SCHEDULER_REPLICATION_START','market':mid}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz'
        def mk(cls,profile):return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=profile)
        b=mk(front.ResponsibilityTransitionCandidate,v80.economic_v1_profile())
        try:br=b.run_transition(models,cr['winner'])
        finally:b.close()
        c=mk(ContinuousSchedulerOnlyCandidate,economic_continuous_scheduler_only_v1_profile())
        try:rr=c.run_candidate(models,cr['winner'])
        finally:c.close()
        ss=front.safety(rr);allsafe=all(float(v)<=EPS for v in ss.values());sm=rr.get('schedulerAdapter') or {};allow=int(sm.get('admissionAllowsFromScheduler') or 0)
        if not allsafe:decision='REJECT_CONTINUOUS_SCHEDULER_REPLICATION_SAFETY'
        elif allow<=0:decision='REPLICATION_NO_SCHEDULER_ORIGIN_ADMISSION'
        elif compact(rr)['fills']>compact(br)['fills'] or compact(rr)['repairExpandRepairRounds']>compact(br)['repairExpandRepairRounds']:decision='REPLICATION_SUPPORT_WITH_PHYSICAL_EFFECT'
        else:decision='REPLICATION_SUPPORT_CLOCK_ONLY_EXECUTION_NOT_MATERIALIZED'
        out={'version':'ETH_CONTINUOUS_SCHEDULER_ONLY_BEHAVIOR_REPLICATION','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'marketId':mid,'winnerPostHocOnly':cr['winner'],'decision':decision,'baselineEventClock':compact(br),'candidateContinuousClock':compact(rr),'schedulerAdapter':sm,'safety':ss,'allSafetyZero':allsafe,'candidateV83Admissions':(rr.get('v83Admissions') or [])[:240],'boundary':['conditional preregistered replication','single module replacement: ResponsibilityScheduler only','all downstream authority frozen','realistic HFT only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'baseline':out['baselineEventClock'],'candidate':out['candidateContinuousClock'],'scheduler':{k:v for k,v in sm.items() if k!='events'},'safety':ss},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
