from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score

ROOT=Path.cwd(); P=Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.')); D=pd.read_csv(ROOT/'.lan_worker_v1/staging/r4_p0b_stage3_teacher_augmented_role_dataset_v1.csv')
D['candidatePxMinusSideMid']=D.candidatePx-np.where(D.candidateSide.astype(str).eq('UP'),D.predictUpMidPublic,D.predictDownMidPublic)
ECON=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic']
LEDGER=['reservedQty','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment','weak_active_owners','dominant_active_owners','weak_unresolved_shares','dominant_unresolved_shares']
DYN=['current_mode_age_s','events_5s','events_15s','transitions_15s','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
T=['pairBalanceProgress','m0EconomicProgress','transitionNonprogress']
SETS={'TEACHERS':T,'ECON_TEACHERS':ECON+T,'ECON_LEDGER_TEACHERS':ECON+LEDGER+T,'FULL_STRICT_TEACHERS':ECON+LEDGER+DYN+T}

def model(kind,seed):
 if kind=='LOGIT': return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',ExtraTreesClassifier(n_estimators=400,max_depth=3,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=4,random_state=seed))])

def oof(d,fs,col,pos,kind):
 probs=[]; preds=[]
 for i in range(len(d)):
  tr=np.arange(len(d))!=i; m=model(kind,11000+i); m.fit(d.loc[tr,fs],d.loc[tr,col]); cls=list(m.classes_); p=float(m.predict_proba(d.loc[[i],fs])[0,cls.index(pos)]); probs.append(p); preds.append(pos if p>=.5 else [x for x in cls if x!=pos][0])
 y=d[col].astype(str).to_numpy(); pred=np.array(preds); yy=(y==pos).astype(int)
 return {'n':int(len(d)),'BA':float(balanced_accuracy_score(y,pred)),'AUC':float(roc_auc_score(yy,probs)),'AP':float(average_precision_score(yy,probs)),'positiveRecall':float(np.mean(pred[yy==1]==pos)),'negativeRecall':float(np.mean(pred[yy==0]!=pos))}

def main():
 real=D[D.gateB_label.astype(str).isin(['SUBSTITUTE','ADDITIVE'])].reset_index(drop=True)
 out={'version':'R4_P0B_STAGE3_TEACHER_AUGMENTED_CV_V1','researchOnly':True,'actionAuthority':False,'rows':int(len(D)),'markets':int(D.marketId.nunique()),'noThresholdSweep':True,'results':{}}
 for kind in ['LOGIT','EXTRATREES']:
  out['results'][kind]={}
  for name,fs in SETS.items(): out['results'][kind][name]={'gateA':oof(D,fs,'gateA_label','REALIZE',kind),'gateB':oof(real,fs,'gateB_label','SUBSTITUTE',kind)}
 (P/'r4_p0b_stage3_teacher_augmented_cv_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
 print(json.dumps(out,indent=2))
if __name__=='__main__': main()

