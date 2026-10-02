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
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1';PRE=P/'r4_stage3_parent_carrier_backing_v1_preregistered.json';OUT=P/'r4_stage3_parent_carrier_backing_v1_diagnostic.json'
TR=[P/'r4_management_marginal_objective_value_obligation_trajectory_chunk_0_13_v1.json',P/'r4_management_marginal_objective_value_obligation_trajectory_chunk_13_13_v1.json'];BK=[P/'r4_stage3_parent_carrier_backing_chunk_0_13_v1.json',P/'r4_stage3_parent_carrier_backing_chunk_13_13_v1.json']
def mk(kind,seed):
 if kind=='LOGIT':return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(C=.5,max_iter=3000,class_weight='balanced',solver='lbfgs',random_state=seed))])
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',ExtraTreesClassifier(n_estimators=500,max_depth=3,min_samples_leaf=2,max_features=.75,class_weight='balanced',n_jobs=1,random_state=seed))])
def lomo(d,fs,kind):
 p=np.full(len(d),np.nan);pr=np.zeros(len(d),int);mids=list(dict.fromkeys(int(x) for x in d.marketId.tolist()));y=(d.label.to_numpy()=='SUBSTITUTE').astype(int)
 for i,mid in enumerate(mids):
  te=np.where(d.marketId.to_numpy()==mid)[0];tr=np.where(d.marketId.to_numpy()!=mid)[0];m=mk(kind,97000+i);m.fit(d.iloc[tr][fs],d.iloc[tr].label);cls=list(m.classes_);pp=m.predict_proba(d.iloc[te][fs])[:,cls.index('SUBSTITUTE')];p[te]=pp;pr[te]=(pp>=.5).astype(int)
 return {'balancedAccuracy':float(balanced_accuracy_score(y,pr)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'substituteRecall':float(np.mean(pr[y==1]==1)),'additiveRecall':float(np.mean(pr[y==0]==0))}
def main():
 pre=json.loads(PRE.read_text());tr=[];bk=[]
 for f in TR:tr+=json.loads(f.read_text())['rows']
 for f in BK:bk+=json.loads(f.read_text())['rows']
 a=pd.DataFrame([r for r in tr if 'error' not in r]);b=pd.DataFrame([r for r in bk if 'error' not in r]);d=a.merge(b[['marketId','candidateKey']+pre['carrierBackingFeatures']],on=['marketId','candidateKey'],how='inner',validate='one_to_one');d['label']=np.where(d.knownRole.eq('PREPOSITION_REPAIR_SUBSTITUTE'),'SUBSTITUTE','ADDITIVE');base=list(pre['baselineFeatures']);aug=base+list(pre['carrierBackingFeatures']);res={}
 for kind in ('LOGIT','EXTRATREES'):
  x=lomo(d,base,kind);y=lomo(d,aug,kind);res[kind]={'BASELINE':x,'PLUS_CARRIER_BACKING':y,'delta':{'balancedAccuracy':y['balancedAccuracy']-x['balancedAccuracy'],'auc':y['auc']-x['auc'],'ap':y['ap']-x['ap']}}
 anatomy={}
 for role,g in b.groupby('knownRole'):
  anatomy[str(role)]={'n':int(len(g)),**{f:float(pd.to_numeric(g[f],errors='coerce').mean()) for f in pre['carrierBackingFeatures']}}
 out={'version':'R4_STAGE3_PARENT_CARRIER_BACKING_V1_DIAGNOSTIC','researchOnly':True,'actionAuthority':False,'promotionEvidence':False,'contractStatus':pre['status'],'rows':int(len(d)),'markets':int(d.marketId.nunique()),'results':res,'anatomy':anatomy,'interpretationBoundary':'Consumed development only; strict-past parent carrier backing. No policy authority or threshold rescue.','guards':pre['guards']};OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
