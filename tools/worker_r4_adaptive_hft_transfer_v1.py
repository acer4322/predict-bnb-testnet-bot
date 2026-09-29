from __future__ import annotations
import argparse,json,sys,math
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,recall_score

ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import audit_r4_target_sequence_v13_wholemarket_shadow as sh

EPS=1e-9
PHASES=['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']

def load_adaptive(ckpt_path,bundle,device):
    deep=sh.load_deep(bundle,device); hz,_,_,gate=deep
    ck=torch.load(ckpt_path,map_location='cpu',weights_only=False)
    add=sh.SeqBinary(len(sh.FEATURES)); add.load_state_dict(ck['addState']); add.to(device).eval()
    repair=sh.SeqBinary(len(sh.FEATURES)); repair.load_state_dict(ck['repairState']); repair.to(device).eval()
    # run_market names pm->pActionTimePurposeAdd and rm->pRoleTaker; here rm intentionally carries Repair Demand.
    return (hz,add,repair,gate)

def met(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=.5).astype(int)
    if len(y)==0:return {'n':0,'positiveSupport':0,'auc':None,'ba':None,'r0':None,'r1':None}
    return {'n':int(len(y)),'positiveSupport':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ba':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,'r0':float(recall_score(y,z,pos_label=0,zero_division=0)),'r1':float(recall_score(y,z,pos_label=1,zero_division=0))}

def spearman(x,y):
    x=pd.Series(np.asarray(x,float));y=pd.Series(np.asarray(y,float))
    if len(x)<3 or x.nunique()<2 or y.nunique()<2:return None
    v=x.rank(method='average').corr(y.rank(method='average'))
    return float(v) if v is not None and math.isfinite(float(v)) else None

def action_label(actions,t,purpose):
    hi=int(t)+5000
    return int(any(int(a['atMs'])>int(t) and int(a['atMs'])<=hi and str(a.get('purpose'))==purpose for a in actions))

def future_query(qs,i):
    target=int(qs[i]['atMs'])+5000
    for j in range(i+1,len(qs)):
        tj=int(qs[j]['atMs'])
        if tj>=target:
            return qs[j] if tj<=target+1500 else None
    return None

def rows_from_pair(base,cand):
    if base.get('error') or cand.get('error'):return [],False
    acts0=base.get('actualActions') or [];acts1=cand.get('actualActions') or []
    action_exact=(acts0==acts1)
    bq=base.get('queries') or [];cq=cand.get('queries') or []
    if len(bq)!=len(cq):return [],False
    out=[]
    for i,(b,c) in enumerate(zip(bq,cq)):
        if int(b['atMs'])!=int(c['atMs']) or b.get('raw')!=c.get('raw'):return [],False
        f=future_query(bq,i); raw=b['raw']; gap=max(float(raw.get('abs_gap') or 0.),18.)
        row={'marketId':int(base['marketId']),'atMs':int(b['atMs']),'phase':b['phase'],'secondsLeft':float(b['secondsLeft']),
             'repairBase':float(b['pRoleTaker']),'repairCand':float(c['pRoleTaker']),'addBase':float(b['pActionTimePurposeAdd']),'addCand':float(c['pActionTimePurposeAdd']),
             'repairAction5s':action_label(acts0,b['atMs'],'REPAIR'),'addAction5s':action_label(acts0,b['atMs'],'ADD'),'floor':float(raw.get('floor') or 0.),'absGap':float(raw.get('abs_gap') or 0.),'upside':float(raw.get('upside') or 0.)}
        if f is not None:
            fr=f['raw'];fd=float(fr.get('floor') or 0.)-row['floor']; gd=row['absGap']-float(fr.get('abs_gap') or 0.); ud=float(fr.get('upside') or 0.)-row['upside']
            row['repairGain5s']=max(0.,fd)/gap+max(0.,gd)/gap
            row['repairEconomicEligible']=bool(row['floor']<0 or row['absGap']>EPS)
            row['safeUpside5s']=int(ud>EPS and float(fr.get('floor') or 0.)>=row['floor']-EPS)
            row['floorDelta5s']=fd;row['absGapDelta5s']=-gd;row['upsideDelta5s']=ud
        else:
            row['repairGain5s']=None;row['repairEconomicEligible']=False;row['safeUpside5s']=None
        out.append(row)
    return out,action_exact

def summarize(rows):
    def one(z):
        if not z:return {}
        yr=np.array([r['repairAction5s'] for r in z]);ya=np.array([r['addAction5s'] for r in z]);rb=np.array([r['repairBase'] for r in z]);rc=np.array([r['repairCand'] for r in z]);ab=np.array([r['addBase'] for r in z]);ac=np.array([r['addCand'] for r in z])
        econ=[r for r in z if r.get('repairGain5s') is not None and r.get('repairEconomicEligible')];safe=[r for r in z if r.get('safeUpside5s') is not None]
        return {'queries':len(z),'repairAction5s':{'baseline':met(yr,rb),'candidate':met(yr,rc)},'stateShapingAction5s':{'baseline':met(ya,ab),'candidate':met(ya,ac)},
                'repairEconomic5s':{'n':len(econ),'baselineSpearman':spearman([r['repairBase'] for r in econ],[r['repairGain5s'] for r in econ]),'candidateSpearman':spearman([r['repairCand'] for r in econ],[r['repairGain5s'] for r in econ])},
                'safeUpside5s':{'n':len(safe),'positiveSupport':int(sum(r['safeUpside5s'] for r in safe)),'baseline':met([r['safeUpside5s'] for r in safe],[r['addBase'] for r in safe]),'candidate':met([r['safeUpside5s'] for r in safe],[r['addCand'] for r in safe])},
                'meanProbabilityDelta':{'repair':float(np.mean(rc-rb)),'stateShaping':float(np.mean(ac-ab))}}
    out={'ALL':one(rows)}
    for ph in PHASES:out[ph]=one([r for r in rows if r['phase']==ph])
    return out

def delta(summary):
    def d(a,b):return None if a is None or b is None else float(b-a)
    out={}
    for k,s in summary.items():
        if not s:continue
        out[k]={'repairActionAuc':d(s['repairAction5s']['baseline']['auc'],s['repairAction5s']['candidate']['auc']),'repairActionBA':d(s['repairAction5s']['baseline']['ba'],s['repairAction5s']['candidate']['ba']),
                'addActionAuc':d(s['stateShapingAction5s']['baseline']['auc'],s['stateShapingAction5s']['candidate']['auc']),'addActionBA':d(s['stateShapingAction5s']['baseline']['ba'],s['stateShapingAction5s']['candidate']['ba']),
                'repairEconomicSpearman':d(s['repairEconomic5s']['baselineSpearman'],s['repairEconomic5s']['candidateSpearman']),'safeUpsideAuc':d(s['safeUpside5s']['baseline']['auc'],s['safeUpside5s']['candidate']['auc']),'safeUpsideBA':d(s['safeUpside5s']['baseline']['ba'],s['safeUpside5s']['candidate']['ba'])}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle-dir',required=True);ap.add_argument('--baseline-checkpoint',required=True);ap.add_argument('--candidate-checkpoint',required=True);ap.add_argument('--out',required=True);ap.add_argument('--start',type=int,default=0);ap.add_argument('--count',type=int,default=999);args=ap.parse_args()
    bundle=Path(args.bundle_dir);all_ids=list(map(int,json.loads((bundle/'validation20_ids.json').read_text())));ids=all_ids[args.start:args.start+args.count];wm=json.loads((bundle/'window_end_map.json').read_text());device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    bm=load_adaptive(Path(args.baseline_checkpoint),bundle,device);cm=load_adaptive(Path(args.candidate_checkpoint),bundle,device)
    rows=[];per=[];all_exact=True
    for mid in ids:
        try:
            b=sh.run_market(mid,int(wm[str(mid)]),bm,device,bundle);c=sh.run_market(mid,int(wm[str(mid)]),cm,device,bundle);rr,exact=rows_from_pair(b,c);all_exact=all_exact and exact
            per.append({'marketId':mid,'queries':len(rr),'trajectoryExact':exact,'r3SummaryBaseline':b.get('r3Summary'),'r3SummaryCandidate':c.get('r3Summary')});rows.extend(rr)
            print(json.dumps({'marketId':mid,'queries':len(rr),'trajectoryExact':exact},ensure_ascii=False),flush=True)
        except Exception as e:
            all_exact=False;per.append({'marketId':mid,'error':f'{type(e).__name__}:{e}','queries':0,'trajectoryExact':False});print(json.dumps(per[-1]),flush=True)
    summ=summarize(rows);rep={'version':'R4_ADAPTIVE_CYCLE_HFT_TRANSFER_V1_RESULT','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'device':str(device),'cohort':ids,'trajectoryIdentityAll':all_exact,'markets':per,'queryRows':rows,'summary':summ,'deltaCandidateMinusBaseline':delta(summ),'guards':['realistic HFT/no dream fill','same HFT market and action trajectory for baseline/candidate','strict-past sequence features','no settlement/winner labels','shadow only; no order mutation'],'contract':'r4_adaptive_cycle_hft_transfer_v1_contract.json'}
    Path(args.out).parent.mkdir(parents=True,exist_ok=True);Path(args.out).write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'trajectoryIdentityAll':all_exact,'deltaCandidateMinusBaseline':rep['deltaCandidateMinusBaseline'].get('ALL'),'summaryAll':summ.get('ALL')},indent=2),flush=True)
if __name__=='__main__':main()
