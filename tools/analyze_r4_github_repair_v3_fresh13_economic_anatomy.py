from __future__ import annotations
import json,glob,sys,math
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path.cwd();sys.path.insert(0,str(ROOT/'tools'))
import evaluate_r4_github_repair_regime_economic_v3 as v3
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
ROUTE={'FORMATION':'BASELINE','MGMT_NEG_FLOOR':'GLOBAL','MGMT_NONNEG_FLOOR':'REGIME_FALLBACK','PROTECTION':'BASELINE'}

def load_train():
 r=json.loads(v3.TRAIN0.read_text(encoding='utf-8'))['queryRows']
 for p in sorted(glob.glob(v3.TRAIN_GLOB)):r+=json.loads(Path(p).read_text(encoding='utf-8'))['queryRows']
 return r

def load_test():
 r=[]
 for p in sorted(glob.glob(str(P/'r4_github_repair_regime_v3_fresh_batches/out*.json'))):r+=json.loads(Path(p).read_text(encoding='utf-8'))['queryRows']
 return r

def spear(x,y):
 a=pd.Series(np.asarray(x,float));b=pd.Series(np.asarray(y,float))
 if len(a)<5 or a.nunique()<2 or b.nunique()<2:return None
 z=a.rank(method='average').corr(b.rank(method='average'));return float(z) if z is not None and math.isfinite(float(z)) else None

def main():
 train=load_train();test=load_test();full=v3.expert_predictions(train,test); routed=np.asarray([full[ROUTE[v3.regime(r)]][i] for i,r in enumerate(test)],float); base=full['BASELINE']
 rows=[]
 for mid in sorted({int(r['marketId']) for r in test}):
  for rg in ['MGMT_NEG_FLOOR','MGMT_NONNEG_FLOOR']:
   ix=[i for i,r in enumerate(test) if int(r['marketId'])==mid and v3.regime(r)==rg and r.get('repairGain5s') is not None and bool(r.get('repairEconomicEligible'))]
   if not ix:continue
   b=spear(base[ix],[test[i]['repairGain5s'] for i in ix]);c=spear(routed[ix],[test[i]['repairGain5s'] for i in ix]); gains=np.asarray([test[i]['repairGain5s'] for i in ix],float)
   rows.append({'marketId':mid,'regime':rg,'n':len(ix),'baselineSpearman':b,'candidateSpearman':c,'deltaSpearman':None if b is None or c is None else c-b,'meanRepairGain5s':float(np.mean(gains)),'positiveGainRate':float(np.mean(gains>1e-12)),'meanProbabilityShift':float(np.mean(routed[ix]-base[ix]))})
 neg=[r for r in rows if r['regime']=='MGMT_NEG_FLOOR' and r['deltaSpearman'] is not None]; non=[r for r in rows if r['regime']=='MGMT_NONNEG_FLOOR' and r['deltaSpearman'] is not None]
 def agg(z):
  return {'marketsWithComparableSpearman':len(z),'improved':sum(r['deltaSpearman']>0 for r in z),'degraded':sum(r['deltaSpearman']<0 for r in z),'ties':sum(abs(r['deltaSpearman'])<=1e-12 for r in z),'medianDeltaSpearman':float(np.median([r['deltaSpearman'] for r in z])) if z else None,'meanDeltaSpearman':float(np.mean([r['deltaSpearman'] for r in z])) if z else None}
 rep={'version':'R4_GITHUB_REPAIR_V3_FRESH13_ECONOMIC_ANATOMY_V1','posthocDiagnosticOnly':True,'noRetuningAllowed':True,'rows':rows,'managementNegativeFloor':agg(neg),'managementNonnegativeFloor':agg(non)}
 (P/'r4_github_repair_v3_fresh13_economic_anatomy_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
