from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'; SRC=ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_unseen24_v1_rows.csv'; OUT=P/'r4_management_testbed_v0_completion_capacity_lomo_v1.json'
d=pd.read_csv(SRC);d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy();d['y']=(d.futureWeakMakerFill5s>0).astype(int)
SETS={
 'LOCAL_ECON':['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','floor','absNet','requested_px'],
 'RESPONSIBILITY_DYNAMICS':['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s','requested_px'],
 'FROZEN_BELIEF_STATE':['p_m0_port','p_m0_full','p_event_full','p_route_continue_full','p_continue_full','p_handoff_full','p_observe_full'],
 'DYNAMICS_PLUS_BELIEF':['weak_active_owners','weak_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s','requested_px','p_m0_port','p_m0_full','p_event_full','p_continue_full','p_observe_full']}
def main():
 mids=sorted(d.marketId.unique());out={}
 for name,fs in SETS.items():
  yy=[];pp=[];pred=[];mm=[]
  for m in mids:
   tr=d[d.marketId!=m];te=d[d.marketId==m]
   if tr.y.nunique()<2:continue
   model=make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000))
   model.fit(tr[fs],tr.y);p=model.predict_proba(te[fs])[:,1];q=(p>=.5).astype(int)
   yy+=te.y.tolist();pp+=p.tolist();pred+=q.tolist();mm+=[int(m)]*len(te)
  y=np.asarray(yy);p=np.asarray(pp);q=np.asarray(pred)
  out[name]={'n':len(y),'markets':len(set(mm)),'ba':float(balanced_accuracy_score(y,q)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'posRecall':float(np.mean(q[y==1]==1)),'negRecall':float(np.mean(q[y==0]==0))}
 rep={'version':'R4_MANAGEMENT_TESTBED_V0_COMPLETION_CAPACITY_LOMO_V1','researchOnly':True,'developmentOnly':True,'rows':len(d),'markets':int(d.marketId.nunique()),'target':'futureWeakMakerFill5s>0','results':out,'interpretation':'True leave-one-market-out diagnostic only. Tests whether responsibility dynamics / frozen management belief state contains candidate-specific execution completion signal.'}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
