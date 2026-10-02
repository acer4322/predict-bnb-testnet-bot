from __future__ import annotations
import json,sys,warnings,hashlib
from pathlib import Path
import joblib,pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import train_r2_add_intervention_adapter_v1 as base
from tools.train_r2_add_intervention_adapter_v1_1 import FEATURES,HIGH,metric
D=base.DATA_DIR; ART=D/'r2_add_intervention_adapter_v1_2_final.joblib'; REPORT=D/'r2_add_intervention_adapter_v1_2_final_report.json'
def main():
 warnings.filterwarnings('ignore',message='X does not have valid feature names')
 fill=joblib.load(base.FILL_ART); pending=joblib.load(base.PENDING_ART); split=json.loads(base.SPLIT.read_text(encoding='utf-8'))
 old=base.load(base.OLD,'old'); fresh=base.load(base.FRESH,'fresh'); d=pd.concat([old,fresh],ignore_index=True); d=d[d.teacherExecutionLabel.isin(HIGH)].copy().reset_index(drop=True)
 feats=pd.DataFrame([base.feature_row(r,fill,pending) for _,r in d.iterrows()]); d=pd.concat([d,feats],axis=1); d['label_keep']=d.teacherExecutionLabel.isin(base.KEEP_STRONG).astype(int)
 model=Pipeline([('imputer',SimpleImputer(strategy='median')),('scaler',StandardScaler()),('logistic',LogisticRegression(C=.5,max_iter=2000,class_weight=None,random_state=20260821))]); model.fit(d[FEATURES].apply(pd.to_numeric,errors='coerce'),d.label_keep.astype(int)); pr=model.predict_proba(d[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]
 contract={'features':FEATURES,'modelFamily':'LogisticRegression','C':0.5,'classWeight':None,'naturalThreshold':0.5,'optionWindowMs':5000,'teacherLabels':sorted(HIGH),'dreamFillAllowed':False,'runtimeTargetDataAllowed':False}; frozen_hash=hashlib.sha256(json.dumps(contract,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 artifact={'version':'R2_ADD_INTERVENTION_ADAPTER_V1_2_FINAL','features':FEATURES,'model':model,'classSemantics':{'1':'VETO_NEW_SAME_SIDE_ADD_FOR_CURRENT_OPTION','0':'ALLOW_FROZEN_R2_ADD'},'optionWindowMs':5000,'naturalThreshold':0.5,'fillArtifact':str(base.FILL_ART),'pendingArtifact':str(base.PENDING_ART),'dreamFillAllowed':False,'runtimeTargetDataAllowed':False,'frozenContract':contract,'frozenContractSha256':frozen_hash,'trainingMarkets':sorted(set(d.marketId.astype(int).tolist())),'sealedHoldoutMarkets':split['sealedHoldoutMarkets']}
 joblib.dump(artifact,ART)
 rep={'reportVersion':'R2_ADD_INTERVENTION_ADAPTER_V1_2_FINAL','researchOnly':True,'liveTradingChanges':False,'studentScale':'PRE_CAP100_ORIGINAL_R2','dataset':{'rows':int(len(d)),'markets':int(d.marketId.nunique()),'keepRows':int(d.label_keep.sum()),'releaseRows':int((1-d.label_keep).sum()),'labels':d.teacherExecutionLabel.value_counts().to_dict()},'fitMetrics':metric(d.label_keep.to_numpy(),pr),'frozenContract':contract,'frozenContractSha256':frozen_hash,'artifact':str(ART),'sealedHoldoutMarkets':split['sealedHoldoutMarkets'],'guards':['Architecture/hyperparameters frozen before sealed holdout.','No PnL threshold sweep.','No Target/future execution fields at runtime.','No CAP100.','All Student fills remain HftBacktest + Execution Tape V1 only.']}; REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'dataset':rep['dataset'],'fitMetrics':rep['fitMetrics'],'frozenContractSha256':frozen_hash},ensure_ascii=False,allow_nan=True))
if __name__=='__main__': main()
