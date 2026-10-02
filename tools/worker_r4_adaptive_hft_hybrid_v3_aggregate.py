from pathlib import Path
import json, math
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, recall_score
ROOT=Path(r'C:\BTC5M-worker\.lan_worker_v1\results')
JOBS=[f'r4-adaptive-hft-hybrid-v3-c{i}' for i in range(4)]
OUT=ROOT/'r4-adaptive-hft-hybrid-v3-aggregate'/'synthesis.json'
def met(y,p):
 y=np.asarray(y,int); p=np.asarray(p,float); z=(p>=.5).astype(int)
 return {'n':len(y),'pos':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ba':float(balanced_accuracy_score(y,z)) if len(set(y))>1 else None,'r0':float(recall_score(y,z,pos_label=0,zero_division=0)),'r1':float(recall_score(y,z,pos_label=1,zero_division=0))}
def spear(x,y):
 a=pd.Series(x).rank(method='average'); b=pd.Series(y).rank(method='average'); v=a.corr(b); return float(v) if v is not None and math.isfinite(float(v)) else None
def main():
 rows=[]; markets=[]; exact=True
 for j in JOBS:
  d=json.loads((ROOT/j/'synthesis.json').read_text(encoding='utf-8')); exact &= bool(d.get('trajectoryIdentityAll')); rows += d.get('queryRows') or []; markets += d.get('markets') or []
 ids=sorted({int(m['marketId']) for m in markets});
 if len({(int(r['marketId']),int(r['atMs'])) for r in rows})!=len(rows): raise RuntimeError('duplicate queries')
 y=np.array([r['repairAction5s'] for r in rows]); rb=np.array([r['repairBase'] for r in rows]); rc=np.array([r['repairCand'] for r in rows]);
 b=met(y,rb); c=met(y,rc)
 econ=[r for r in rows if r.get('repairGain5s') is not None and r.get('repairEconomicEligible')]
 sb=spear([r['repairBase'] for r in econ],[r['repairGain5s'] for r in econ]); sc=spear([r['repairCand'] for r in econ],[r['repairGain5s'] for r in econ])
 adddiff=np.asarray([abs(float(r['addCand'])-float(r['addBase'])) for r in rows]); form=[r for r in rows if r['phase']=='FORMATION_180_300']; form_add=max([abs(float(r['addCand'])-float(r['addBase'])) for r in form] or [0.0])
 rep={'version':'R4_ADAPTIVE_HFT_HYBRID_V3_CONSUMED_DIAGNOSTIC','researchOnly':True,'promotionEvidence':False,'marketCount':len(ids),'queries':len(rows),'trajectoryIdentityAll':exact,
 'repair':{'baseline':b,'candidate':c,'aucDelta':c['auc']-b['auc'],'baDelta':c['ba']-b['ba'],'r1Delta':c['r1']-b['r1'],'economicSpearmanBaseline':sb,'economicSpearmanCandidate':sc,'economicSpearmanDelta':sc-sb},
 'stateShapingIdentity':{'maxAbsProbabilityDeltaAll':float(adddiff.max()) if len(adddiff) else None,'maxAbsProbabilityDeltaFormation':float(form_add)},
 'diagnosticGates':{}}
 g=rep['diagnosticGates']; g['marketCount20']=len(ids)==20; g['trajectoryIdentity']=exact; g['repairAucNonDecreasing']=rep['repair']['aucDelta']>=0; g['repairEconomicNonDecreasing']=rep['repair']['economicSpearmanDelta']>=0; g['repairR1DeltaMinMinus003']=rep['repair']['r1Delta']>=-0.03; g['stateShapingNumericallyIdentical']=rep['stateShapingIdentity']['maxAbsProbabilityDeltaAll']<=1e-12
 rep['allDiagnosticGatesPass']=all(g.values()); rep['decision']='KEEP_HYBRID_V3_AS_NEXT_ARCHITECTURE_CANDIDATE' if rep['allDiagnosticGatesPass'] else 'REJECT_HYBRID_V3';
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
