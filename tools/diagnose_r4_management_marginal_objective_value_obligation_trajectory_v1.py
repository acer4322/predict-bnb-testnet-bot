from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
PRE=P/'r4_management_marginal_objective_value_obligation_trajectory_v1_preregistered.json';OUT=P/'r4_management_marginal_objective_value_obligation_trajectory_v1_diagnostic.json'
CH=[P/'r4_management_marginal_objective_value_obligation_trajectory_chunk_0_13_v1.json',P/'r4_management_marginal_objective_value_obligation_trajectory_chunk_13_13_v1.json']

def model(kind,seed):
 if kind=='LOGIT':return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',ExtraTreesClassifier(n_estimators=500,max_depth=3,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=1,random_state=seed))])
def score(d,fs,kind):
 probs=np.full(len(d),np.nan);pred=np.empty(len(d),dtype=object);mids=list(dict.fromkeys(int(x) for x in d.marketId.tolist()))
 for i,mid in enumerate(mids):
  te=np.where(d.marketId.to_numpy()==mid)[0];tr=np.where(d.marketId.to_numpy()!=mid)[0];m=model(kind,41000+i);m.fit(d.iloc[tr][fs],d.iloc[tr].label);cls=list(m.classes_);p=m.predict_proba(d.iloc[te][fs])[:,cls.index('SUBSTITUTE')];probs[te]=p;pred[te]=np.where(p>=.5,'SUBSTITUTE','ADDITIVE')
 y=d.label.to_numpy();yy=(y=='SUBSTITUTE').astype(int)
 return {'n':len(d),'markets':int(d.marketId.nunique()),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'auc':float(roc_auc_score(yy,probs)),'ap':float(average_precision_score(yy,probs)),'substituteRecall':float(np.mean(pred[yy==1]=='SUBSTITUTE')),'additiveRecall':float(np.mean(pred[yy==0]=='ADDITIVE'))}
def main():
 pre=json.loads(PRE.read_text(encoding='utf-8'));rows=[]
 for f in CH:rows+=json.loads(f.read_text(encoding='utf-8'))['rows']
 d=pd.DataFrame([r for r in rows if 'error' not in r]);d['label']=np.where(d.knownRole.eq('PREPOSITION_REPAIR_SUBSTITUTE'),'SUBSTITUTE','ADDITIVE')
 dyn=list(pre['featureSets']['TRAJECTORY_ONLY']);variation={f:{'nonzero':int((pd.to_numeric(d[f],errors='coerce').fillna(0).abs()>1e-12).sum()),'unique':int(pd.to_numeric(d[f],errors='coerce').nunique(dropna=True)),'std':float(pd.to_numeric(d[f],errors='coerce').fillna(0).std())} for f in dyn}
 results={}
 for kind in ('LOGIT','EXTRATREES'):
  results[kind]={name:score(d,list(fs),kind) for name,fs in pre['featureSets'].items()}
 out={'version':'R4_MANAGEMENT_MARGINAL_OBJECTIVE_VALUE_OBLIGATION_TRAJECTORY_V1_DIAGNOSTIC','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'contractStatus':pre['status'],'rows':len(d),'markets':int(d.marketId.nunique()),'roleCounts':d.knownRole.value_counts().to_dict(),'trajectoryVariation':variation,'results':results,'interpretationBoundary':'All trajectory X reconstructed from Objective Ledger events strictly before candidate open. Development-consumed cohort only; no promotion.','guards':pre['guards']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
