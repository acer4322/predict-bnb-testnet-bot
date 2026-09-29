from __future__ import annotations

import json, math
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score,precision_score,recall_score,balanced_accuracy_score,confusion_matrix,log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'pair_completion_sequential_curriculum_v1.jsonl'; COHORT=D/'pair_completion_canonical_cohort_v1.json'
ART=D/'sequential_clarity_hazard_v2.joblib'; REPORT=D/'sequential_clarity_hazard_v2_report.json'
DOM={'REPLACE_DOMINATES','KEEP_DOMINATES'}
CUR=[
 'workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty',
 'absTrackingError','trackingError','actualMakerNet','actualCombinedGross','secondsLeft','recoveryBid','recoveryAsk','recoverySpreadTicks','pairAskSum','pairBidSum',
 'marginalSurplusChunkAvgCost','lockedPairEdgePerShare','lastMakerFillAgeMs','lastMakerFillSideIsRecovery',
 'directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery',
 'futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery',
 'makerTargetRealizationRatio','recoveryMakerRealizationRatio','otherMakerRealizationRatio','makerExecutionFillRatio','makerRecentFillToSubmitRatio20s',
]
DELTA_KEYS=['absTrackingError','trackingError','actualMakerNet','actualCombinedGross','recoveryBid','recoveryAsk','recoverySpreadTicks','pairAskSum','pairBidSum','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty','lockedPairEdgePerShare','lastMakerFillAgeMs','directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery','futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery','makerTargetRealizationRatio','recoveryMakerRealizationRatio','otherMakerRealizationRatio','makerExecutionFillRatio','makerRecentFillToSubmitRatio20s']
TEM=['asymmetryAgeMs','observationDelayMs','hasPriorObservation','elapsedSincePriorMs']+['delta_'+k for k in DELTA_KEYS]+['recoveryChildAppearedSincePrior','recoveryChildDisappearedSincePrior','recoveryStatusChanged','recoveryStatusNew','recoveryStatusPartial']

def fv(v:Any)->float:
 try:
  x=float(v); return x if math.isfinite(x) else math.nan
 except:return math.nan

def enrich(cur:dict[str,Any],prev:dict[str,Any]|None)->dict[str,float]:
 f=cur.get('features') or {}; p=(prev.get('features') or {}) if prev else {}
 out={k:fv(f.get(k)) for k in CUR}; out['asymmetryAgeMs']=fv(cur.get('candidateAsymmetryAgeMs'));out['observationDelayMs']=float(cur.get('delayMs') or 0);out['hasPriorObservation']=float(prev is not None);out['elapsedSincePriorMs']=float(int(cur.get('candidateAtMs') or 0)-int(prev.get('candidateAtMs') or 0)) if prev else 0.0
 for k in DELTA_KEYS:
  a=fv(f.get(k));b=fv(p.get(k)) if prev else math.nan;out['delta_'+k]=(a-b) if math.isfinite(a) and math.isfinite(b) else math.nan
 ce=bool(fv(f.get('workingRecoveryExists')) or 0);pe=bool(fv(p.get('workingRecoveryExists')) or 0) if prev else ce
 out['recoveryChildAppearedSincePrior']=float(ce and not pe);out['recoveryChildDisappearedSincePrior']=float(pe and not ce)
 cs=str(f.get('workingRecoveryStatus') or 'NONE');ps=str(p.get('workingRecoveryStatus') or 'NONE') if prev else cs
 out['recoveryStatusChanged']=float(cs!=ps);out['recoveryStatusNew']=float(cs=='NEW');out['recoveryStatusPartial']=float(cs=='PARTIALLY_FILLED')
 return out

