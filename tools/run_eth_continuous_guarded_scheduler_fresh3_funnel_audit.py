from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,threading,time,joblib,sys,importlib.util,statistics
from pathlib import Path
from collections import Counter,defaultdict

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
EPS=1e-9;FIXED=[1917197,1917298,1917324]

def sibling(name,path):
    p=Path(path);s=importlib.util.spec_from_file_location(name,p)
    if s is None or s.loader is None:raise ImportError(p)
    m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m

batch=sibling('guarded_scheduler_funnel_batch',Path(__file__).resolve().with_name('run_eth_continuous_guarded_scheduler_modular_batch.py'))
front=batch.front;v38=batch.v38;Candidate=batch.Candidate;compact=batch.compact
try:
    from tools.eth_repair_modular import economic_continuous_guarded_scheduler_candidate_v1_profile
except ImportError:
    from eth_repair_modular import economic_continuous_guarded_scheduler_candidate_v1_profile

def dist(vals):
    xs=sorted(float(x) for x in vals if x is not None)
    if not xs:return {'n':0,'min':None,'median':None,'max':None,'ge050':0,'lt050':0}
    return {'n':len(xs),'min':xs[0],'median':statistics.median(xs),'max':xs[-1],'ge050':sum(x>=0.5 for x in xs),'lt050':sum(x<0.5 for x in xs)}

def main():
    ap=argparse.ArgumentParser()
    for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
    ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    if mids!=FIXED:raise ValueError(f'fixed preregistered market order required: {FIXED}')
    tmp=Path(tempfile.mkdtemp(prefix='guarded_scheduler_funnel_'));stop=threading.Event()
    def hb():
        while not stop.wait(15):print(json.dumps({'heartbeat':'GUARDED_SCHEDULER_FRESH3_FUNNEL','ts':time.time(),'markets':mids}),flush=True)
    threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'GUARDED_SCHEDULER_FRESH3_FUNNEL_START','markets':mids}),flush=True)
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);by={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
        agg_reason=Counter();agg_v75_dec=Counter();agg_v75_rec=Counter();agg_pe=defaultdict(list)
        for mid in mids:
            cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            c=batch.mk(Candidate,tape,models,life,cap,tim,econ,price,sur,t44,t47,economic_continuous_guarded_scheduler_candidate_v1_profile())
            try:
                r=c.run_candidate_guard(models,cr['winner'])
                adm=list(getattr(c,'v83Admissions',[]) or [])
                v75=list(getattr(c,'v75Events',[]) or [])
            finally:c.close()
            rc=Counter(str(x.get('reason') or 'NONE') for x in adm);pe=defaultdict(list)
            for x in adm:
                pe[str(x.get('reason') or 'NONE')].append(x.get('pExpand'))
            vd=Counter(str(x.get('decision') or 'NONE') for x in v75);vr=Counter(str(x.get('reason') or 'NONE') for x in v75)
            for k,v in rc.items():agg_reason[k]+=v
            for k,v in vd.items():agg_v75_dec[k]+=v
            for k,v in vr.items():agg_v75_rec[k]+=v
            for k,vals in pe.items():agg_pe[k].extend(vals)
            ss=front.safety(r);sm=r.get('schedulerAdapter') or {}
            row={'marketId':mid,'winnerPostHocOnly':cr['winner'],'behavior':compact(r),'safety':ss,'scheduler':{k:v for k,v in sm.items() if k!='events'},'v83Reasons':dict(rc),'pExpandByV83Reason':{k:dist(v) for k,v in pe.items()},'v75OwnershipDecisions':dict(vd),'v75RecoverabilityReasons':dict(vr),'prospectiveChecks':int(r.get('prospectiveTransitionChecks') or 0),'prospectiveBlocks':int(r.get('prospectiveTransitionBlocks') or 0)};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        ge050_nonopp=sum(v['ge050'] for k,v in {k:dist(vals) for k,vals in agg_pe.items()}.items() if k!='OPPORTUNITY_BELOW_FROZEN_V44_THRESHOLD')
        reason_total=sum(agg_reason.values());reason_share={k:(v/reason_total if reason_total else 0.0) for k,v in agg_reason.items()}
        peagg={k:dist(vals) for k,vals in agg_pe.items()}
        allsafe=all(all(float(v)<=EPS for v in x['safety'].values()) for x in rows)
        dominant=max(agg_reason,key=agg_reason.get) if agg_reason else None
        if agg_reason.get('NO_RECOVERABLE_THESIS',0)>0 and peagg.get('NO_RECOVERABLE_THESIS',{}).get('ge050',0)>0:next_seam='OWNERSHIP_RECOVERABILITY'
        elif agg_reason.get('GENERATION_ALREADY_OWNS_EXPAND',0)+agg_reason.get('GLOBAL_EXPAND_OCCUPIED',0)>0:next_seam='CHILD_OCCUPANCY_GENERATION'
        elif dominant=='OPPORTUNITY_BELOW_FROZEN_V44_THRESHOLD' and ge050_nonopp==0:next_seam='OPPORTUNITY_SUPPORT_LOW_BUT_NO_TUNING_FROM_THIS_AUDIT'
        elif any('TRANSITION' in k for k in agg_reason):next_seam='RESPONSIBILITY_TOPOLOGY_KEEP_REPAIR_FIRST'
        else:next_seam='MIXED_FUNNEL_NEEDS_DECOMPOSITION'
        out={'version':'ETH_CONTINUOUS_GUARDED_SCHEDULER_FRESH3_FUNNEL_AUDIT','date':'2026-09-04','researchOnly':True,'behaviorChange':False,'marketIds':mids,'candidateProfile':economic_continuous_guarded_scheduler_candidate_v1_profile().describe(),'allSafetyZero':allsafe,'aggregate':{'v83ReasonCounts':dict(agg_reason),'v83ReasonShare':reason_share,'pExpandByReason':peagg,'v75OwnershipDecisionCounts':dict(agg_v75_dec),'v75RecoverabilityReasonCounts':dict(agg_v75_rec),'schedulerReevaluations':sum(int(x['scheduler'].get('reevaluations') or 0) for x in rows),'schedulerOriginAdmissions':sum(int(x['scheduler'].get('admissionAllowsFromScheduler') or 0) for x in rows),'prospectiveChecks':sum(x['prospectiveChecks'] for x in rows),'prospectiveBlocks':sum(x['prospectiveBlocks'] for x in rows),'dominantV83Reason':dominant,'nextSeam':next_seam},'rows':rows,'decision':'KEEP_FUNNEL_DIAGNOSIS_NO_PARAMETER_CHANGE' if allsafe else 'DIAGNOSTIC_RERUN_SAFETY_MISMATCH','boundary':['diagnostic rerun only','exact candidate behavior','no threshold/cadence/qty/price changes','no Target future runtime','realistic HFT only','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'aggregate':out['aggregate'],'allSafetyZero':allsafe},ensure_ascii=False),flush=True)
    finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
