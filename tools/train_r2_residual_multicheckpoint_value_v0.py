from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FILES={'train':D/'r2_residual_multicheckpoint_train15_v0.json','validation':D/'r2_residual_multicheckpoint_validation15_v0.json','forward':D/'r2_residual_multicheckpoint_forward20_v0.json'}
OUT=D/'r2_residual_multicheckpoint_value_v0_report.json'
FEATURES=['candidateDelayMs','recoverySideIsUp','absTrackingError','targetNet','actualNet','actualMakerNet','actualMakerGross','desiredMakerGross','makerTargetRealizationRatio','recoveryDesired','recoveryActual','recoveryDeficit','surplusDesired','surplusActual','surplusDeficit','recoveryWorkingExists','recoveryWorkingAgeMs','recoveryWorkingRemaining','recoveryWorkingCum','surplusWorkingExists','surplusWorkingAgeMs','surplusWorkingRemaining','surplusWorkingCum','secondsLeft','directionScore','spotReturn1sBps','spotReturn3sBps','spotQueueImbalance','spotTakerImbalance1s','futuresReturn1sBps','futuresReturn3sBps','futuresQueueImbalance','futuresTakerImbalance1s']
def load(path):
 d=json.load(open(path,encoding='utf-8')); out=[]
 for r in d['rowsData']:
  z={'marketId':r['marketId'],'value':float(r['value']),'candidateDelayMs':r['candidateDelayMs'],'improves':r['improves'],'harms':r['harms']}
  for f in FEATURES:
   if f=='candidateDelayMs': continue
   z[f]=(r.get('features') or {}).get(f)
  out.append(z)
 return pd.DataFrame(out)
def main():
 parts={k:load(v) for k,v in FILES.items()}; tr=parts['train']; cols=FEATURES
 med=float(tr.value.median()); mean=float(tr.value.mean())
 models={'RIDGE':Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('m',Ridge(alpha=10.0))]),'HGBR_SHALLOW':Pipeline([('imp',SimpleImputer(strategy='median')),('m',HistGradientBoostingRegressor(max_depth=2,max_iter=100,learning_rate=.05,l2_regularization=8.0,min_samples_leaf=10,random_state=7))])}
 rep={'version':'R2_RESIDUAL_MULTICHECKPOINT_VALUE_V0','researchOnly':True,'trainingMarkets':'Random1-15 only','validationMarkets':'Random16-30 untouched','forwardMarkets':'Random31-50 untouched','features':cols,'target':'execution-local benefit = -deltaTargetErrorArea','guardrails':['No winner/PnL/Target runtime input','No threshold sweep','Fixed lifecycle delays 0/1/3/5/10s','Residual default remains EXECUTE_AS_R2','Model is not action authority unless forward lift exists'],'models':{}}
 for name,m in models.items():
  m.fit(tr[cols],tr.value)
  rr={}
  for k,p in parts.items():
   y=p.value.to_numpy(float); pred=m.predict(p[cols]);
   rr[k]={'n':len(y),'markets':int(p.marketId.nunique()),'positiveRate':float(np.mean(y>0)),'mae':float(mean_absolute_error(y,pred)),'medianBaselineMae':float(mean_absolute_error(y,np.repeat(med,len(y)))),'meanBaselineMae':float(mean_absolute_error(y,np.repeat(mean,len(y)))),'maeLiftVsMedian':float(1-mean_absolute_error(y,pred)/mean_absolute_error(y,np.repeat(med,len(y)))),'signAccuracy':float(np.mean((pred>0)==(y>0))),'predInterventionRate':float(np.mean(pred>0)),'meanTargetValue':float(np.mean(y)),'meanPredValue':float(np.mean(pred))}
  rep['models'][name]=rr
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
