from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score,recall_score
ROOT=Path.cwd()
SRC=ROOT/'.lan_worker_v1/staging/r4_p0b_stage3_group_context_dataset_v1.csv'
OUT=ROOT/'.lan_worker_v1/results/r4_p0b_stage3_online_additive_router_cv_v1.json'
ECON=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','current_mode_age_s','events_5s','events_15s','transitions_15s','candidateReservedQty','candidateReservedRootCount','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment']
GROUP=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','parent_objective_age_s','same_side_oldest_objective_age_s']
TEACH=['pairBalanceProgress','m0EconomicProgress','transitionNonprogress']
SETS={'LOCAL_ECON_EXEC':ECON,'GROUP_CONTEXT':GROUP,'ECON_GROUP':ECON+GROUP,'FULL_SEMANTIC':ECON+GROUP+TEACH}
def model(kind,seed):
 if kind=='LOGIT': return make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=0.5,class_weight='balanced',solver='liblinear',max_iter=2000,random_state=seed))
 return make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=300,max_depth=4,min_samples_leaf=2,class_weight='balanced',random_state=seed,n_jobs=1))
def evalset(d,features,kind,seed):
 y=(d.knownRole=='PARALLEL_STATE_SHAPING').astype(int).to_numpy(); ps=[]; pred=[]
 for i in range(len(d)):
  tr=np.arange(len(d))!=i; m=model(kind,seed+i); m.fit(d.loc[tr,features],y[tr]); p=float(m.predict_proba(d.loc[[i],features])[0][list(m.classes_).index(1)]); ps.append(p); pred.append(int(p>=.5))
 return {'n':len(d),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'auc':float(roc_auc_score(y,ps)),'ap':float(average_precision_score(y,ps)),'stateShapingRecall':float(recall_score(y,pred,pos_label=1)),'rejectRecall':float(recall_score(y,pred,pos_label=0)),'predictedStateShaping':int(sum(pred))}
def main():
 d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan); d=d[d.knownRole.isin(['REJECT_NO_ACTION','PARALLEL_STATE_SHAPING'])].reset_index(drop=True)
 out={'version':'R4_P0B_STAGE3_ONLINE_ADDITIVE_ROUTER_CV_V1','researchOnly':True,'actionAuthority':False,'semanticContract':'PREPOSITION_REPAIR_SUBSTITUTE is excluded from online action target because frozen Target audit found no direct PREPOSITION support; it remains post-realization credit/accounting semantics. Online Stage3 candidate target is REJECT_NO_ACTION vs PARALLEL_STATE_SHAPING.','rows':len(d),'markets':int(d.marketId.nunique()),'roleCounts':d.knownRole.value_counts().to_dict(),'noThresholdSweep':True,'results':{}}
 for kind in ['LOGIT','EXTRATREES']:
  out['results'][kind]={}
  for name,fs in SETS.items(): out['results'][kind][name]=evalset(d,fs,kind,83001 if kind=='LOGIT' else 93001)
 OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
