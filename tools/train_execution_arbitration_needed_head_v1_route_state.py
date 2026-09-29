from __future__ import annotations
import json, math
from collections import Counter
from pathlib import Path
from typing import Any
import joblib, numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, average_precision_score, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'sequential_arbitration_option_teacher_v1.jsonl'; COHORT=D/'pair_completion_canonical_cohort_v1.json'
OPTION_ART=D/'sequential_arbitration_option_v1.joblib'; OPTION_REPORT=D/'sequential_arbitration_option_v1_report.json'
OUT=D/'execution_arbitration_needed_head_v1_route_state_report.json'; ART=D/'execution_arbitration_needed_head_v1_route_state.joblib'
# One pre-stated representation change vs V0: add strict-past route economics/progression already present in teacher rows.
# No direction/spot/futures predictors, no outcome/PnL, no threshold/model sweep.
FEATURES=[
 'workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty',
 'absTrackingError','trackingError','actualMakerNet','actualCombinedGross','asymmetryAgeMs','observationDelayMs',
 'hasPriorObservation','elapsedSincePriorMs','lastMakerFillAgeMs','lastMakerFillSideIsRecovery',
 'recoveryBid','recoveryAsk','recoverySpreadTicks','pairAskSum','pairBidSum','marginalSurplusChunkAvgCost','lockedPairEdgePerShare',
 'delta_absTrackingError','delta_trackingError','delta_actualMakerNet','delta_actualCombinedGross',
 'delta_workingRecoveryAgeMs','delta_workingRecoveryOffsetTicks','delta_workingRecoveryRemainingQty','delta_lastMakerFillAgeMs',
 'delta_recoveryBid','delta_recoveryAsk','delta_recoverySpreadTicks','delta_pairAskSum','delta_pairBidSum','delta_lockedPairEdgePerShare',
 'recoveryChildAppearedSincePrior','recoveryChildDisappearedSincePrior','recoveryStatusChanged','recoveryStatusNew','recoveryStatusPartial'
]
ACT={'KEEP_EXECUTING','REPLACE_ROUTE'}
def finite(v:Any)->float:
 try:
  x=float(v); return x if math.isfinite(x) else math.nan
 except Exception:return math.nan
def mat(rows,feats):return np.asarray([[finite((r.get('features') or {}).get(f)) for f in feats] for r in rows],float)
def y_need(rows):return np.asarray([1 if str(r['teacherAction']) in ACT else 0 for r in rows],int)
def binary_probs(option,rows):
 x=mat(rows,list(option['features'])); m=option['models']; return m['wait'].predict_proba(x)[:,1],m['replace'].predict_proba(x)[:,1]
def policy_metrics(rows,p_need,p_wait,p_replace):
 pred=[]
 for pn,pw,pr in zip(p_need,p_wait,p_replace):
  pred.append(('REPLACE_ROUTE' if pr>=.5 else 'KEEP_EXECUTING') if pn>=.5 else ('WAIT_FOR_CLARITY' if pw>=.5 else 'RETURN_TO_CONTROLLER'))
 truth=[str(r['teacherAction']) for r in rows]; premature=sum(t not in ACT and p in ACT for t,p in zip(truth,pred)); missed=sum(t in ACT and p not in ACT for t,p in zip(truth,pred))
 return {'n':len(rows),'exactAccuracy':accuracy_score(truth,pred),'prematureAct':premature,'prematureActRate':premature/len(rows),'missedAct':missed,'missedActRate':missed/len(rows),'predictedActions':dict(Counter(pred)),'trueActions':dict(Counter(truth))}
def gate_metrics(rows,p):
 y=y_need(rows); auc=float(roc_auc_score(y,p)) if len(set(y))>1 else None; ap=float(average_precision_score(y,p)) if len(set(y))>1 else None
 return {'n':len(rows),'positive':int(y.sum()),'positiveRate':float(y.mean()),'auc':auc,'ap':ap,'logLoss':float(log_loss(y,p,labels=[0,1])),'predActRateAtNatural0p5':float(np.mean(p>=.5))}
def compact(old,split):
 p=old['currentOnly']['policy'][split]; return {k:p[k] for k in ['n','exactAccuracy','predictedActions','trueActions','prematureActOnWaitOrReturn','prematureActRate','missedAct']}
def main():
 rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]; mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
 splits={'train':set(mids[:100]),'validation':set(mids[100:126]),'forwardOos':set(mids[126:])}; parts={k:[r for r in rows if int(r['marketId']) in ids] for k,ids in splits.items()}
 model=Pipeline([('imputer',SimpleImputer(strategy='median',add_indicator=True)),('scale',StandardScaler()),('clf',LogisticRegression(C=1,max_iter=2000,class_weight='balanced',random_state=20260821))]); model.fit(mat(parts['train'],FEATURES),y_need(parts['train']))
 opt=joblib.load(OPTION_ART)['currentOnly']; old=json.loads(OPTION_REPORT.read_text(encoding='utf-8'))
 rep={'version':'EXECUTION_ARBITRATION_NEEDED_HEAD_V1_ROUTE_STATE','researchOnly':True,'candidateFrozen':False,'graduationEligible':False,'representationChange':'V0 lifecycle/ownership plus strict-past recovery-route book economics and their progression deltas; excludes direction/spot/futures predictors','label':'1 iff teacherAction in {KEEP_EXECUTING,REPLACE_ROUTE}; 0 iff {WAIT_FOR_CLARITY,RETURN_TO_CONTROLLER}','features':FEATURES,'model':'fixed LogisticRegression C=1 balanced; natural 0.5 only; no sweep','splits':{},'guards':['Opened canonical 149-market execution curriculum only.','No Candidate V1 formal exam outcomes used.','No winner/PnL/Target future/runtime input.','No direction/spot/futures predictors.','Frozen WAIT-vs-RETURN and REPLACE-vs-KEEP heads unchanged.','No threshold/hyperparameter sweep.']}
 for name,rs in parts.items():
  p=model.predict_proba(mat(rs,FEATURES))[:,1]; pw,pr=binary_probs(opt,rs); rep['splits'][name]={'gate':gate_metrics(rs,p),'hierarchicalPolicy':policy_metrics(rs,p,pw,pr),'monolithicV1':compact(old,name)}
 joblib.dump({'version':rep['version'],'features':FEATURES,'model':model,'label':rep['label'],'trainingMarketIds':mids[:100]},ART); OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'report':str(OUT),'splits':rep['splits']},ensure_ascii=False))
if __name__=='__main__':main()
