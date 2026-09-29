from pathlib import Path
import json, numpy as np, pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, recall_score
P=Path('data/research/r4_v0/p0_provenance_v1/r4_p0b_stage3_expanded48_dataset_v2.csv')
O=Path('data/research/r4_v0/p0_provenance_v1/r4_p0b_stage3_additive_admission_v0_seed_robustness_quick_v1.json')
d=pd.read_csv(P).replace([np.inf,-np.inf],np.nan)
d['candidatePxMinusSideMid']=d.candidatePx-np.where(d.candidateSide.astype(str).eq('UP'),d.predictUpMidPublic,d.predictDownMidPublic)
F=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic','candidateReservedQty','candidateReservedRootCount']
e=1e-9; y=((d.target_add_floor_gain>=-e)&(d.target_add_absnet_gain>=-e)&((d.target_add_floor_gain>e)|(d.target_add_absnet_gain>e))).astype(int).to_numpy()
m=d.marketId.astype(int).to_numpy(); u=list(dict.fromkeys(m.tolist())); seeds=[7,101,2027,51000]
out={'version':'R4_P0B_STAGE3_ADDITIVE_ADMISSION_V0_SEED_ROBUSTNESS_QUICK_V1','rows':len(d),'markets':len(u),'featureSet':'LOCAL_ECON_EXEC_V0','results':{}}
for kind in ['LOGIT','EXTRATREES']:
 rr=[]
 for s in seeds:
  p=np.zeros(len(d))
  for j,mid in enumerate(u):
   te=np.where(m==mid)[0]; tr=np.where(m!=mid)[0]
   if kind=='LOGIT': mdl=Pipeline([('i',SimpleImputer(strategy='median')),('s',StandardScaler()),('c',LogisticRegression(C=.5,class_weight='balanced',solver='liblinear',max_iter=2000,random_state=s+j))])
   else: mdl=Pipeline([('i',SimpleImputer(strategy='median')),('c',ExtraTreesClassifier(n_estimators=120,max_depth=4,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=2,random_state=s+j))])
   mdl.fit(d.loc[tr,F],y[tr]); p[te]=mdl.predict_proba(d.loc[te,F])[:,1]
  q=(p>=.5).astype(int); rr.append({'seed':s,'ba':float(balanced_accuracy_score(y,q)),'auc':float(roc_auc_score(y,p)),'pos':float(recall_score(y,q,pos_label=1)),'neg':float(recall_score(y,q,pos_label=0))})
 out['results'][kind]={'runs':rr,'baMean':float(np.mean([x['ba'] for x in rr])),'baMin':float(np.min([x['ba'] for x in rr])),'aucMean':float(np.mean([x['auc'] for x in rr])),'aucMin':float(np.min([x['auc'] for x in rr]))}
O.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
