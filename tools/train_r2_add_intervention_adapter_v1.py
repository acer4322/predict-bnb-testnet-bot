from __future__ import annotations
import json, math, warnings, sys
from pathlib import Path
from typing import Any
import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score, confusion_matrix
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
DATA_DIR=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
OLD=DATA_DIR/'r2_execution_school_teacher_v0_rows.csv'
FRESH=DATA_DIR/'r2_fresh_intervention_teacher_v1_rows.csv'
SPLIT=DATA_DIR/'r2_pending_management_fresh_split_v1.json'
FILL_ART=DATA_DIR/'open_order_fill_lifecycle_v0.joblib'
PENDING_ART=DATA_DIR/'execution_aware_pending_management_v0.joblib'
ART=DATA_DIR/'r2_add_intervention_adapter_v1.joblib'
REPORT=DATA_DIR/'r2_add_intervention_adapter_v1_report.json'
from tools.hftbacktest_r2_execution_school_v0 import _pending_skill_frame

KEEP_STRONG={'DUPLICATE_PENDING_INTENT_STRONG','DUPLICATE_PENDING_INTENT_TARGET_SUPPORTED'}
KEEP_WEAK={'DUPLICATE_PENDING_INTENT_HFT'}
RELEASE={'RELEASE_FOR_REDECISION_PROXY'}
FEATURES=[
 'p_fill_1s','p_fill_3s','p_fill_5s','p_continue_teacher_v0',
 'order_age_ms','remaining_ratio','partial_fill_ratio','active_same_count','active_opp_count',
 'quote_offset_ticks','current_spread_ticks','public_depletion_ratio',
 'maker_abs_net','maker_paired_coverage','combined_abs_net','combined_paired_coverage','worst_case_floor',
 'reason_is_hazard','reason_is_burst','side_is_up'
]

def parse_portfolio(row:pd.Series)->dict[str,Any]:
    raw=row.get('portfolio_json')
    if isinstance(raw,str) and raw:
        try:return json.loads(raw)
        except Exception:return {}
    return {}

def canonical_row(row:pd.Series)->dict[str,Any]:
    d=row.to_dict(); d['portfolio']=parse_portfolio(row); return d

def feature_row(row:pd.Series, fill_art:dict[str,Any], pending_art:dict[str,Any])->dict[str,float]:
    d=canonical_row(row)
    ffeatures=list(fill_art['features'])
    xf=_pending_skill_frame(d,ffeatures)
    models=fill_art['models']
    p1=float(models['fill_1s'].predict_proba(xf)[0,1]); p3=float(models['fill_3s'].predict_proba(xf)[0,1]); p5=float(models['fill_5s'].predict_proba(xf)[0,1])
    pfeatures=list(pending_art['features']); xp=_pending_skill_frame(d,pfeatures)
    pp=float(pending_art['model'].predict_proba(xp)[0,1])
    port=d.get('portfolio') or {}
    cum=float(d.get('cumExecQty') or 0.0); rem=float(d.get('remainingQty') or 0.0); tot=cum+rem
    init=float(d.get('initialDepth') or 0.0); dep=float(d.get('publicCumDepletion') or 0.0)
    ctx=str(d.get('context') or '')
    reason=ctx.split(':',2)[2] if ctx.count(':')>=2 else ''
    def fv(x):
        try:
            x=float(x); return x if math.isfinite(x) else math.nan
        except Exception:return math.nan
    return {
      'p_fill_1s':p1,'p_fill_3s':p3,'p_fill_5s':p5,'p_continue_teacher_v0':pp,
      'order_age_ms':fv(d.get('orderAgeMs')),'remaining_ratio':rem/tot if tot>1e-9 else math.nan,
      'partial_fill_ratio':fv(d.get('partialFillRatio')),'active_same_count':fv(d.get('activeSameCount')),
      'active_opp_count':fv(d.get('activeOppCount')),'quote_offset_ticks':fv(d.get('quoteOffsetTicks')),
      'current_spread_ticks':fv(d.get('currentSpreadTicks')),'public_depletion_ratio':dep/init if init>1e-9 else math.nan,
      'maker_abs_net':fv(port.get('maker_abs_net')),'maker_paired_coverage':fv(port.get('maker_paired_coverage')),
      'combined_abs_net':fv(port.get('combined_abs_net')),'combined_paired_coverage':fv(port.get('combined_paired_coverage')),
      'worst_case_floor':fv(port.get('worst_case_floor')),'reason_is_hazard':float(reason=='MAKER_HAZARD'),
      'reason_is_burst':float(reason=='MAKER_BURST'),'side_is_up':float(str(d.get('side') or '').upper()=='UP')
    }

def metrics(y,p):
    y=np.asarray(y,dtype=int); p=np.clip(np.asarray(p,dtype=float),1e-7,1-1e-7); pred=(p>=.5).astype(int)
    cm=confusion_matrix(y,pred,labels=[0,1]); tn,fp,fn,tp=[int(x) for x in cm.ravel()]
    return {'n':int(len(y)),'keep':int(y.sum()),'keepRate':float(y.mean()) if len(y) else None,
      'predMean':float(p.mean()) if len(p) else None,'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,
      'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,
      'balancedAccuracy0p5':float(balanced_accuracy_score(y,pred)) if len(y) else None,
      'confusion0p5':{'tnRelease':tn,'fpKeep':fp,'fnRelease':fn,'tpKeep':tp}}

