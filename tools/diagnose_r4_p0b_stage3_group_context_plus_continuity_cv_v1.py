from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
d=pd.read_csv(P/'r4_p0b_stage3_group_context_dataset_v1.csv');oc=json.loads((P/'r4_p0b_stage3_ownership_continuity_role_context_v1.json').read_text(encoding='utf-8'));m={(int(r['marketId']),str(r['candidateKey'])):r for r in oc['rowsDetail']};d['pExistingWeak5s']=[m[(int(r.marketId),str(r.candidateKey))]['pExistingWeak5s'] for _,r in d.iterrows()];d['pExistingDom5s']=[m[(int(r.marketId),str(r.candidateKey))]['pExistingDom5s'] for _,r in d.iterrows()];d['candidatePxMinusSideMid']=d.candidatePx-np.where(d.candidateSide.astype(str).eq('UP'),d.predictUpMidPublic,d.predictDownMidPublic)
GROUP=['same_side_active_objectives','opposite_side_active_objectives','same_side_total_residual','opposite_side_total_residual','recent_objective_opens_5s','recent_objective_opens_15s','parent_objective_age_s','same_side_oldest_objective_age_s'];ECON=['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic'];LEDGER=['reservedQty','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment'];OC=['pExistingWeak5s','pExistingDom5s'];SETS={'GROUP_ECON_LEDGER':GROUP+ECON+LEDGER,'PLUS_OC':GROUP+ECON+LEDGER+OC,'OC_ONLY':OC}
def model(k,s):
 if k=='LOGIT':return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=s))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',ExtraTreesClassifier(n_estimators=500,max_depth=3,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=1,random_state=s))])
def oof(x,fs,col,pos,k):
 probs=[];pred=[]
 for i in range(len(x)):
  tr=np.arange(len(x))!=i;mm=model(k,26000+i);mm.fit(x.loc[tr,fs],x.loc[tr,col]);cls=list(mm.classes_);p=float(mm.predict_proba(x.loc[[i],fs])[0,cls.index(pos)]);probs.append(p);pred.append(pos if p>=.5 else [c for c in cls if c!=pos][0])
 y=x[col].astype(str).to_numpy();pr=np.asarray(pred);yy=(y==pos).astype(int);return {'n':int(len(x)),'BA':float(balanced_accuracy_score(y,pr)),'AUC':float(roc_auc_score(yy,probs)),'AP':float(average_precision_score(yy,probs)),'positiveRecall':float(np.mean(pr[yy==1]==pos)),'negativeRecall':float(np.mean(pr[yy==0]!=pos))}
def main():
 real=d[d.gateB_label.astype(str).isin(['SUBSTITUTE','ADDITIVE'])].reset_index(drop=True);out={'version':'R4_P0B_STAGE3_GROUP_CONTEXT_PLUS_OWNERSHIP_CONTINUITY_CV_V1','researchOnly':True,'promotionEvidence':False,'rows':len(d),'results':{}}
 for k in ['LOGIT','EXTRATREES']:
  out['results'][k]={}
  for n,fs in SETS.items():out['results'][k][n]={'gateA':oof(d,fs,'gateA_label','REALIZE',k),'gateB':oof(real,fs,'gateB_label','SUBSTITUTE',k)}
 out['interpretation']='Consumed Lane-F development CV only. Tests whether formally validated Ownership Continuity adds role-routing information. No threshold tuning or promotion.';(P/'r4_p0b_stage3_group_context_plus_ownership_continuity_cv_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
