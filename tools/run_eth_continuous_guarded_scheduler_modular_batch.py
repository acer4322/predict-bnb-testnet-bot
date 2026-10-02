from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util
from pathlib import Path

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

guard=sibling('guarded_scheduler_batch_base',Path(__file__).resolve().with_name('run_eth_continuous_scheduler_prospective_ownership_transition_guard_v1_1916869.py'))
front=guard.front;v38=guard.v38;compact=guard.compact
Candidate=guard.ProspectiveOwnershipTransitionGuardCandidate
try:
    from tools.eth_repair_modular import economic_v1_profile,economic_continuous_guarded_scheduler_candidate_v1_profile
except ImportError:
    from eth_repair_modular import economic_v1_profile,economic_continuous_guarded_scheduler_candidate_v1_profile

def mk(cls,tape,models,life,cap,tim,econ,price,sur,t44,t47,profile):
    return cls(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47,policy_profile=profile)

def safezero(ss):
    return all(float(v)<=EPS for v in ss.values())

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='guarded_scheduler_batch_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'CONTINUOUS_GUARDED_SCHEDULER_BATCH','ts':time.time(),'markets':mids}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'CONTINUOUS_GUARDED_SCHEDULER_BATCH_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);by={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        missing=[m for m in mids if m not in by]
        if missing:raise ValueError(f'markets missing from bundle: {missing}')
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        for mid in mids:
            cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=mk(front.ResponsibilityTransitionCandidate,tape,models,life,cap,tim,econ,price,sur,t44,t47,economic_v1_profile())
            try:br=b.run_transition(models,cr['winner'])
            finally:b.close()
            c=mk(Candidate,tape,models,life,cap,tim,econ,price,sur,t44,t47,economic_continuous_guarded_scheduler_candidate_v1_profile())
            try:rr=c.run_candidate_guard(models,cr['winner'])
            finally:c.close()
            bs=front.safety(br);ss=front.safety(rr);sm=rr.get('schedulerAdapter') or {}
            row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'baseline':compact(br),'candidate':compact(rr),'baselineSafety':bs,'candidateSafety':ss,'scheduler':{k:v for k,v in sm.items() if k!='events'},'prospectiveChecks':int(rr.get('prospectiveTransitionChecks') or 0),'prospectiveBlocks':int(rr.get('prospectiveTransitionBlocks') or 0),'schedulerOriginAdmissions':int(sm.get('admissionAllowsFromScheduler') or 0),'profile':rr.get('v80PolicyProfile')}
            rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        def sm(side,key):return sum(float(r[side].get(key) or 0.0) for r in rows)
        allsafe=all(safezero(r['candidateSafety']) for r in rows)
        agg={'markets':len(rows),'baselineFills':int(sm('baseline','fills')),'candidateFills':int(sm('candidate','fills')),'baselineSubmits':int(sm('baseline','submits')),'candidateSubmits':int(sm('candidate','submits')),'baselineRounds':int(sm('baseline','repairExpandRepairRounds')),'candidateRounds':int(sm('candidate','repairExpandRepairRounds')),'baselinePnlDiagnostic':sm('baseline','pnlDiagnosticOnly'),'candidatePnlDiagnostic':sm('candidate','pnlDiagnosticOnly'),'baselineFloorSum':sm('baseline','floor'),'candidateFloorSum':sm('candidate','floor'),'schedulerReevaluations':sum(int(r['scheduler'].get('reevaluations') or 0) for r in rows),'schedulerOriginAdmissions':sum(int(r['schedulerOriginAdmissions']) for r in rows),'prospectiveChecks':sum(int(r['prospectiveChecks']) for r in rows),'prospectiveBlocks':sum(int(r['prospectiveBlocks']) for r in rows),'candidatePositiveMarkets':sum(float(r['candidate'].get('pnlDiagnosticOnly') or 0)>0 for r in rows),'allSafetyZero':allsafe}
        gates={'marketsComplete':len(rows)==len(mids),'allSafetyZero':allsafe,'schedulerExercised':agg['schedulerReevaluations']>0,'schedulerActionSupportOrExplicitInconclusive':True,'roundsNonDecreasing':agg['candidateRounds']>=agg['baselineRounds']}
        if not allsafe or not gates['roundsNonDecreasing']:decision='REJECT_OR_DIAGNOSE_GUARDED_SCHEDULER_BATCH'
        elif agg['schedulerOriginAdmissions']>0:decision='KEEP_GUARDED_SCHEDULER_FOR_LARGER_FUNCTIONAL_COHORT'
        else:decision='SAFE_BUT_ACTION_SUPPORT_INCONCLUSIVE_NO_THRESHOLD_RELAXATION'
        out={'version':'ETH_CONTINUOUS_GUARDED_SCHEDULER_MODULAR_BATCH_V1','date':'2026-09-04','researchOnly':True,'behaviorChange':True,'marketIds':mids,'controlProfile':economic_v1_profile().describe(),'candidateProfile':economic_continuous_guarded_scheduler_candidate_v1_profile().describe(),'decision':decision,'gates':gates,'aggregate':agg,'rows':rows,'boundary':['generic modular batch runner','event-only transition-correct baseline','candidate changes continuous scheduler + authoritative Repair frontier + prospective ownership-transition ordering guard','all economic thresholds/qty/price/generation/router/allocation frozen','realistic HFT only','winner/PnL post-hoc only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':decision,'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
