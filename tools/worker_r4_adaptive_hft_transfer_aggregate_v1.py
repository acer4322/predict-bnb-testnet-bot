from __future__ import annotations
import json,math,os
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,recall_score

ROOT=Path(r'C:\BTC5M-worker\.lan_worker_v1\results')
JOBS=[f'r4-adaptive-hft-transfer-v1-c{i}b' for i in range(4)]
OUT=ROOT/'r4-adaptive-hft-transfer-v1-aggregate'/'synthesis.json'
PHASES=['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']

def met(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=.5).astype(int)
    if len(y)==0:return {'n':0,'positiveSupport':0,'auc':None,'ba':None,'r0':None,'r1':None}
    return {'n':int(len(y)),'positiveSupport':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ba':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,'r0':float(recall_score(y,z,pos_label=0,zero_division=0)),'r1':float(recall_score(y,z,pos_label=1,zero_division=0))}

def spear(x,y):
    a=pd.Series(np.asarray(x,float));b=pd.Series(np.asarray(y,float))
    if len(a)<3 or a.nunique()<2 or b.nunique()<2:return None
    v=a.rank(method='average').corr(b.rank(method='average'))
    return float(v) if v is not None and math.isfinite(float(v)) else None

def summarize(z):
    if not z:return {}
    yr=np.array([r['repairAction5s'] for r in z]); ya=np.array([r['addAction5s'] for r in z])
    rb=np.array([r['repairBase'] for r in z]); rc=np.array([r['repairCand'] for r in z]); ab=np.array([r['addBase'] for r in z]); ac=np.array([r['addCand'] for r in z])
    econ=[r for r in z if r.get('repairGain5s') is not None and r.get('repairEconomicEligible')]
    safe=[r for r in z if r.get('safeUpside5s') is not None]
    return {'queries':len(z),
      'repairAction5s':{'baseline':met(yr,rb),'candidate':met(yr,rc)},
      'stateShapingAction5s':{'baseline':met(ya,ab),'candidate':met(ya,ac)},
      'repairEconomic5s':{'n':len(econ),'baselineSpearman':spear([r['repairBase'] for r in econ],[r['repairGain5s'] for r in econ]),'candidateSpearman':spear([r['repairCand'] for r in econ],[r['repairGain5s'] for r in econ])},
      'safeUpside5s':{'n':len(safe),'positiveSupport':int(sum(r['safeUpside5s'] for r in safe)),'baseline':met([r['safeUpside5s'] for r in safe],[r['addBase'] for r in safe]),'candidate':met([r['safeUpside5s'] for r in safe],[r['addCand'] for r in safe])},
      'meanProbabilityDelta':{'repair':float(np.mean(rc-rb)),'stateShaping':float(np.mean(ac-ab))}}

def diff(a,b): return None if a is None or b is None else float(b-a)
def deltas(s):
    return {'repairActionAuc':diff(s['repairAction5s']['baseline']['auc'],s['repairAction5s']['candidate']['auc']),
      'repairActionBA':diff(s['repairAction5s']['baseline']['ba'],s['repairAction5s']['candidate']['ba']),
      'repairR0':diff(s['repairAction5s']['baseline']['r0'],s['repairAction5s']['candidate']['r0']),
      'repairR1':diff(s['repairAction5s']['baseline']['r1'],s['repairAction5s']['candidate']['r1']),
      'addActionAuc':diff(s['stateShapingAction5s']['baseline']['auc'],s['stateShapingAction5s']['candidate']['auc']),
      'addActionBA':diff(s['stateShapingAction5s']['baseline']['ba'],s['stateShapingAction5s']['candidate']['ba']),
      'addR0':diff(s['stateShapingAction5s']['baseline']['r0'],s['stateShapingAction5s']['candidate']['r0']),
      'addR1':diff(s['stateShapingAction5s']['baseline']['r1'],s['stateShapingAction5s']['candidate']['r1']),
      'repairEconomicSpearman':diff(s['repairEconomic5s']['baselineSpearman'],s['repairEconomic5s']['candidateSpearman']),
      'safeUpsideAuc':diff(s['safeUpside5s']['baseline']['auc'],s['safeUpside5s']['candidate']['auc']),
      'safeUpsideBA':diff(s['safeUpside5s']['baseline']['ba'],s['safeUpside5s']['candidate']['ba']),
      'safeUpsideR0':diff(s['safeUpside5s']['baseline']['r0'],s['safeUpside5s']['candidate']['r0']),
      'safeUpsideR1':diff(s['safeUpside5s']['baseline']['r1'],s['safeUpside5s']['candidate']['r1'])}

def main():
    rows=[];markets=[];all_exact=True
    for j in JOBS:
        p=ROOT/j/'synthesis.json';d=json.loads(p.read_text(encoding='utf-8'))
        all_exact=all_exact and bool(d.get('trajectoryIdentityAll'))
        rows.extend(d.get('queryRows') or []);markets.extend(d.get('markets') or [])
    ids=sorted({int(x['marketId']) for x in markets if 'marketId' in x})
    uniqq={(int(r['marketId']),int(r['atMs'])) for r in rows}
    if len(uniqq)!=len(rows): raise RuntimeError(f'duplicate query rows {len(rows)-len(uniqq)}')
    summ={'ALL':summarize(rows)}
    for ph in PHASES:summ[ph]=summarize([r for r in rows if r['phase']==ph])
    ds={k:deltas(v) for k,v in summ.items() if v}
    overall=ds['ALL']; positive_action=(overall['repairActionAuc'] is not None and overall['repairActionAuc']>=0 and overall['addActionAuc'] is not None and overall['addActionAuc']>=0)
    econ_pos=any((overall[k] is not None and overall[k]>0) for k in ['repairEconomicSpearman','safeUpsideAuc','safeUpsideBA'])
    rep={'version':'R4_ADAPTIVE_CYCLE_HFT_TRANSFER_V1_AGGREGATE','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,
      'chunkJobs':JOBS,'markets':ids,'marketCount':len(ids),'queries':len(rows),'trajectoryIdentityAll':all_exact,'summary':summ,'deltaCandidateMinusBaseline':ds,
      'directionality':{'bothActionAucNonDecreasing':positive_action,'atLeastOneEconomicReadoutImproves':econ_pos,'developmentDirectionallyPositive':bool(all_exact and len(ids)==20 and positive_action and econ_pos),
      'note':'No post-hoc numeric recall-collapse threshold is invented because the frozen V1 transfer contract did not preregister one. This is development directionality only.'},
      'contract':'r4_adaptive_cycle_hft_transfer_v1_contract.json'}
    OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'marketCount':len(ids),'queries':len(rows),'trajectoryIdentityAll':all_exact,'deltaALL':overall,'directionality':rep['directionality'],'phaseDeltas':ds},indent=2),flush=True)
if __name__=='__main__':main()
