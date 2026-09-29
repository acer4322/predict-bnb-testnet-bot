from __future__ import annotations

import json, math
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, average_precision_score, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'sequential_arbitration_option_teacher_v1.jsonl'
COHORT=D/'pair_completion_canonical_cohort_v1.json'
OPTION_ART=D/'sequential_arbitration_option_v1.joblib'
OPTION_REPORT=D/'sequential_arbitration_option_v1_report.json'
OUT=D/'execution_arbitration_needed_head_v0_report.json'
ART=D/'execution_arbitration_needed_head_v0.joblib'

# Pre-registered lifecycle/ownership/progression-only feature family. No winner/PnL,
# no Target runtime state, and no broad market-direction predictors.
FEATURES=[
 'workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty',
 'absTrackingError','trackingError','actualMakerNet','actualCombinedGross','asymmetryAgeMs',
 'observationDelayMs','hasPriorObservation','elapsedSincePriorMs',
 'delta_absTrackingError','delta_trackingError','delta_actualMakerNet','delta_actualCombinedGross',
 'delta_workingRecoveryAgeMs','delta_workingRecoveryOffsetTicks','delta_workingRecoveryRemainingQty',
 'delta_lastMakerFillAgeMs','recoveryChildAppearedSincePrior','recoveryChildDisappearedSincePrior',
 'recoveryStatusChanged','recoveryStatusNew','recoveryStatusPartial','lastMakerFillAgeMs',
 'lastMakerFillSideIsRecovery'
]
ACT={'KEEP_EXECUTING','REPLACE_ROUTE'}


def finite(v:Any)->float:
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception: return math.nan

def mat(rows, feats):
    return np.asarray([[finite((r.get('features') or {}).get(f)) for f in feats] for r in rows],float)

def y_need(rows): return np.asarray([1 if str(r['teacherAction']) in ACT else 0 for r in rows],int)

def binary_probs(option, rows):
    feats=list(option['features']); x=mat(rows,feats); m=option['models']
    return m['wait'].predict_proba(x)[:,1],m['replace'].predict_proba(x)[:,1]

def policy_metrics(rows,p_need,p_wait,p_replace):
    pred=[]
    for pn,pw,pr in zip(p_need,p_wait,p_replace):
        if pn>=0.5:
            pred.append('REPLACE_ROUTE' if pr>=0.5 else 'KEEP_EXECUTING')
        else:
            pred.append('WAIT_FOR_CLARITY' if pw>=0.5 else 'RETURN_TO_CONTROLLER')
    truth=[str(r['teacherAction']) for r in rows]
    premature=sum(t not in ACT and p in ACT for t,p in zip(truth,pred))
    missed=sum(t in ACT and p not in ACT for t,p in zip(truth,pred))
    return {
      'n':len(rows),'exactAccuracy':accuracy_score(truth,pred) if rows else None,
      'prematureAct':premature,'prematureActRate':premature/len(rows) if rows else None,
      'missedAct':missed,'missedActRate':missed/len(rows) if rows else None,
      'predictedActions':dict(Counter(pred)),'trueActions':dict(Counter(truth))
    }

def gate_metrics(rows,p):
    y=y_need(rows)
    if len(set(y))<2: auc=ap=None
    else: auc=float(roc_auc_score(y,p)); ap=float(average_precision_score(y,p))
    return {'n':len(rows),'positive':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,
            'auc':auc,'ap':ap,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,
            'predActRateAtNatural0p5':float(np.mean(p>=0.5)) if len(p) else None}

def compact_monolithic(old, split):
    p=old['currentOnly']['policy'][split]
    return {k:p[k] for k in ['n','exactAccuracy','predictedActions','trueActions','prematureActOnWaitOrReturn','prematureActRate','missedAct']}

def main():
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
    splits={'train':set(mids[:100]),'validation':set(mids[100:126]),'forwardOos':set(mids[126:])}
    parts={k:[r for r in rows if int(r['marketId']) in ids] for k,ids in splits.items()}
    model=Pipeline([
      ('imputer',SimpleImputer(strategy='median',add_indicator=True)),
      ('scale',StandardScaler()),
      ('clf',LogisticRegression(C=1.0,max_iter=2000,class_weight='balanced',random_state=20260821))
    ])
    model.fit(mat(parts['train'],FEATURES),y_need(parts['train']))
    opt=joblib.load(OPTION_ART)['currentOnly']
    old=json.loads(OPTION_REPORT.read_text(encoding='utf-8'))
    rep={'version':'EXECUTION_ARBITRATION_NEEDED_HEAD_V0','researchOnly':True,'candidateFrozen':False,
         'graduationEligible':False,'question':'Can a separate lifecycle/ownership progression head decide WHEN arbitration is needed before frozen binary action heads choose WHAT to do?',
         'label':'1 iff teacherAction in {KEEP_EXECUTING,REPLACE_ROUTE}; 0 iff {WAIT_FOR_CLARITY,RETURN_TO_CONTROLLER}',
         'features':FEATURES,'model':'fixed LogisticRegression C=1 balanced; natural 0.5 only; no sweep',
         'splits':{},'guards':['Opened canonical 149-market execution curriculum only.','No Candidate V1 formal exam outcomes used.','No winner/PnL/Target future/runtime input.','Frozen WAIT-vs-RETURN and REPLACE-vs-KEEP heads reused unchanged.','No threshold/hyperparameter sweep.']}
    for name,rs in parts.items():
        p=model.predict_proba(mat(rs,FEATURES))[:,1]
        pw,pr=binary_probs(opt,rs)
        rep['splits'][name]={'gate':gate_metrics(rs,p),'hierarchicalPolicy':policy_metrics(rs,p,pw,pr),'monolithicV1':compact_monolithic(old,name)}
    joblib.dump({'version':rep['version'],'features':FEATURES,'model':model,'label':rep['label'],'trainingMarketIds':mids[:100]},ART)
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(OUT),'artifact':str(ART),'splits':{k:v for k,v in rep['splits'].items()}},ensure_ascii=False))
if __name__=='__main__': main()
