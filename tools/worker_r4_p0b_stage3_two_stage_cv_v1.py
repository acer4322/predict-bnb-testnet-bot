from __future__ import annotations
import json, os
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
DATA=ST/'r4_p0b_stage3_strictpast_role_dataset_v1.csv'

ECON=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic']
LEDGER=['reservedQty','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment','weak_active_owners','dominant_active_owners','weak_unresolved_shares','dominant_unresolved_shares']
DYNAMIC=['current_mode_age_s','events_5s','events_15s','transitions_15s','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
FEATURE_SETS={'ECON':ECON,'ECON_LEDGER':ECON+LEDGER,'FULL_STRICT':ECON+LEDGER+DYNAMIC}

ROLE_MAP={'REJECT_NO_ACTION':'REJECT_NO_ACTION','PREPOSITION_REPAIR_SUBSTITUTE':'PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING':'PARALLEL_STATE_SHAPING'}

def prepare():
 d=pd.read_csv(DATA)
 side_mid=np.where(d['candidateSide'].astype(str).eq('UP'),d['predictUpMidPublic'],d['predictDownMidPublic'])
 d['candidatePxMinusSideMid']=d['candidatePx']-side_mid
 return d

def model(kind,seed):
 if kind=='LOGIT':
  return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',ExtraTreesClassifier(n_estimators=800,max_depth=3,min_samples_leaf=2,max_features=.75,class_weight='balanced',random_state=seed,n_jobs=4))])

def oof_binary(d,features,label_col,pos_label,kind,seed):
 probs=np.full(len(d),np.nan); preds=np.empty(len(d),dtype=object)
 for i in range(len(d)):
  tr=np.arange(len(d))!=i
  m=model(kind,seed+i); m.fit(d.loc[tr,features],d.loc[tr,label_col])
  cls=list(m.classes_); probs[i]=m.predict_proba(d.loc[[i],features])[0,cls.index(pos_label)]; preds[i]=pos_label if probs[i]>=.5 else [c for c in cls if c!=pos_label][0]
 y=d[label_col].astype(str).to_numpy(); yb=(y==pos_label).astype(int)
 return {'n':int(len(d)),'balancedAccuracy':float(balanced_accuracy_score(y,preds)),'auc':float(roc_auc_score(yb,probs)),'ap':float(average_precision_score(yb,probs)),'positiveRecall':float(np.mean(preds[yb==1]==pos_label)),'negativeRecall':float(np.mean(preds[yb==0]!=pos_label)),'probs':probs.tolist(),'preds':preds.tolist()}

def oof_flat(d,features,kind,seed):
 probs=[]; preds=[]
 labels=sorted(d.knownRole.unique())
 for i in range(len(d)):
  tr=np.arange(len(d))!=i; m=model(kind,seed+i); m.fit(d.loc[tr,features],d.loc[tr,'knownRole']); cls=list(m.classes_); pr=m.predict_proba(d.loc[[i],features])[0]; probs.append({c:float(pr[cls.index(c)]) if c in cls else 0. for c in labels}); preds.append(str(m.predict(d.loc[[i],features])[0]))
 y=d.knownRole.astype(str).to_numpy(); ba=float(balanced_accuracy_score(y,np.array(preds)))
 cm=confusion_matrix(y,preds,labels=labels).tolist()
 recalls={lab:float(np.mean(np.array(preds)[y==lab]==lab)) for lab in labels}
 return {'n':int(len(d)),'balancedAccuracy':ba,'classRecall':recalls,'labels':labels,'confusion':cm,'preds':preds,'probs':probs}

def two_stage(d,features,kind,seed):
 a=oof_binary(d,features,'gateA_label','REALIZE',kind,seed)
 real=d[d.gateB_label.astype(str).isin(['SUBSTITUTE','ADDITIVE'])].copy().reset_index(drop=True)
 b=oof_binary(real,features,'gateB_label','SUBSTITUTE',kind,seed+10000)
 # For three-class OOF, Gate A is OOF on all rows. Gate B probabilities are trained leave-one-out for realize rows,
 # and for REJECT rows a Gate-B model is trained on all realize examples because Gate-B is downstream and does not use their label.
 gateb_prob_by_idx={int(real.iloc[j].name):None for j in range(len(real))}
 # rebuild realize row mapping from market/candidate keys
 bmap={(int(real.iloc[j].marketId),str(real.iloc[j].candidateKey)):float(b['probs'][j]) for j in range(len(real))}
 all_b=[]
 for i,r in d.iterrows():
  key=(int(r.marketId),str(r.candidateKey))
  if key in bmap: all_b.append(bmap[key])
  else:
   m=model(kind,seed+20000+i); m.fit(real[features],real.gateB_label); cls=list(m.classes_); all_b.append(float(m.predict_proba(d.loc[[i],features])[0,cls.index('SUBSTITUTE')]))
 pred3=[]
 for pa,pb in zip(a['probs'],all_b):
  if pa<.5: pred3.append('REJECT_NO_ACTION')
  elif pb>=.5: pred3.append('PREPOSITION_REPAIR_SUBSTITUTE')
  else: pred3.append('PARALLEL_STATE_SHAPING')
 y=d.knownRole.astype(str).to_numpy(); pred3=np.array(pred3); recalls={lab:float(np.mean(pred3[y==lab]==lab)) for lab in sorted(set(y))}
 return {'gateA':{k:v for k,v in a.items() if k not in ('probs','preds')},'gateB':{k:v for k,v in b.items() if k not in ('probs','preds')},'threeClassBalancedAccuracy':float(balanced_accuracy_score(y,pred3)),'threeClassRecall':recalls,'threeClassPreds':pred3.tolist()}

def main():
 d=prepare(); out={'version':'R4_P0B_STAGE3_TWO_STAGE_CV_V1','researchOnly':True,'actionAuthority':False,'rows':int(len(d)),'markets':int(d.marketId.nunique()),'roleCounts':d.knownRole.value_counts().to_dict(),'evaluation':'Leave-one-market-out equals leave-one-row-out because all 31 labeled candidates are from distinct markets. Fixed feature groups only; no threshold sweep.','results':{}}
 for kind in ['LOGIT','EXTRATREES']:
  out['results'][kind]={}
  for fs,features in FEATURE_SETS.items():
   out['results'][kind][fs]={'twoStage':two_stage(d,features,kind,81001),'flat3':oof_flat(d,features,kind,91001)}
 (OUT/'stage3_two_stage_cv.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()

