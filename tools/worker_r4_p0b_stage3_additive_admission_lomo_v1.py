from __future__ import annotations
import json,os
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score,recall_score
ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
d=pd.read_csv(ST/'r4_p0b_stage3_expanded48_dataset_v2.csv').replace([np.inf,-np.inf],np.nan)
d['candidatePxMinusSideMid']=d.candidatePx-np.where(d.candidateSide.astype(str).eq('UP'),d.predictUpMidPublic,d.predictDownMidPublic)
EPS=1e-9
d['y']=((d.target_add_floor_gain>=-EPS)&(d.target_add_absnet_gain>=-EPS)&((d.target_add_floor_gain>EPS)|(d.target_add_absnet_gain>EPS))).astype(int)
ECON=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic']
EXEC=['candidateReservedQty','candidateReservedRootCount']
GROUP=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','parent_objective_age_s','same_side_oldest_objective_age_s']
DYN=['current_mode_age_s','events_5s','events_15s','transitions_15s','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
SETS={'LOCAL_ECON_EXEC':ECON+EXEC,'GROUP_VALUE':ECON+EXEC+GROUP,'FULL_STRICT':ECON+EXEC+GROUP+DYN}
def model(kind,seed):
 if kind=='LOGIT':return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000,random_state=seed))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',ExtraTreesClassifier(n_estimators=500,max_depth=4,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=4,random_state=seed))])
def ev(kind,fs):
 y=d.y.to_numpy(int); p=np.zeros(len(d)); mids=d.marketId.astype(int).to_numpy(); uniq=list(dict.fromkeys(mids.tolist()))
 for j,mid in enumerate(uniq):
  te=np.where(mids==mid)[0];tr=np.where(mids!=mid)[0];m=model(kind,51000+j);m.fit(d.loc[tr,fs],y[tr]);p[te]=m.predict_proba(d.loc[te,fs])[:,list(m.classes_).index(1)]
 q=(p>=.5).astype(int)
 return {'n':len(d),'balancedAccuracy':float(balanced_accuracy_score(y,q)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'positiveRecall':float(recall_score(y,q,pos_label=1)),'negativeRecall':float(recall_score(y,q,pos_label=0)),'predictedPositive':int(q.sum())}
def main():
 out={'version':'R4_P0B_STAGE3_ADDITIVE_ADMISSION_LOMO_V1','researchOnly':True,'actionAuthority':False,'rows':len(d),'markets':int(d.marketId.nunique()),'target':'ADDITIVE_PARETO_DOMINATES_RESERVATION','positive':int(d.y.sum()),'negative':int((1-d.y).sum()),'roleCross':pd.crosstab(d.knownRole,d.y).to_dict(),'evaluation':'True leave-one-market-out; fixed 0.5 threshold; no threshold/utility sweep. Future branch economics create teacher label only.','results':{}}
 for k in ['LOGIT','EXTRATREES']:
  out['results'][k]={}
  for n,fs in SETS.items():out['results'][k][n]=ev(k,fs)
 (OUT/'stage3_additive_admission_lomo_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
