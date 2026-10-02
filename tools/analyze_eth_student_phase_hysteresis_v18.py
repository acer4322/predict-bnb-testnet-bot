from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,statistics
from pathlib import Path
import numpy as np
STAGING=Path(__file__).resolve().parent
if str(STAGING) not in sys.path:sys.path.insert(0,str(STAGING))
import run_eth_dagger60_local_pending_reservation_v1 as lp
import run_eth_target_repair_progress_authority_v1 as pa
import analyze_eth_btc_repair_streak_reentry_v11b as v11b
import analyze_target_btc_eth_phase_hysteresis_v17 as v17
EPS=1e-9

def qstats(z,k):
    a=np.asarray([r[k] for r in z],float)
    return {'n':len(z),'p25':float(np.quantile(a,.25)) if len(a) else None,'median':float(np.median(a)) if len(a) else None,'p75':float(np.quantile(a,.75)) if len(a) else None}

def summarize(acts):
    tr=[]
    acts=sorted(acts,key=lambda z:(z['fillT'],z['n']))
    for i in range(1,len(acts)):
        a,b=acts[i-1],acts[i]
        if a['type']==b['type']:continue
        u=float(b['preUp']);d=float(b['preDown']);g=u+d;gap=abs(u-d);gr=gap/g if g>EPS else 0.;pc=2*min(u,d)/g if g>EPS else 0.
        tr.append({'transition':a['type']+'_to_'+b['type'],'gapRatio':gr,'paircov':pc,'gapMs':int(b['fillT'])-int(a['fillT'])})
    out={}
    for name in ('EXPAND_to_REPAIR','REPAIR_to_EXPAND'):
        z=[r for r in tr if r['transition']==name]
        out[name]={'n':len(z),'gapRatio':qstats(z,'gapRatio'),'paircov':qstats(z,'paircov'),'gapMs':qstats(z,'gapMs')}
    a=np.asarray([r['gapRatio'] for r in tr if r['transition']=='EXPAND_to_REPAIR']);b=np.asarray([r['gapRatio'] for r in tr if r['transition']=='REPAIR_to_EXPAND'])
    if len(a) and len(b):
        vals=np.concatenate([a,b]);order=np.argsort(vals,kind='mergesort');ranks=np.empty(len(vals),float);ranks[order]=np.arange(1,len(vals)+1);rb=ranks[len(a):].sum();out['gapRatioDirectionAuc_RtoE_high']=float((rb-len(b)*(len(b)+1)/2)/(len(a)*len(b)))
    else:out['gapRatioDirectionAuc_RtoE_high']=None
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_student_hyst_v18_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];traj=json.load(open(tmp/'trajectory.json',encoding='utf-8'));models,off1,off2=lp.train_models(tmp,cohort,traj);iso,teacher=pa.build_iso(a.target_db)
        test=[r for r in cohort if r['split']!='TRAIN40'];allacts=[];marketRows=[]
        for i,cr in enumerate(test,1):
            sim=v11b.FirstFillSeqSim(tmp/'tapes'/f"{cr['marketId']}.json.xz",'BOOK_IMBALANCE',models,iso)
            try:
                sim.run_student_progress(models,cr['winner']);acts=list(sim.firstFillActions);allacts.extend(acts);marketRows.append({'marketId':int(cr['marketId']),'materializedCarriers':len(acts)})
            finally:sim.close()
            if i%20==0:print(json.dumps({'progress':i,'of':len(test),'materializedCarriers':len(allacts)}),flush=True)
        targ=v17.build(a.target_db,'ETH');targetSummary=v17.summarize(targ)
        studentSummary=summarize(allacts)
        out={'version':'ETH_STUDENT_PHASE_HYSTERESIS_V18','researchOnly':True,'controllerMutation':False,'boundary':['OUR = current best Progress Authority + Local Pending on Fresh101 development-only','Transitions classified at first actual material fill using authoritative pre-fill inventory','Target only used as structural reference; no Target numeric threshold copied into runtime'],'student':studentSummary,'targetETH':targetSummary,'targetProgressTeacher':teacher,'round1Offline':off1,'round2Offline':off2,'markets':marketRows}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'student':studentSummary,'targetETH':targetSummary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
