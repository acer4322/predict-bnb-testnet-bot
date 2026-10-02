from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score,recall_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
d=pd.read_csv(P/'r4_p0b_stage3_expanded48_dataset_v2.csv').replace([np.inf,-np.inf],np.nan)
cf=json.loads((P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.json').read_text(encoding='utf-8')); old={(int(r['marketId']),str(r['candidateKey'])) for r in cf.get('rows',[]) if r.get('knownRole') in {'REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'}}
d['cohort']=np.where([(int(m),str(k)) in old for m,k in zip(d.marketId,d.candidateKey)],'LANEF31','NEW17')
d['candidatePxMinusSideMid']=d.candidatePx-np.where(d.candidateSide.astype(str).eq('UP'),d.predictUpMidPublic,d.predictDownMidPublic); EPS=1e-9
d['y']=((d.target_add_floor_gain>=-EPS)&(d.target_add_absnet_gain>=-EPS)&((d.target_add_floor_gain>EPS)|(d.target_add_absnet_gain>EPS))).astype(int)
ECON=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic'];EXEC=['candidateReservedQty','candidateReservedRootCount'];GROUP=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','parent_objective_age_s','same_side_oldest_objective_age_s'];DYN=['current_mode_age_s','events_5s','events_15s','transitions_15s','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
SETS={'LOCAL_ECON_EXEC':ECON+EXEC,'GROUP_VALUE':ECON+EXEC+GROUP,'FULL_STRICT':ECON+EXEC+GROUP+DYN}
def model(kind,seed):
 if kind=='LOGIT':return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000,random_state=seed))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',ExtraTreesClassifier(n_estimators=500,max_depth=4,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=4,random_state=seed))])
def ev(tr,te,kind,fs,seed):
 m=model(kind,seed);m.fit(tr[fs],tr.y);p=m.predict_proba(te[fs])[:,list(m.classes_).index(1)];q=(p>=.5).astype(int);y=te.y.to_numpy(int)
 return {'trainN':len(tr),'testN':len(te),'trainRate':float(tr.y.mean()),'testRate':float(te.y.mean()),'balancedAccuracy':float(balanced_accuracy_score(y,q)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(np.unique(y))>1 else None,'positiveRecall':float(recall_score(y,q,pos_label=1)),'negativeRecall':float(recall_score(y,q,pos_label=0))}
def main():
 out={'version':'R4_P0B_STAGE3_ADDITIVE_ADMISSION_CROSS_COHORT_V1','researchOnly':True,'actionAuthority':False,'rows':len(d),'cohorts':{k:{'rows':len(g),'markets':int(g.marketId.nunique()),'positive':int(g.y.sum()),'rate':float(g.y.mean())} for k,g in d.groupby('cohort')},'fixedThreshold':.5,'results':{}}
 for direction,(a,b) in {'LANEF31_TO_NEW17':('LANEF31','NEW17'),'NEW17_TO_LANEF31':('NEW17','LANEF31')}.items():
  tr=d[d.cohort==a];te=d[d.cohort==b];out['results'][direction]={}
  for kind in ['LOGIT','EXTRATREES']:
   out['results'][direction][kind]={n:ev(tr,te,kind,fs,61000+i) for i,(n,fs) in enumerate(SETS.items())}
 q=P/'r4_p0b_stage3_additive_admission_cross_cohort_v1.json';q.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
