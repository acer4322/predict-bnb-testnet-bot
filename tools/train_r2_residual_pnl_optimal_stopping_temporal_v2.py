from __future__ import annotations
import json,math
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,accuracy_score,log_loss
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'; EPS=1e-9

def load(split):
 if split!='forward': return json.loads((D/f'r2_residual_pnl_action_values_v1_{split}.json').read_text())['rows']
 rows=[]
 for i in range(1,5): rows+=json.loads((D/f'r2_residual_pnl_action_values_v1_forward_b{i}.json').read_text())['rows']
 return rows
raw={s:load(s) for s in ['train','validation','forward']}
base_names=sorted({k for r in raw['train'] for k,v in r['features'].items() if isinstance(v,(int,float)) or v is None})
# Only deterministic strict-past temporal transforms of existing scalar state.
def val(r,k):
 v=r['features'].get(k)
 try: x=float(v) if v is not None else np.nan
 except: return np.nan
 return x if math.isfinite(x) else np.nan

def enrich(rows):
 by={}
 for r in rows: by.setdefault(int(r['marketId']),[]).append(r)
 out=[]
 for mid,rs in by.items():
  rs=sorted(rs,key=lambda z:int(z['candidateDelayMs'])); hist=[]
  for r in rs:
   d=dict(r); f=dict(r['features']); prev=hist[-1] if hist else None
   for k in base_names:
    cur=val(r,k); pv=val(prev,k) if prev is not None else np.nan
    f['delta_'+k]=(cur-pv) if math.isfinite(cur) and math.isfinite(pv) else np.nan
    vals=[val(h,k) for h in hist+[r]]; vals=[x for x in vals if math.isfinite(x)]
    f['since_start_'+k]=(cur-vals[0]) if vals and math.isfinite(cur) else np.nan
   f['elapsedSincePriorMs']=int(r['candidateDelayMs'])-int(prev['candidateDelayMs']) if prev else 0
   f['hasPriorCheckpoint']=1 if prev else 0
   d['featuresTemporal']=f; out.append(d); hist.append(r)
 return out
raw={s:enrich(raw[s]) for s in raw}
feat_names=sorted({k for r in raw['train'] for k,v in r['featuresTemporal'].items() if isinstance(v,(int,float)) or v is None})+['candidateDelayMs']
def vec(r):
 f=r['featuresTemporal']; a=[]
 for k in feat_names:
  v=r['candidateDelayMs'] if k=='candidateDelayMs' else f.get(k)
  try:x=float(v) if v is not None else np.nan
  except:x=np.nan
  a.append(x if math.isfinite(x) else np.nan)
 return a

def build(rows):
 by={}
 for r in rows:by.setdefault(int(r['marketId']),[]).append(r)
 risk=[]; oracle={}
 for mid,rs in by.items():
  rs=sorted(rs,key=lambda z:int(z['candidateDelayMs'])); b=float(rs[0]['baselinePnl']); cand=[]
  for r in rs:
   v=r['actionPnl']; fam='PASSIVE_PRIORITY' if v['PASSIVE_PRIORITY']>=v['TAKER_RECOVERY'] else 'TAKER_RECOVERY'; cand.append((float(v[fam]),int(r['candidateDelayMs']),fam,r))
  best=max([(b,10**12,'BASELINE_R2',None)]+cand,key=lambda z:(z[0],-z[1]));
  if best[0]<=b+EPS:best=(b,10**12,'BASELINE_R2',None)
  oracle[mid]={'baseline':b,'oraclePnl':best[0],'oracleDelayMs':None if best[3] is None else best[1],'oracleAction':best[2]}
  for r in rs:
   d=int(r['candidateDelayMs'])
   if best[3] is None:lab=0
   elif d<best[1]:lab=0
   elif d==best[1]:lab=1
   else:break
   risk.append({'marketId':mid,'row':r,'y':lab})
 return risk,oracle
risk={};oracle={}
for s in raw:risk[s],oracle[s]=build(raw[s])
X={s:np.asarray([vec(x['row']) for x in risk[s]],float) for s in risk};y={s:np.asarray([x['y'] for x in risk[s]],int) for s in risk}
mods={'LOGIT':make_pipeline(SimpleImputer(strategy='median',add_indicator=True),StandardScaler(),LogisticRegression(max_iter=4000,class_weight='balanced',C=.3)),
'HGB':make_pipeline(SimpleImputer(strategy='median',add_indicator=True),HistGradientBoostingClassifier(max_depth=2,max_iter=100,learning_rate=.05,l2_regularization=2.,random_state=2))}
def met(m,s):
 p=m.predict_proba(X[s])[:,1]; pr=(p>=.5).astype(int); yy=y[s]
 return {'n':len(yy),'positives':int(yy.sum()),'auc':float(roc_auc_score(yy,p)) if len(set(yy))>1 else None,'ap':float(average_precision_score(yy,p)) if yy.sum() else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pr)),'accuracy':float(accuracy_score(yy,pr)),'logLoss':float(log_loss(yy,p,labels=[0,1]))}
rep={'version':'R2_RESIDUAL_PNL_OPTIMAL_STOPPING_TEMPORAL_V2','researchOnly':True,'representation':'current scalar state + strict-past delta and since-episode-start transforms only','features':feat_names,'models':{},'risksetCounts':{s:{'n':len(y[s]),'act':int(y[s].sum())} for s in y},'guards':['same fixed oracle action/delay space','train15 only','no threshold sweep','no future/outcome in runtime features']}
for n,m in mods.items():
 m.fit(X['train'],y['train']);rep['models'][n]={s:met(m,s) for s in ['train','validation','forward']}
out=D/'r2_residual_pnl_optimal_stopping_temporal_v2_report.json';out.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
