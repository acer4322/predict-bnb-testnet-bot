from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_semantic_stacking_arbiter_v1_oof_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_transition_disagreement_uncertainty_v1.json'
C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
STATE=['seconds_left','risk_deficit','floor_per_gross','absnet_ratio','weak_active_owners','dominant_active_owners','current_mode_age_s','transitions_15s']
BASE=['flat_max_probability','flat_probability_entropy']+STATE
FULL=BASE+['p_transition_std']

def entropy(a):
    a=np.clip(np.asarray(a,float),1e-9,1.0)
    return -(a*np.log(a)).sum(axis=1)

def prep(d):
    fp=d[[f'FLAT_{c}' for c in C]].to_numpy(float)
    fp=np.clip(fp,1e-9,None); fp=fp/fp.sum(1,keepdims=True)
    d=d.copy(); d['flat_max_probability']=fp.max(1); d['flat_probability_entropy']=entropy(fp)
    pred=np.asarray(C)[fp.argmax(1)]
    d['manager_error']=(pred!=d.label.to_numpy()).astype(int)
    return d

def met(y,p):
    y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
    return {'n':int(len(y)),'errors':int(y.sum()),'correct':int(len(y)-y.sum()),'errorRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}

def fit_predict(train,test,features):
    m=LogisticRegression(C=1.0,max_iter=2000,class_weight='balanced',random_state=20260827)
    m.fit(train[features].astype(float),train.manager_error.astype(int))
    return m.predict_proba(test[features].astype(float))[:,1]

def main():
    d=prep(pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan).fillna(0.0))
    tr=d[d.block==1].copy()
    blocks=[]; eligible=True
    for bi in [2,3,4]:
        q=d[d.block==bi].copy()
        support=bool(q.manager_error.sum()>=30 and (len(q)-q.manager_error.sum())>=30)
        if not support:
            blocks.append({'block':bi,'support':False,'rows':int(len(q)),'errors':int(q.manager_error.sum()),'correct':int(len(q)-q.manager_error.sum())});eligible=False;continue
        pb=fit_predict(tr,q,BASE); pf=fit_predict(tr,q,FULL); mb=met(q.manager_error,pb); mf=met(q.manager_error,pf)
        delta={'auc':mf['auc']-mb['auc'],'ap':mf['ap']-mb['ap'],'logLossImprovement':mb['logLoss']-mf['logLoss'],'brierImprovement':mb['brier']-mf['brier']}
        blocks.append({'block':bi,'support':True,'baseline':mb,'candidate':mf,'delta':delta,'meanTransitionStd':float(q.p_transition_std.mean()),'errorMeanTransitionStd':float(q.loc[q.manager_error==1,'p_transition_std'].mean()),'correctMeanTransitionStd':float(q.loc[q.manager_error==0,'p_transition_std'].mean())})
    if eligible:
        ds=[b['delta'] for b in blocks]
        summary={'eligibleBlocks':3,'meanDeltaAuc':float(np.mean([x['auc'] for x in ds])),'worstDeltaAuc':float(np.min([x['auc'] for x in ds])),'meanDeltaAp':float(np.mean([x['ap'] for x in ds])),'meanLogLossImprovement':float(np.mean([x['logLossImprovement'] for x in ds])),'meanBrierImprovement':float(np.mean([x['brierImprovement'] for x in ds]))}
        keep=summary['worstDeltaAuc']>=0 and summary['meanDeltaAuc']>=.02 and summary['meanDeltaAp']>0 and summary['meanLogLossImprovement']>0 and summary['meanBrierImprovement']>0
        status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
    else:
        summary={'eligibleBlocks':sum(bool(b.get('support')) for b in blocks)};status='TESTED_INCONCLUSIVE'
    art={'version':'R4_MANAGEMENT_TRANSITION_DISAGREEMENT_UNCERTAINTY_V1','testId':'R4_MANAGEMENT_TRANSITION_DISAGREEMENT_UNCERTAINTY_V1_20260827_2138','researchOnly':True,'actionAuthority':False,'semanticAxis':'MANAGEMENT / PARALLEL_TRANSITION_BELIEF_UNCERTAINTY_CALIBRATION','layerAssignment':{'p_transition_std':'MANAGEMENT_UNCERTAINTY_BELIEF_CANDIDATE','flatManagerAndState':'MANAGEMENT_LOGIC_CONTEXT','output':'CALIBRATION_CONTEXT_ONLY_NOT_ACTION_AUTHORITY'},'cohort':{'source':str(SRC.relative_to(ROOT)).replace('\\','/'),'trainBlock':1,'evaluationBlocks':[2,3,4],'trainRows':int(len(tr)),'trainMarkets':int(tr.market_id.nunique()),'sealed20260816':True,'echtgeldTraining':False},'features':{'baseline':BASE,'candidateIncrement':['p_transition_std']},'label':'existing flat manager argmax is wrong vs observed Target management label','blocks':blocks,'summary':summary,'status':status,'decision':status,'guards':['strict OOS specialist/flat predictions inherited from source table','no threshold/model/hyperparameter sweep','not action authority','no Echtgeld training','2026-08-16 SEALED']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'summary':summary,'blocks':blocks},ensure_ascii=False))
if __name__=='__main__': main()