def build()->tuple[list[dict],list[dict],list[dict]]:
 raw=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()];by={}
 for r in raw:by.setdefault(int(r['marketId']),[]).append(r)
 initial=[];haz=[];direction=[]
 for mid,z in by.items():
  z=sorted(z,key=lambda r:int(r['delayMs']));first=None
  for i,r in enumerate(z):
   if str(r.get('paretoLabel')) in DOM:first=i;break
  # initial wait-worth question only applies when current state itself is non-dominant.
  if str(z[0]['paretoLabel']) not in DOM:
   initial.append({'marketId':mid,'y':int(first is not None),'features':enrich(z[0],None),'eventDelayMs':int(z[first]['delayMs']) if first is not None else None})
  if first is None:
   # Right-censored: every interval with a following observation is a no-event hazard state.
   for j in range(len(z)-1):
    haz.append({'marketId':mid,'delayMs':int(z[j]['delayMs']),'y':0,'features':enrich(z[j],z[j-1] if j else None),'censoredMarket':1})
  elif first>0:
   for j in range(first):
    haz.append({'marketId':mid,'delayMs':int(z[j]['delayMs']),'y':int(j==first-1),'features':enrich(z[j],z[j-1] if j else None),'censoredMarket':0})
  if first is not None:
   target=int(str(z[first]['paretoLabel'])=='REPLACE_DOMINATES')
   # Direction can be learned from all information states reachable up to and including first clarity.
   for j in range(first+1):
    direction.append({'marketId':mid,'delayMs':int(z[j]['delayMs']),'y':target,'atClarity':int(j==first),'features':enrich(z[j],z[j-1] if j else None)})
 return initial,haz,direction

def model()->Pipeline:return Pipeline([('i',SimpleImputer(strategy='median',add_indicator=True)),('s',StandardScaler()),('l',LogisticRegression(C=.3,max_iter=4000,class_weight='balanced'))])
def X(rs,fs):return np.asarray([[fv((r.get('features') or {}).get(f)) for f in fs] for r in rs],float)
def fit(tr,fs):m=model();m.fit(X(tr,fs),np.asarray([int(r['y']) for r in tr]));return m
def met(m,rs,fs):
 if not rs:return {'n':0}
 y=np.asarray([int(r['y']) for r in rs]);p=m.predict_proba(X(rs,fs))[:,1];z=p>=.5;both=len(set(y.tolist()))>1
 return {'n':len(rs),'positives':int(y.sum()),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if both else None,'precision':float(precision_score(y,z,zero_division=0)),'recall':float(recall_score(y,z,zero_division=0)),'logLoss':float(log_loss(y,p,labels=[0,1])),'cm':confusion_matrix(y,z,labels=[0,1]).ravel().astype(int).tolist(),'meanP':float(p.mean())}
def run(dataset,name,fs,sets):
 parts={k:[r for r in dataset if int(r['marketId']) in s] for k,s in sets.items()};m=fit(parts['train'],fs);return m,{k:met(m,v,fs) for k,v in parts.items()}
def main():
 initial,haz,direction=build();mids=[int(x) for x in json.loads(COHORT.read_text())['marketIds']];sets={'train':set(mids[:100]),'validation':set(mids[100:126]),'forwardOos':set(mids[126:])}
 out={};arts={}
 for variant,fs in [('current',CUR),('temporal',CUR+TEM)]:
  arts[variant]={};out[variant]={}
  for name,data in [('initialWaitWorth',initial),('clarityHazardNextObservation',haz),('eventualDirectionReplace',direction)]:
   m,rep=run(data,name,fs,sets);arts[variant][name]=m;out[variant][name]=rep
 artifact={'version':'SEQUENTIAL_CLARITY_HAZARD_V2','researchOnly':True,'features':{'current':CUR,'temporal':CUR+TEM},'models':arts,'threshold':.5,'semantics':{'initialWaitWorth':'At a non-dominant first asymmetry state, predict whether any KEEP/REPLACE Pareto clarity will emerge by the fixed 8s observation horizon.','clarityHazardNextObservation':'Discrete-time hazard: from a pre-clarity state, predict whether the next fixed observation is the first Pareto-dominant state; no-clarity markets are right-censored negatives.','eventualDirectionReplace':'For markets that eventually become clear, predict whether the first clarity action is REPLACE rather than KEEP from reachable states up to that event.'},'splitMarkets':{k:sorted(v) for k,v in sets.items()}}
 joblib.dump(artifact,ART)
 report={'version':'SEQUENTIAL_CLARITY_HAZARD_V2_REPORT','researchOnly':True,'liveTradingChanges':False,'datasetCounts':{'initial':len(initial),'hazard':len(haz),'direction':len(direction)},'splitMarkets':{k:len(v) for k,v in sets.items()},'results':out,'guards':['No winner/PnL/Target/future Pareto data in runtime features.','Future dominance is teacher label only.','No threshold sweep; natural 0.5.','Hazard rows after first clarity are censored as unreachable.']}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'datasetCounts':report['datasetCounts'],'results':out},ensure_ascii=False,allow_nan=True))
if __name__=='__main__':main()
