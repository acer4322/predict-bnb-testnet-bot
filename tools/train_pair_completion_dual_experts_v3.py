from __future__ import annotations

import json,sys
from pathlib import Path
import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,precision_score,recall_score,log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/pair_completion_tradeoff_curriculum_v3.jsonl'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
ART=OUT/'pair_completion_dual_experts_v3.joblib'
REPORT=OUT/'pair_completion_dual_experts_v3_report.json'
H=(5,10,20)

NEED_FEATURES=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty','absTrackingError','actualCombinedGross','secondsLeft','recoverySpreadTicks','pairAskSum','lastMakerFillAgeMs']
COST_FEATURES=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','absTrackingError','secondsLeft','recoveryAsk','recoverySpreadTicks','pairAskSum','directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery','futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery']

def consensus(vals):
    neg=any(x<0 for x in vals); pos=any(x>0 for x in vals)
    if neg and not pos:return 'REPLACE'
    if pos and not neg:return 'KEEP'
    if not neg and not pos:return 'NEUTRAL'
    return 'MIXED'

def pipe():
    return Pipeline([('imputer',SimpleImputer(strategy='median',add_indicator=True)),('scaler',StandardScaler()),('logit',LogisticRegression(C=0.5,max_iter=3000,solver='lbfgs',class_weight='balanced'))])

def xy(rows,features,label_kind):
    X=[]; y=[]
    for r in rows:
        if label_kind=='need': lab=consensus([float(r[f'deltaTargetErrorArea{h}s']) for h in H])
        else: lab=consensus([float(r[f'deltaCompletionCost{h}s']) for h in H])
        if lab not in {'REPLACE','KEEP'}: continue
        f=r.get('features') or {}; X.append([f.get(k) for k in features]); y.append(1 if lab=='REPLACE' else 0)
    return np.asarray(X,float),np.asarray(y,int)

def met(m,X,y):
    if not len(y): return {'n':0}
    p=m.predict_proba(X)[:,1]; z=(p>=0.5).astype(int)
    return {'n':int(len(y)),'positiveRate':float(y.mean()),'predictedPositiveRate':float(z.mean()),'predictedMean':float(p.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if len(set(y.tolist()))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if len(set(y.tolist()))>1 else None,'precision':float(precision_score(y,z,zero_division=0)),'recall':float(recall_score(y,z,zero_division=0)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]; rows.sort(key=lambda r:int(r['checkpointMs']))
    cut=int(len(rows)*0.70); tr=rows[:cut]; va=rows[cut:]
    Xn,yn=xy(tr,NEED_FEATURES,'need'); Xnv,ynv=xy(va,NEED_FEATURES,'need')
    Xc,yc=xy(tr,COST_FEATURES,'cost'); Xcv,ycv=xy(va,COST_FEATURES,'cost')
    need=pipe(); cost=pipe(); need.fit(Xn,yn); cost.fit(Xc,yc)
    # Joint conservative gate on every chronological validation state.
    joint=[]
    for r in va:
        f=r.get('features') or {}
        pn=float(need.predict_proba(np.asarray([[f.get(k) for k in NEED_FEATURES]],float))[:,1][0])
        pc=float(cost.predict_proba(np.asarray([[f.get(k) for k in COST_FEATURES]],float))[:,1][0])
        gate=int(pn>=0.5 and pc>=0.5)
        truth=int(r.get('paretoLabel')=='REPLACE_DOMINATES')
        joint.append((truth,gate,pn,pc,int(r['marketId']),r.get('paretoLabel')))
    yt=np.asarray([x[0] for x in joint],int); zg=np.asarray([x[1] for x in joint],int)
    gate_metrics={'n':len(joint),'trueReplaceDominates':int(yt.sum()),'selectedAutoReplace':int(zg.sum()),'balancedAccuracy':float(balanced_accuracy_score(yt,zg)) if len(set(yt.tolist()))>1 else None,'precision':float(precision_score(yt,zg,zero_division=0)),'recall':float(recall_score(yt,zg,zero_division=0)),'selectedRows':[{'marketId':x[4],'truth':x[5],'pNeed':x[2],'pCost':x[3]} for x in joint if x[1]]}
    rep={'version':'PAIR_COMPLETION_DUAL_EXPERTS_V3','researchOnly':True,'liveTradingChanges':False,'split':{'allRows':len(rows),'trainRows':len(tr),'validationRows':len(va),'trainMaxCheckpointMs':max(int(r['checkpointMs']) for r in tr),'validationMinCheckpointMs':min(int(r['checkpointMs']) for r in va)},'needExpert':{'semantics':'Predict whether REPLACE consistently improves local 5/10/20s target tracking versus KEEP. Mixed/neutral teacher states excluded. Execution-state features only.','features':NEED_FEATURES,'train':met(need,Xn,yn),'validation':met(need,Xnv,ynv)},'costExpert':{'semantics':'Predict whether REPLACE consistently lowers same-chunk completion cost at 5/10/20s versus KEEP. Mixed/neutral cost states excluded. Recovery-aligned short-horizon microstructure allowed; winner/PnL excluded.','features':COST_FEATURES,'train':met(cost,Xc,yc),'validation':met(cost,Xcv,ycv)},'jointGate':{'rule':'AUTO_REPLACE only if pNeed>=0.5 AND pCost>=0.5; otherwise do not auto-replace.',**gate_metrics},'guardrails':['No threshold sweep.','No winner, settlement PnL, Target runtime state, or future fill feature.','Validation is chronological tail only.','TRADEOFF is not forced into an action label.']}
    joblib.dump({'version':'PAIR_COMPLETION_DUAL_EXPERTS_V3','needModel':need,'costModel':cost,'needFeatures':NEED_FEATURES,'costFeatures':COST_FEATURES,'threshold':0.5,'trainingMaxCheckpointMs':rep['split']['trainMaxCheckpointMs'],'researchOnly':True},ART)
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'need':rep['needExpert'],'cost':rep['costExpert'],'jointGate':rep['jointGate']},ensure_ascii=False))
if __name__=='__main__': main()
