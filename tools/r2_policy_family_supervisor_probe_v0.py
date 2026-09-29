from __future__ import annotations
import json,math,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import ExtraTreesClassifier,RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import LeaveOneOut,cross_val_predict
from sklearn.metrics import roc_auc_score,accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
CAP=['mature_cap2_maker_only_v0_random15_part1.json','mature_cap2_maker_only_v0_random15_rest.json']
MAN=['mature_inventory_manifold_v0_random15.json']
FIELDS=['directionScore','spotMinusStrikeBps','chainlinkMinusStrikeBps','spotReturn1sBps','spotReturn3sBps','futuresReturn1sBps','futuresReturn3sBps','spotQueueImbalance','futuresQueueImbalance','spotTakerImbalance1s','futuresTakerImbalance1s','predictUpMid','predictDownMid']
def finite(x):
 try:v=float(x);return v if math.isfinite(v) else np.nan
 except:return np.nan
def pnlmap(files):
 out={}
 for n in files:
  p=BASE/n
  if not p.exists():continue
  d=json.loads(p.read_text(encoding='utf8'))
  for r in d.get('rows',[]):out[int(r['marketId'])]=float(r['actualExecution']['realizedPnl'])
 return out
def feats(mid):
 s=sorted(load_public_snapshots(mid),key=lambda z:int(z.get('sampledAtMs') or 0))
 if not s:return {}
 t0=int(s[0].get('sampledAtMs') or 0); w=[z for z in s if int(z.get('sampledAtMs') or 0)<=t0+60000]
 o={}
 for f in FIELDS:
  a=np.array([finite(z.get(f)) for z in w],float);a=a[np.isfinite(a)]
  if len(a):o[f+'_mean']=float(a.mean());o[f+'_std']=float(a.std());o[f+'_last']=float(a[-1]);o[f+'_maxabs']=float(np.max(np.abs(a)))
  else:
   for k in ('mean','std','last','maxabs'):o[f+'_'+k]=np.nan
 o['samples60s']=len(w);return o
def main():
 c=pnlmap(CAP);m=pnlmap(MAN); mids=sorted(set(c)&set(m));rows=[]
 for mid in mids:
  x=feats(mid);x.update({'marketId':mid,'capPnl':c[mid],'manifoldPnl':m[mid],'deltaManifoldMinusCap':m[mid]-c[mid],'labelManifoldBetter':int(m[mid]>c[mid])});rows.append(x)
 df=pd.DataFrame(rows); features=[x for x in df.columns if x not in {'marketId','capPnl','manifoldPnl','deltaManifoldMinusCap','labelManifoldBetter'}]; X=df[features];y=df.labelManifoldBetter.to_numpy();loo=LeaveOneOut(); models={
 'logit':make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=.25,max_iter=2000)),
 'rf':make_pipeline(SimpleImputer(strategy='median'),RandomForestClassifier(n_estimators=300,min_samples_leaf=2,max_features=.7,random_state=20260823)),
 'extra':make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=300,min_samples_leaf=2,max_features=.7,random_state=20260823))}
 res={}
 for name,model in models.items():
  p=cross_val_predict(model,X,y,cv=loo,method='predict_proba')[:,1];pred=(p>=.5).astype(int);res[name]={'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'accuracy':float(accuracy_score(y,pred)),'probs':p.tolist()}
 best=max(res,key=lambda k:(res[k]['auc'] or -1,res[k]['accuracy'])); p=np.array(res[best]['probs']); chosen=np.where(p>=.5,df.manifoldPnl,df.capPnl); oracle=np.maximum(df.manifoldPnl,df.capPnl); cap=np.array(df.capPnl);man=np.array(df.manifoldPnl)
 report={'version':'R2_POLICY_FAMILY_SUPERVISOR_PROBE_V0','researchOnly':True,'performanceClaim':False,'strictPastFeatures':'first 60s public snapshots only','markets':mids,'labels':'which complete closed-loop family has higher terminal PnL','classBalance':{'manifoldBetter':int(y.sum()),'capBetterOrEqual':int(len(y)-y.sum())},'models':res,'bestModel':best,'economicsLOOCV':{'selectorPnl':float(chosen.sum()),'alwaysCapPnl':float(cap.sum()),'alwaysManifoldPnl':float(man.sum()),'oracleFamilyPnl':float(oracle.sum())},'rows':[{'marketId':int(df.iloc[i].marketId),'capPnl':float(cap[i]),'manifoldPnl':float(man[i]),'labelManifoldBetter':int(y[i]),'selectorProbManifold':float(p[i]),'selectorChoice':'MANIFOLD' if p[i]>=.5 else 'CAP2','selectorPnl':float(chosen[i])} for i in range(len(df))]}
 (BASE/'r2_policy_family_supervisor_probe_v0_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf8');print(json.dumps({k:v for k,v in report.items() if k not in ('rows','models')},ensure_ascii=False,indent=2));print(json.dumps(res,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
