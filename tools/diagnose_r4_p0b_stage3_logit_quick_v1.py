from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, roc_auc_score, average_precision_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
D=pd.read_csv(P/'r4_p0b_stage3_strictpast_role_dataset_v1.csv')
side_mid=np.where(D.candidateSide.astype(str).eq('UP'),D.predictUpMidPublic,D.predictDownMidPublic)
D['candidatePxMinusSideMid']=D.candidatePx-side_mid
SETS={
 'ECON':['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic'],
 'ECON_LEDGER':['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic','reservedQty','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment','weak_active_owners','dominant_active_owners','weak_unresolved_shares','dominant_unresolved_shares'],
 'FULL_STRICT':['seconds_left','abs_gap','risk_deficit','coverage','floor_per_gross','candidateUpside','candidatePx','candidatePxMinusSideMid','spotMinusStrikeBpsPublic','reservedQty','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment','weak_active_owners','dominant_active_owners','weak_unresolved_shares','dominant_unresolved_shares','current_mode_age_s','events_5s','events_15s','transitions_15s','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
}

def m(seed): return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed))])
def bin_oof(d,fs,col,pos):
 pr=[]; pred=[]
 for i in range(len(d)):
  tr=np.arange(len(d))!=i; x=m(1000+i); x.fit(d.loc[tr,fs],d.loc[tr,col]); cls=list(x.classes_); p=float(x.predict_proba(d.loc[[i],fs])[0,cls.index(pos)]); pr.append(p); pred.append(pos if p>=.5 else [c for c in cls if c!=pos][0])
 y=d[col].astype(str).to_numpy(); yy=(y==pos).astype(int)
 return {'n':len(d),'BA':float(balanced_accuracy_score(y,pred)),'AUC':float(roc_auc_score(yy,pr)),'AP':float(average_precision_score(yy,pr)),'posRecall':float(np.mean(np.array(pred)[yy==1]==pos)),'negRecall':float(np.mean(np.array(pred)[yy==0]!=pos)),'probs':pr}
def flat(d,fs):
 pred=[]
 for i in range(len(d)):
  tr=np.arange(len(d))!=i; x=m(5000+i); x.fit(d.loc[tr,fs],d.loc[tr,'knownRole']); pred.append(str(x.predict(d.loc[[i],fs])[0]))
 y=d.knownRole.astype(str).to_numpy(); labs=sorted(set(y)); return {'BA':float(balanced_accuracy_score(y,pred)),'recall':{z:float(np.mean(np.array(pred)[y==z]==z)) for z in labs}}
def main():
 out={'version':'R4_P0B_STAGE3_LOGIT_QUICK_V1','rows':len(D),'roleCounts':D.knownRole.value_counts().to_dict(),'results':{}}
 real=D[D.gateB_label.astype(str).isin(['SUBSTITUTE','ADDITIVE'])].reset_index(drop=True)
 for n,fs in SETS.items():
  a=bin_oof(D,fs,'gateA_label','REALIZE'); b=bin_oof(real,fs,'gateB_label','SUBSTITUTE'); out['results'][n]={'gateA':{k:v for k,v in a.items() if k!='probs'},'gateB':{k:v for k,v in b.items() if k!='probs'},'flat3':flat(D,fs)}
 (P/'r4_p0b_stage3_logit_quick_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
