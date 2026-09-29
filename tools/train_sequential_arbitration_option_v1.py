from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, balanced_accuracy_score, confusion_matrix, log_loss, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'sequential_arbitration_option_teacher_v1.jsonl'
COHORT=D/'pair_completion_canonical_cohort_v1.json'
ART=D/'sequential_arbitration_option_v1.joblib'
REPORT=D/'sequential_arbitration_option_v1_report.json'

CURRENT=[
 'workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty',
 'absTrackingError','trackingError','actualMakerNet','actualCombinedGross','secondsLeft',
 'recoveryBid','recoveryAsk','recoverySpreadTicks','pairAskSum','pairBidSum',
 'marginalSurplusChunkAvgCost','lockedPairEdgePerShare','lastMakerFillAgeMs','lastMakerFillSideIsRecovery',
 'directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery',
 'futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery',
 'asymmetryAgeMs','observationDelayMs','hasPriorObservation','elapsedSincePriorMs',
 'recoveryStatusNew','recoveryStatusPartial'
]
DELTA=[
 'delta_absTrackingError','delta_trackingError','delta_actualMakerNet','delta_actualCombinedGross','delta_recoveryBid','delta_recoveryAsk',
 'delta_recoverySpreadTicks','delta_pairAskSum','delta_pairBidSum','delta_workingRecoveryAgeMs','delta_workingRecoveryOffsetTicks',
 'delta_workingRecoveryRemainingQty','delta_lockedPairEdgePerShare','delta_lastMakerFillAgeMs','delta_directionTowardRecovery',
 'delta_spotReturn1sTowardRecovery','delta_spotReturn3sTowardRecovery','delta_spotQueueTowardRecovery','delta_spotTaker1sTowardRecovery',
 'delta_futuresReturn1sTowardRecovery','delta_futuresReturn3sTowardRecovery','delta_futuresQueueTowardRecovery','delta_futuresTaker1sTowardRecovery',
 'recoveryChildAppearedSincePrior','recoveryChildDisappearedSincePrior','recoveryStatusChanged'
]


def finite(v:Any)->float:
 try:
  x=float(v); return x if math.isfinite(x) else math.nan
 except Exception:return math.nan

def pipe()->Pipeline:
 return Pipeline([('imputer',SimpleImputer(strategy='median',add_indicator=True)),('scale',StandardScaler()),('logit',LogisticRegression(C=0.35,max_iter=4000,class_weight='balanced',solver='lbfgs'))])
def X(rows:list[dict[str,Any]], feats:list[str])->np.ndarray:
 return np.asarray([[finite((r.get('features') or {}).get(f)) for f in feats] for r in rows],float)
