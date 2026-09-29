from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_cross_value_paired_forks_training_snapshot_v3.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_cross_actionability_advantage_early_v1.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_cross_actionability_advantage_early_v1.joblib'
FEATURES=['risk','transition','floor','surplus','floor_abs','floor_per_surplus','severity_per_risk']
def prep():
 d=json.loads(SRC.read_text()); rows=[]
 for x in d.get('rows',[]):
  if x.get('error') or not x.get('seedEquivalent') or not x.get('treatmentExecuted'): continue
  s=x.get('source',{}) or {}; floor=float(s.get('floor',0)); surplus=float(s.get('surplus',0)); risk=float(s.get('risk',0)); trans=float(s.get('transition',0)); delta=float(x.get('deltaFloor',0) or 0)
  rows.append({'marketId':int(x['marketId']),'atMs':int(x['atMs']),'risk':risk,'transition':trans,'floor':floor,'surplus':surplus,'floor_abs':abs(floor),'floor_per_surplus':floor/max(abs(surplus),1e-6),'severity_per_risk':abs(floor)/max(risk,1e-6),'deltaFloor':delta,'actionable':int(abs(delta)>1e-9)})
 return pd.DataFrame(rows).sort_values(['marketId','atMs'])
def score_cls(y,p):
 y=np.asarray(y);p=np.clip(np.asarray(p),1e-6,1-1e-6);o={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None}
 if len(np.unique(y))>1:o|={'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p))}
 else:o|={'auc':None,'ap':None}
 return o
def main():
 df=prep(); mids=sorted(int(x) for x in df.marketId.unique()); cut=max(1,int(len(mids)*0.67)); trm=set(mids[:cut]); vam=set(mids[cut:]); tr=df[df.marketId.isin(trm)]; va=df[df.marketId.isin(vam)]
 Xa,Xv=tr[FEATURES],va[FEATURES]
 ca=HistGradientBoostingClassifier(max_depth=2,max_iter=80,learning_rate=.06,l2_regularization=2,random_state=7).fit(Xa,tr.actionable)
 pa=ca.predict_proba(Xv)[:,1] if len(va) else np.array([])
 non=tr[tr.actionable==1]; nonv=va[va.actionable==1]; reg=None; adv={}
 if len(non)>=4:
  reg=HistGradientBoostingRegressor(max_depth=2,max_iter=80,learning_rate=.06,l2_regularization=2,random_state=11).fit(non[FEATURES],non.deltaFloor)
  if len(nonv):
   pred=reg.predict(nonv[FEATURES]);adv={'n':int(len(nonv)),'mae':float(mean_absolute_error(nonv.deltaFloor,pred)),'signAcc':float(np.mean((pred>0)==(nonv.deltaFloor>0))),'predMean':float(np.mean(pred)),'trueMean':float(nonv.deltaFloor.mean())}
 cls=np.where(df.deltaFloor>1e-9,'VALUE_POS',np.where(df.deltaFloor<-1e-9,'VALUE_NEG','NO_OP')); counts={str(k):int(v) for k,v in pd.Series(cls).value_counts().to_dict().items()}
 out={'version':'R4_CROSS_ACTIONABILITY_ADVANTAGE_EARLY_V1','researchOnly':True,'actionAuthority':False,'source':str(SRC.relative_to(ROOT)),'rows':int(len(df)),'markets':int(df.marketId.nunique()),'classCounts':counts,'split':{'trainMarkets':[int(x) for x in sorted(trm)],'valMarkets':[int(x) for x in sorted(vam)]},'features':FEATURES,'actionability':{'train':score_cls(tr.actionable,ca.predict_proba(Xa)[:,1]),'val':score_cls(va.actionable,pa)},'advantage':adv,'guard':'Early research challenger only; market-grouped chronological split; no threshold tuning; latest 12 holdout excluded.'}
 OUT.write_text(json.dumps(out,indent=2));joblib.dump({'actionability':ca,'advantage':reg,'features':FEATURES},MODEL);print(json.dumps(out,indent=2))
if __name__=='__main__':main()
