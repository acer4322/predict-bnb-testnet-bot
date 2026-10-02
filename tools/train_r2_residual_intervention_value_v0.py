from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'r2_residual_intervention_curriculum_v0.json'
OUT=D/'r2_residual_intervention_value_v0_report.json'
FEATURES=['recoverySideIsUp','absTrackingError','targetNet','actualNet','actualMakerNet','actualMakerGross','desiredMakerGross','makerTargetRealizationRatio','recoveryDesired','recoveryActual','recoveryDeficit','surplusDesired','surplusActual','surplusDeficit','recoveryWorkingExists','recoveryWorkingAgeMs','recoveryWorkingRemaining','recoveryWorkingCum','surplusWorkingExists','surplusWorkingAgeMs','surplusWorkingRemaining','surplusWorkingCum','secondsLeft','directionScore','spotReturn1sBps','spotReturn3sBps','spotQueueImbalance','spotTakerImbalance1s','futuresReturn1sBps','futuresReturn3sBps','futuresQueueImbalance','futuresTakerImbalance1s']

def main():
 d=json.load(open(SRC,encoding='utf-8')); rows=[]
 for r in d['rows']:
  if r.get('episodeAtMs') is None: continue
  z={'marketId':r['marketId'],'episodeAtMs':r['episodeAtMs'],'value':-float(r['deltaTargetErrorArea'])}
  for f in FEATURES: z[f]=(r.get('features') or {}).get(f)
  rows.append(z)
 df=pd.DataFrame(rows).sort_values('episodeAtMs').reset_index(drop=True)
 n=len(df); a=int(n*.60); b=int(n*.80)
 parts={'train':df.iloc[:a],'validation':df.iloc[a:b],'test':df.iloc[b:]}
 Xcols=[c for c in FEATURES if c in df.columns]
 models={
  'RIDGE':Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('m',Ridge(alpha=10.0))]),
  'HGBR_SHALLOW':Pipeline([('imp',SimpleImputer(strategy='median')),('m',HistGradientBoostingRegressor(max_depth=2,max_iter=80,learning_rate=.05,l2_regularization=5.0,min_samples_leaf=8,random_state=7))])}
 tr=parts['train']; med=float(tr['value'].median()); mean=float(tr['value'].mean())
 report={'version':'R2_RESIDUAL_INTERVENTION_VALUE_V0','researchOnly':True,'rows':n,'splitRows':{k:len(v) for k,v in parts.items()},'features':Xcols,'target':'-deltaTargetErrorArea (higher = more execution-local benefit from OPPOSITE_PRIORITY)','guardrails':['No winner/PnL in target or features','Chronological 60/20/20 split','No threshold sweep','Small-sample diagnostic only'] ,'models':{}}
 for name,m in models.items():
  m.fit(tr[Xcols],tr['value'])
  rr={}
  for k,p in parts.items():
   pred=m.predict(p[Xcols]); y=p['value'].to_numpy(float)
   rr[k]={'n':len(y),'mae':float(mean_absolute_error(y,pred)),'medianBaselineMae':float(mean_absolute_error(y,np.repeat(med,len(y)))),'meanBaselineMae':float(mean_absolute_error(y,np.repeat(mean,len(y)))),'meanTarget':float(np.mean(y)),'meanPred':float(np.mean(pred)),'signAccuracy':float(np.mean((pred>0)==(y>0)))}
  report['models'][name]=rr
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
