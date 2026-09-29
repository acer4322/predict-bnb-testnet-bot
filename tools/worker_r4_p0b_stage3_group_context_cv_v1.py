from __future__ import annotations
import json,os
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score
ROOT=Path.cwd();ST=ROOT/'.lan_worker_v1/staging';OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
d=pd.read_csv(ST/'r4_p0b_stage3_group_context_dataset_v1.csv')
d['candidatePxMinusSideMid']=d.candidatePx-np.where(d.candidateSide.astype(str).eq('UP'),d.predictUpMidPublic,d.predictDownMidPublic)
GROUP=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','parent_objective_age_s','same_side_oldest_objective_age_s']
ECON=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic']
LEDGER=['reservedQty','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment']
T=['pairBalanceProgress','m0EconomicProgress','transitionNonprogress']
SETS={'GROUP':GROUP,'GROUP_ECON':GROUP+ECON,'GROUP_ECON_LEDGER':GROUP+ECON+LEDGER,'GROUP_TEACHERS':GROUP+T}
def model(kind,seed):
 if kind=='LOGIT':return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',ExtraTreesClassifier(n_estimators=500,max_depth=3,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=4,random_state=seed))])
def oof(x,fs,col,pos,kind):
 probs=[];pred=[]
 for i in range(len(x)):
  tr=np.arange(len(x))!=i;m=model(kind,20000+i);m.fit(x.loc[tr,fs],x.loc[tr,col]);cls=list(m.classes_);p=float(m.predict_proba(x.loc[[i],fs])[0,cls.index(pos)]);probs.append(p);pred.append(pos if p>=.5 else [c for c in cls if c!=pos][0])
 y=x[col].astype(str).to_numpy();pr=np.array(pred);yy=(y==pos).astype(int)
 return {'n':int(len(x)),'BA':float(balanced_accuracy_score(y,pr)),'AUC':float(roc_auc_score(yy,probs)),'AP':float(average_precision_score(yy,probs)),'positiveRecall':float(np.mean(pr[yy==1]==pos)),'negativeRecall':float(np.mean(pr[yy==0]!=pos))}
def main():
 real=d[d.gateB_label.astype(str).isin(['SUBSTITUTE','ADDITIVE'])].reset_index(drop=True);out={'version':'R4_P0B_STAGE3_GROUP_CONTEXT_CV_V1','researchOnly':True,'actionAuthority':False,'rows':int(len(d)),'markets':int(d.marketId.nunique()),'noThresholdSweep':True,'results':{}}
 for k in ['LOGIT','EXTRATREES']:
  out['results'][k]={}
  for n,fs in SETS.items():out['results'][k][n]={'gateA':oof(d,fs,'gateA_label','REALIZE',k),'gateB':oof(real,fs,'gateB_label','SUBSTITUTE',k)}
 (OUT/'stage3_group_context_cv.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