def metric(model:Pipeline, rows:list[dict[str,Any]], feats:list[str], label_fn)->dict[str,Any]:
 if not rows:return {'n':0}
 y=np.asarray([int(label_fn(r)) for r in rows],int); p=model.predict_proba(X(rows,feats))[:,1]; z=(p>=.5).astype(int); both=len(set(y.tolist()))>1
 return {'n':len(rows),'positiveRate':float(y.mean()),'predictedPositiveRate':float(z.mean()),'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if both else None,'precision':float(precision_score(y,z,zero_division=0)),'recall':float(recall_score(y,z,zero_division=0)),'logLoss':float(log_loss(y,p,labels=[0,1])),'confusionTnFpFnTp':confusion_matrix(y,z,labels=[0,1]).ravel().astype(int).tolist()}

def fit(rows:list[dict[str,Any]],feats:list[str],label_fn)->Pipeline:
 m=pipe(); m.fit(X(rows,feats),np.asarray([int(label_fn(r)) for r in rows],int)); return m

def policy_eval(models:dict[str,Pipeline], rows:list[dict[str,Any]], feats:list[str])->dict[str,Any]:
 if not rows:return {'n':0}
 xa=X(rows,feats); pa=models['act'].predict_proba(xa)[:,1]; pr=models['replace'].predict_proba(xa)[:,1]
 # wait model is only semantically trained for non-ACT states, but we can evaluate it wherever pAct<.5.
 pw=models['wait'].predict_proba(xa)[:,1]
 pred=[]
 for a,w,r in zip(pa,pw,pr):
  if a>=.5: pred.append('REPLACE_ROUTE' if r>=.5 else 'KEEP_EXECUTING')
  else: pred.append('WAIT_FOR_CLARITY' if w>=.5 else 'RETURN_TO_CONTROLLER')
 truth=[str(r['teacherAction']) for r in rows]
 exact=sum(a==b for a,b in zip(pred,truth))
 premature=sum(t in {'WAIT_FOR_CLARITY','RETURN_TO_CONTROLLER'} and p in {'REPLACE_ROUTE','KEEP_EXECUTING'} for t,p in zip(truth,pred))
 missed=sum(t in {'REPLACE_ROUTE','KEEP_EXECUTING'} and p not in {'REPLACE_ROUTE','KEEP_EXECUTING'} for t,p in zip(truth,pred))
 wrong_side=sum(t in {'REPLACE_ROUTE','KEEP_EXECUTING'} and p in {'REPLACE_ROUTE','KEEP_EXECUTING'} and t!=p for t,p in zip(truth,pred))
 wait_on_return=sum(t=='RETURN_TO_CONTROLLER' and p=='WAIT_FOR_CLARITY' for t,p in zip(truth,pred))
 return_on_wait=sum(t=='WAIT_FOR_CLARITY' and p=='RETURN_TO_CONTROLLER' for t,p in zip(truth,pred))
 from collections import Counter
 return {'n':len(rows),'exactAccuracy':exact/len(rows),'predictedActions':dict(Counter(pred)),'trueActions':dict(Counter(truth)),'prematureActOnWaitOrReturn':premature,'prematureActRate':premature/len(rows),'missedAct':missed,'wrongActSide':wrong_side,'waitInsteadOfReturn':wait_on_return,'returnInsteadOfWait':return_on_wait,
 'rows':[{'marketId':int(r['marketId']),'delayMs':int(r['delayMs']),'truth':t,'pred':p,'pAct':float(a),'pWait':float(w),'pReplaceIfAct':float(q)} for r,t,p,a,w,q in zip(rows,truth,pred,pa,pw,pr)]}

def run_variant(name:str,feats:list[str],parts:dict[str,list[dict[str,Any]]])->tuple[dict[str,Pipeline],dict[str,Any]]:
 tr=parts['train']
 act=fit(tr,feats,lambda r:r['teacherActNow'])
 tr_no=[r for r in tr if int(r['teacherActNow'])==0]
 wait=fit(tr_no,feats,lambda r:r['teacherWait'])
 tr_act=[r for r in tr if int(r['teacherActNow'])==1]
 rep=fit(tr_act,feats,lambda r:r['teacherReplaceIfAct'])
 models={'act':act,'wait':wait,'replace':rep}
 out={'name':name,'features':feats,'actNow':{},'waitVsReturn':{},'replaceVsKeep':{},'policy':{}}
 for split,rs in parts.items():
  out['actNow'][split]=metric(act,rs,feats,lambda r:r['teacherActNow'])
  nr=[r for r in rs if int(r['teacherActNow'])==0]
  ar=[r for r in rs if int(r['teacherActNow'])==1]
  out['waitVsReturn'][split]=metric(wait,nr,feats,lambda r:r['teacherWait'])
  out['replaceVsKeep'][split]=metric(rep,ar,feats,lambda r:r['teacherReplaceIfAct'])
  out['policy'][split]=policy_eval(models,rs,feats)
 return models,out

def main()->int:
 rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
 mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
 split={'train':set(mids[:100]),'validation':set(mids[100:126]),'forwardOos':set(mids[126:])}
 parts={k:[r for r in rows if int(r['marketId']) in ids] for k,ids in split.items()}
 base_models,base=run_variant('CURRENT_ONLY',CURRENT,parts)
 temp_models,temp=run_variant('CURRENT_PLUS_TEMPORAL_DELTAS',CURRENT+DELTA,parts)
 artifact={'version':'SEQUENTIAL_ARBITRATION_OPTION_V1','researchOnly':True,'liveTradingChanges':False,'thresholds':{'act':.5,'wait':.5,'replace':.5},'currentOnly':{'models':base_models,'features':CURRENT},'temporal':{'models':temp_models,'features':CURRENT+DELTA},'splitMarkets':{k:sorted(v) for k,v in split.items()},'semantics':'Hierarchical runtime: ACT_NOW? If no, WAIT_FOR_CLARITY vs RETURN_TO_CONTROLLER. If yes, KEEP_EXECUTING vs REPLACE_ROUTE. No threshold sweep.'}
 joblib.dump(artifact,ART)
 report={'version':'SEQUENTIAL_ARBITRATION_OPTION_V1_REPORT','researchOnly':True,'liveTradingChanges':False,'teacher':str(SRC),'split':{k:{'markets':len(v),'rows':len(parts[k])} for k,v in split.items()},'currentOnly':base,'temporal':temp,'guardrails':['Market-level chronological 100 Frozen train / 26 Frozen validation / 23 COMPLETE_FORWARD_V1 OOS.','No winner/PnL/Target/future Pareto field in runtime features.','Temporal features are differences between current and previous already-observed execution states only.','Natural threshold 0.5 only; no sweep.','Later no-action states after teacher ACT are censored.']}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 def compact(v):
  return {'act':v['actNow'],'wait':v['waitVsReturn'],'replace':v['replaceVsKeep'],'policy':{k:{q:x[q] for q in ['n','exactAccuracy','prematureActRate','missedAct','wrongActSide','waitInsteadOfReturn','returnInsteadOfWait','predictedActions','trueActions']} for k,x in v['policy'].items()}}
 print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'split':report['split'],'currentOnly':compact(base),'temporal':compact(temp)},ensure_ascii=False,allow_nan=True))
 return 0
if __name__=='__main__':raise SystemExit(main())