def load(path,source):
    d=pd.read_csv(path,low_memory=False); d=d[d.context.fillna('').str.startswith('BEFORE_ADD:')].copy(); d['source']=source
    d=d[d.teacherExecutionLabel.isin(KEEP_STRONG|KEEP_WEAK|RELEASE)].copy(); return d

def main():
    warnings.filterwarnings('ignore',message='X does not have valid feature names')
    fill=joblib.load(FILL_ART); pending=joblib.load(PENDING_ART); split=json.loads(SPLIT.read_text(encoding='utf-8'))
    fresh_ids=[int(x) for x in split['developmentMarkets']]; fresh_train=set(fresh_ids[:20]); fresh_val=set(fresh_ids[20:])
    old=load(OLD,'old'); fresh=load(FRESH,'fresh'); d=pd.concat([old,fresh],ignore_index=True)
    feats=pd.DataFrame([feature_row(r,fill,pending) for _,r in d.iterrows()]); d=pd.concat([d.reset_index(drop=True),feats],axis=1)
    d['label_keep']=d.teacherExecutionLabel.isin(KEEP_STRONG|KEEP_WEAK).astype(int)
    d['teacher_weight']=np.where(d.teacherExecutionLabel.isin(KEEP_WEAK),0.25,1.0)
    # Train on all historical rows plus first 20 fresh DEV markets. Last 10 fresh DEV markets are validation only.
    tr=d[(d.source=='old')|((d.source=='fresh')&d.marketId.astype(int).isin(fresh_train))].copy()
    va=d[(d.source=='fresh')&d.marketId.astype(int).isin(fresh_val)&d.teacherExecutionLabel.isin(KEEP_STRONG|RELEASE)].copy()
    # High-confidence diagnostic on training data excludes weak HFT-only labels.
    tr_hi=tr[tr.teacherExecutionLabel.isin(KEEP_STRONG|RELEASE)].copy()
    y=tr.label_keep.astype(int).to_numpy(); basew=tr.teacher_weight.astype(float).to_numpy()
    mass0=basew[y==0].sum(); mass1=basew[y==1].sum(); cw=np.where(y==1,(mass0+mass1)/(2*max(mass1,1e-9)),(mass0+mass1)/(2*max(mass0,1e-9)))
    w=basew*cw
    model=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=48,max_interaction_bins=12,interactions=2,outer_bags=6,learning_rate=.025,max_rounds=700,early_stopping_rounds=50,min_samples_leaf=10,n_jobs=-2,random_state=20260821)
    model.fit(tr[FEATURES].apply(pd.to_numeric,errors='coerce'),y,sample_weight=w)
    def evalset(x):
        yy=x.label_keep.astype(int).to_numpy(); pp=model.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]; return metrics(yy,pp)
    rep={
      'reportVersion':'R2_ADD_INTERVENTION_ADAPTER_V1','researchOnly':True,'liveTradingChanges':False,'dreamFillAllowed':False,
      'studentScale':'PRE_CAP100_ORIGINAL_R2','actionQuestion':'At an actual frozen-R2 BEFORE_ADD same-side Maker intervention point with an ACKed pending same-side order, should the new ADD be vetoed for the current execution option?',
      'dataset':{'rows':int(len(d)),'oldRows':int((d.source=='old').sum()),'freshRows':int((d.source=='fresh').sum()),
        'trainRows':int(len(tr)),'trainHighConfidenceRows':int(len(tr_hi)),'validationHighConfidenceRows':int(len(va)),
        'labels':d.teacherExecutionLabel.value_counts().to_dict(),'weakHftOnlyWeight':0.25},
      'split':{'freshTrainMarkets':sorted(fresh_train),'freshValidationMarkets':sorted(fresh_val),'sealedHoldoutMarketsNeverRead':split['sealedHoldoutMarkets']},
      'metrics':{'trainHighConfidence':evalset(tr_hi),'freshValidationHighConfidence':evalset(va)},
      'features':FEATURES,
      'guards':['Target and future HFT fields are teacher-only and never runtime features.','OPEN_ORDER_FILL_LIFECYCLE_V0 predictions are runtime-safe expert inputs.','PENDING_MANAGEMENT_V0 score is advisory expert input only; this adapter learns the intervention boundary.','No PnL threshold sweep; natural 0.5 decision boundary only.','SEALED holdout is not read during training or model selection.']}
    artifact={'version':'R2_ADD_INTERVENTION_ADAPTER_V1','features':FEATURES,'model':model,'classSemantics':{'1':'VETO_NEW_SAME_SIDE_ADD_FOR_CURRENT_OPTION','0':'ALLOW_FROZEN_R2_ADD'},'optionWindowMs':5000,'fillArtifact':str(FILL_ART),'pendingArtifact':str(PENDING_ART),'dreamFillAllowed':False,'runtimeTargetDataAllowed':False,'freshValidationMarkets':sorted(fresh_val),'sealedHoldoutMarkets':split['sealedHoldoutMarkets']}
    joblib.dump(artifact,ART); REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'dataset':rep['dataset'],'metrics':rep['metrics']},ensure_ascii=False,allow_nan=True))
if __name__=='__main__': main()
