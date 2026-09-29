from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path
from collections import Counter

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=1917324

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

batch=sibling('guarded_partial_debt_batch_base',Path(__file__).resolve().with_name('run_eth_continuous_guarded_scheduler_modular_batch.py'))
front=batch.front;v38=batch.v38;compact=batch.compact
try:
    from tools.eth_repair_modular import economic_continuous_guarded_partial_debt_generation_candidate_v1_profile
except ImportError:
    from eth_repair_modular import economic_continuous_guarded_partial_debt_generation_candidate_v1_profile


def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    if int(a.market_id)!=FIXED:raise ValueError(a.market_id)
    tmp=Path(tempfile.mkdtemp(prefix='guarded_partial_debt_gen_smoke1_'));stop=threading.Event()
    def hb():
        while not stop.wait(10):print(json.dumps({'heartbeat':'GUARDED_PARTIAL_DEBT_GEN_SMOKE1','ts':time.time(),'market':FIXED}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'GUARDED_PARTIAL_DEBT_GEN_SMOKE1_START','market':FIXED}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[FIXED]
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{FIXED}.json.xz'
        c=batch.mk(batch.Candidate,tape,models,life,cap,tim,econ,price,sur,t44,t47,economic_continuous_guarded_partial_debt_generation_candidate_v1_profile())
        try:
            rr=c.run_candidate_guard(models,cr['winner'])
            full_admissions=list(getattr(c,'v83Admissions',[]));gen_events=list(getattr(c,'v70gEvents',[])) if hasattr(c,'v70gEvents') else []
        finally:c.close()
        ss=front.safety(rr);sm=rr.get('schedulerAdapter') or {};reasons=Counter(str(x.get('reason')) for x in full_admissions)
        cons=abs(float(rr.get('v84CompositeFillQty') or 0)-float(rr.get('v84RepairAllocatedQty') or 0)-float(rr.get('v84OverflowAllocatedQty') or 0))<=1e-7
        behavior=compact(rr);control={'fills':6,'submits':20,'repairExpandRepairRounds':2,'floor':-1.1995814307458144,'pnlDiagnosticOnly':1.4413861617578307,'v83Allows':1}
        delta={'fills':int(behavior['fills'])-6,'submits':int(behavior['submits'])-20,'rounds':int(behavior['repairExpandRepairRounds'])-2,'floor':float(behavior['floor'])-control['floor'],'pnlDiagnosticOnly':float(behavior['pnlDiagnosticOnly'])-control['pnlDiagnosticOnly'],'v83Allows':int(behavior['v83Allows'])-1}
        allsafe=all(float(v)<=EPS for v in ss.values()) and cons
        action_support=(delta['rounds']>0) or (delta['fills']>0 and delta['rounds']>=0)
        gates={'allSafetyZero':allsafe,'allocationConservation':cons,'roundsNonDecreasing':delta['rounds']>=0,'physicalActionSupport':action_support,'floorWithinDevelopmentTolerance':delta['floor']>=-0.25}
        if not allsafe or not gates['roundsNonDecreasing'] or not gates['floorWithinDevelopmentTolerance']:decision='REJECT_PARTIAL_DEBT_GENERATION_TIER0'
        elif action_support:decision='KEEP_PARTIAL_DEBT_GENERATION_FOR_ONE_REPLICATION_ONLY'
        else:decision='STOP_PARTIAL_DEBT_GENERATION_NO_ACTION_SUPPORT'
        out={'version':'ETH_CONTINUOUS_GUARDED_PARTIAL_DEBT_GENERATION_SMOKE1_1917324','date':'2026-09-04','researchOnly':True,'tier':'TIER_0_SINGLE_MARKET_SINGLE_MODULE','marketId':FIXED,'winnerPostHocOnly':cr['winner'],'threshold':0.50,'controlFrozenArtifact':control,'candidate':behavior,'delta':delta,'safety':ss,'allocationConservation':cons,'gates':gates,'decision':decision,'v83ReasonCounts':dict(reasons),'scheduler':{k:v for k,v in sm.items() if k!='events'},'prospectiveTransitionChecks':rr.get('prospectiveTransitionChecks'),'prospectiveTransitionBlocks':rr.get('prospectiveTransitionBlocks'),'profile':rr.get('v80PolicyProfile'),'generationEventSample':gen_events[:100],'admissionSubmits':[x for x in full_admissions if x.get('submit')][:40],'boundary':['single market','single module: GenerationUnlockPolicy only relative to exact guarded scheduler control','threshold fixed 0.50','control reused, not rerun','realistic HFT only','winner/PnL post-hoc only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'candidate':behavior,'delta':delta,'safety':ss,'gates':gates,'reasons':dict(reasons),'scheduler':out['scheduler'],'profile':out['profile']},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
