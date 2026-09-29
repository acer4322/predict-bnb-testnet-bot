from __future__ import annotations
import json,math,joblib,sys
from pathlib import Path
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import r2_dynamic_lifecycle_supervisor_automl_v1 as s
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
OLD=BASE/'sequential_arbitration_option_v1.joblib';OUT=BASE/'r2_responsibility_handoff_expert_v1.joblib';REPORT=BASE/'r2_responsibility_handoff_expert_v1_report.json'
def fit(X,y):
 m=make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=500,min_samples_leaf=4,max_features=.8,n_jobs=-1,random_state=20260823,class_weight='balanced'));m.fit(X,y);return m
def main():
 rows,mids,sp=s.load();mids=sorted(mids);folds=[]
 for c,e in zip([60,85,105,125],[85,105,125,149]):
  rr,X,y=s.xy(rows,set(mids[:c]));mask=np.array([a in ['KEEP_EXECUTING','REPLACE_ROUTE'] for a in y]);yt=np.array([a=='REPLACE_ROUTE' for a in np.array(y)[mask]],int);model=fit(X[mask],yt)
  rr2,X2,y2=s.xy(rows,set(mids[c:e]));mask2=np.array([a in ['KEEP_EXECUTING','REPLACE_ROUTE'] for a in y2]);yv=np.array([a=='REPLACE_ROUTE' for a in np.array(y2)[mask2]],int);p=model.predict_proba(X2[mask2])[:,1];folds.append({'trainMarkets':c,'testMarkets':e-c,'n':len(yv),'replace':int(yv.sum()),'auc':roc_auc_score(yv,p) if len(set(yv))>1 else None})
 rr,X,y=s.xy(rows,set(mids));mask=np.array([a in ['KEEP_EXECUTING','REPLACE_ROUTE'] for a in y]);yt=np.array([a=='REPLACE_ROUTE' for a in np.array(y)[mask]],int);expert=fit(X[mask],yt)
 old=joblib.load(OLD);hybrid={'currentOnly':{'features':list(old['currentOnly']['features']),'models':dict(old['currentOnly']['models'])},'thresholds':dict(old['thresholds']),'version':'R2_RESPONSIBILITY_HANDOFF_EXPERT_V1_HYBRID'};hybrid['currentOnly']['models']['replace']=expert;joblib.dump(hybrid,OUT)
 rep={'version':'R2_RESPONSIBILITY_HANDOFF_EXPERT_V1','researchOnly':True,'role':'KEEP vs REPLACE only after existing lifecycle ACT authority','features':s.FEATURES,'rollingChronology':folds,'allFoldsAboveRandom':all((x['auc'] or 0)>.5 for x in folds),'runtimeArtifact':OUT.name,'actWaitAuthority':'unchanged sequential_arbitration_option_v1 act/wait heads','replaceThreshold':.5};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
