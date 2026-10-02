from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, recall_score

ROOT=Path(r'C:\BTC5M-worker\.lan_worker_v1\results')
JOBS=[f'r4-adaptive-hft-formal20-c{i}' for i in range(4)]
OUT=ROOT/'r4-adaptive-hft-formal20-aggregate'/'synthesis.json'
PHASES=['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']

def met(y,p):
    y=np.asarray(y,int); p=np.asarray(p,float); z=(p>=.5).astype(int)
    if len(y)==0:return {'n':0,'positiveSupport':0,'auc':None,'ba':None,'r0':None,'r1':None}
    return {'n':int(len(y)),'positiveSupport':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ba':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,'r0':float(recall_score(y,z,pos_label=0,zero_division=0)),'r1':float(recall_score(y,z,pos_label=1,zero_division=0))}

def spear(x,y):
    a=pd.Series(np.asarray(x,float)); b=pd.Series(np.asarray(y,float))
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

def ge(v,thr): return bool(v is not None and v>=thr)
def main():
    rows=[]; markets=[]; all_exact=True
    for j in JOBS:
        d=json.loads((ROOT/j/'synthesis.json').read_text(encoding='utf-8'))
        all_exact=all_exact and bool(d.get('trajectoryIdentityAll'))
        q=d.get('queryRows') or []
        if not q: raise RuntimeError(f'{j} has no queryRows')
        rows.extend(q); markets.extend(d.get('markets') or [])
    ids=sorted({int(x['marketId']) for x in markets if 'marketId' in x})
    uniq={(int(r['marketId']),int(r['atMs'])) for r in rows}
    if len(uniq)!=len(rows): raise RuntimeError(f'duplicate query rows {len(rows)-len(uniq)}')
    summ={'ALL':summarize(rows)}
    for ph in PHASES: summ[ph]=summarize([r for r in rows if r['phase']==ph])
    ds={k:deltas(v) for k,v in summ.items() if v}
    a=ds['ALL']; f=ds['FORMATION_180_300']
    gates={
      'marketCountExact20': len(ids)==20,
      'trajectoryIdentityAll': all_exact,
      'allRepairActionAucDeltaMin0': ge(a['repairActionAuc'],0.0),
      'allStateShapingActionAucDeltaMin0': ge(a['addActionAuc'],0.0),
      'allRepairEconomicSpearmanDeltaMin0': ge(a['repairEconomicSpearman'],0.0),
      'allSafeUpsideBADeltaMin0': ge(a['safeUpsideBA'],0.0),
      'formationStateShapingActionAucDeltaMin0': ge(f['addActionAuc'],0.0),
      'formationStateShapingPositiveRecallDeltaMinMinus003': ge(f['addR1'],-0.03),
      'allRepairPositiveRecallDeltaMinMinus003': ge(a['repairR1'],-0.03),
    }
    passed=all(gates.values())
    rep={'version':'R4_ADAPTIVE_CYCLE_HFT_FORMAL20_AGGREGATE_V1','researchOnly':True,'actionAuthority':False,'promotionEvidence':True,'chunkJobs':JOBS,'markets':ids,'marketCount':len(ids),'queries':len(rows),'trajectoryIdentityAll':all_exact,'summary':summ,'deltaCandidateMinusBaseline':ds,'primaryGates':gates,'allPrimaryGatesPass':passed,'decision':'KEEP_FRESH_HFT_DIRECTION' if passed else 'NOT_KEEP_BLOCK_PROMOTION','contract':'r4_adaptive_cycle_hft_formal_v1_contract.json'}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'marketCount':len(ids),'queries':len(rows),'trajectoryIdentityAll':all_exact,'deltaALL':a,'formationDelta':f,'primaryGates':gates,'allPrimaryGatesPass':passed,'decision':rep['decision']},indent=2),flush=True)
if __name__=='__main__': main()
