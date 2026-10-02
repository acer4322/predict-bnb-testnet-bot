from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,log_loss
SRC=Path('data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_state_rows_v2.csv')
OUT=Path('data/research/r4_v0/p0_provenance_v1/TARGET_MULTILOOP_GROWTH_TEACHER_V1B_MARKETSECOND_20260905.json')
MODEL=Path('data/research/r4_v0/p0_provenance_v1/target_multiloop_growth_teacher_v1b_marketsecond_20260905.joblib')
FEATURES=['seconds_left','weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','events_5s','events_15s','transitions_15s','mode_age_s','coverage','floor_per_gross','abs_gap']
ABL={'TIME':['seconds_left'],'ROOTS':['weak_active_roots','dominant_active_roots'],'TIME_ROOTS':['seconds_left','weak_active_roots','dominant_active_roots'],'OWNER_PROGRESS':['weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio']}
def score(y,p):
 return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'balancedAccuracyAt05':float(balanced_accuracy_score(y,p>=.5)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def fit(tr,te,fs):
 x=tr[fs].replace([np.inf,-np.inf],np.nan).fillna(0.).to_numpy(float);y=tr.label.to_numpy(int);xt=te[fs].replace([np.inf,-np.inf],np.nan).fillna(0.).to_numpy(float);yt=te.label.to_numpy(int)
 m=HistGradientBoostingClassifier(max_depth=4,max_iter=160,learning_rate=.06,l2_regularization=1,random_state=23).fit(x,y);p=m.predict_proba(xt)[:,1]
 return m,{'trainRows':len(tr),'testRows':len(te),'testMarkets':int(te.market_id.nunique()),'trainRate':float(y.mean()),'testRate':float(yt.mean()),**score(yt,p)}
def main():
 d=pd.read_csv(SRC);d=d[np.isfinite(d.seconds_left)&(d.seconds_left>0)&(d.seconds_left<=300)].copy();d['sec_bucket']=np.floor(d.seconds_left).astype(int);d=d.sort_values(['market_id','t']).groupby(['market_id','sec_bucket'],as_index=False).tail(1).copy();d['roots']=d.weak_active_roots.fillna(0)+d.dominant_active_roots.fillna(0)
 rows=[]
 for mid,g in d.groupby('market_id',sort=False):
  g=g.sort_values('t');ts=g.t.to_numpy(np.int64);rr=g.roots.to_numpy(float)
  for i,r in enumerate(g.itertuples(index=False)):
   j=np.searchsorted(ts,ts[i]+5000,side='left')
   if j>=len(g):continue
   z=r._asdict();z['label']=int(rr[j]>rr[i]+1e-9);z['futureRoots5s']=float(rr[j]);rows.append(z)
 x=pd.DataFrame(rows);mids=sorted(x.market_id.unique());n=len(mids);folds=[]
 for a,b in [(.5,.625),(.625,.75),(.75,.875)]:
  tr=x[x.market_id.isin(set(mids[:int(n*a)]))];te=x[x.market_id.isin(set(mids[int(n*a):int(n*b)]))];_,s=fit(tr,te,FEATURES);s['trainFraction']=a;s['testRange']=[a,b];folds.append(s)
 cut=int(n*.8);tr=x[x.market_id.isin(set(mids[:cut]))];te=x[x.market_id.isin(set(mids[cut:]))];abl={k:fit(tr,te,f)[1] for k,f in ABL.items()};model,full=fit(tr,te,FEATURES);joblib.dump({'model':model,'features':FEATURES,'label':'future_5s_live_root_growth','sampling':'one_latest_state_per_market_second'},MODEL)
 out={'version':'TARGET_MULTILOOP_GROWTH_TEACHER_V1B_MARKETSECOND','date':'2026-09-05','researchOnly':True,'actionAuthority':False,'rows':len(x),'markets':int(x.market_id.nunique()),'label':'future 5s live-root count exceeds current','features':FEATURES,'forwardChronologyFolds':folds,'final20pctHoldout':full,'ablation':abl,'decision':'KEEP_GROWTH_AS_WEAK_BELIEF_ONLY' if min(s['auc'] for s in folds)>=.58 else 'DO_NOT_USE_GROWTH_BELIEF','boundary':['future roots offline label only','no winner/PnL/future price runtime inputs','one state per market-second','not direct birth authority']};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'folds':folds,'final':full,'ablation':abl},ensure_ascii=False))
if __name__=='__main__':main()
