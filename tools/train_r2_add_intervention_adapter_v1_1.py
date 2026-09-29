from __future__ import annotations
import json, warnings, sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score, confusion_matrix
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import train_r2_add_intervention_adapter_v1 as base
D=base.DATA_DIR
ART=D/'r2_add_intervention_adapter_v1_1.joblib'
REPORT=D/'r2_add_intervention_adapter_v1_1_report.json'
HIGH=base.KEEP_STRONG|base.RELEASE
FEATURES=[
 'p_fill_1s','p_fill_3s','p_fill_5s','p_continue_teacher_v0',
 'order_age_ms','remaining_ratio','partial_fill_ratio','active_same_count','active_opp_count',
 'quote_offset_ticks','current_spread_ticks','public_depletion_ratio',
 'maker_abs_net','maker_paired_coverage','combined_abs_net','combined_paired_coverage','worst_case_floor',
 'reason_is_hazard','reason_is_burst','side_is_up'
]
def metric(y,p):
 y=np.asarray(y,dtype=int); p=np.clip(np.asarray(p,dtype=float),1e-7,1-1e-7); pred=(p>=.5).astype(int); cm=confusion_matrix(y,pred,labels=[0,1]); tn,fp,fn,tp=[int(x) for x in cm.ravel()]
 return {'n':int(len(y)),'keep':int(y.sum()),'keepRate':float(y.mean()),'predMean':float(p.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])),'balancedAccuracy0p5':float(balanced_accuracy_score(y,pred)),'confusion0p5':{'tnRelease':tn,'fpKeep':fp,'fnRelease':fn,'tpKeep':tp}}
def main():
 warnings.filterwarnings('ignore',message='X does not have valid feature names')
 fill=joblib.load(base.FILL_ART); pending=joblib.load(base.PENDING_ART); split=json.loads(base.SPLIT.read_text(encoding='utf-8'))
 ids=[int(x) for x in split['developmentMarkets']]; train_ids=set(ids[:20]); val_ids=set(ids[20:])
 old=base.load(base.OLD,'old'); fresh=base.load(base.FRESH,'fresh'); d=pd.concat([old,fresh],ignore_index=True); d=d[d.teacherExecutionLabel.isin(HIGH)].copy().reset_index(drop=True)
 feats=pd.DataFrame([base.feature_row(r,fill,pending) for _,r in d.iterrows()]); d=pd.concat([d,feats],axis=1); d['label_keep']=d.teacherExecutionLabel.isin(base.KEEP_STRONG).astype(int)
 tr=d[(d.source=='old')|((d.source=='fresh')&d.marketId.astype(int).isin(train_ids))].copy(); va=d[(d.source=='fresh')&d.marketId.astype(int).isin(val_ids)].copy()
 model=Pipeline([('imputer',SimpleImputer(strategy='median')),('scaler',StandardScaler()),('logistic',LogisticRegression(C=.5,max_iter=2000,class_weight=None,random_state=20260821))])
 model.fit(tr[FEATURES].apply(pd.to_numeric,errors='coerce'),tr.label_keep.astype(int))
 def ev(x): return metric(x.label_keep.astype(int).to_numpy(),model.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1])
 artifact={'version':'R2_ADD_INTERVENTION_ADAPTER_V1_1','features':FEATURES,'model':model,'classSemantics':{'1':'VETO_NEW_SAME_SIDE_ADD_FOR_CURRENT_OPTION','0':'ALLOW_FROZEN_R2_ADD'},'optionWindowMs':5000,'fillArtifact':str(base.FILL_ART),'pendingArtifact':str(base.PENDING_ART),'dreamFillAllowed':False,'runtimeTargetDataAllowed':False,'trainingSemantics':'High-confidence BEFORE_ADD only: Target-supported KEEP vs Target refresh + HFT no-fill RELEASE. Natural threshold 0.5.'}
 joblib.dump(artifact,ART)
 rep={'reportVersion':'R2_ADD_INTERVENTION_ADAPTER_V1_1','researchOnly':True,'liveTradingChanges':False,'studentScale':'PRE_CAP100_ORIGINAL_R2','dataset':{'rows':int(len(d)),'trainRows':int(len(tr)),'validationRows':int(len(va)),'labels':d.teacherExecutionLabel.value_counts().to_dict()},'split':{'freshTrainMarkets':sorted(train_ids),'freshValidationMarkets':sorted(val_ids),'sealedHoldoutMarketsNeverRead':split['sealedHoldoutMarkets']},'metrics':{'train':ev(tr),'freshValidation':ev(va)},'features':FEATURES,'artifact':str(ART),'guards':['No weak HFT-only labels in V1.1 fit.','No Target/future fields at runtime.','No PnL model selection or threshold sweep.','Natural 0.5 boundary only.','SEALED holdout remains untouched.']}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'dataset':rep['dataset'],'metrics':rep['metrics']},ensure_ascii=False,allow_nan=True))
if __name__=='__main__': main()
