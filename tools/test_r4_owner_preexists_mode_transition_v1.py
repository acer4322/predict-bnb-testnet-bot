from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'r4_v0'/'hourly'
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_OWNER_PREEXISTS_MODE_TRANSITION_V1'
FILES={
 'FRESH24':OUT/'r4_management_hft_shadow_fresh24_v1_rows.csv',
 'UNSEEN24':OUT/'r4_management_hft_shadow_unseen24_v1_rows.csv',
 'REPLICATION3':OUT/'r4_management_hft_shadow_replication3_v1_rows.csv',
}
BASE=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s','events_5s','events_15s','transitions_15s']
TARGETS=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s']

def load(p):
 d=pd.read_csv(p)
 d=d[(d.seconds_left>=60)&(d.seconds_left<=300)&(d.weak_active_owners>0)].copy()
 d['owner_preexists_mode_transition']=(d.weak_oldest_age_s>d.current_mode_age_s).astype(float)
 for c in BASE+TARGETS+['owner_preexists_mode_transition']:
  d[c]=pd.to_numeric(d[c],errors='coerce')
 return d.dropna(subset=BASE+TARGETS+['owner_preexists_mode_transition'])

def eval_one(train,test,target,features):
 ytr=train[target].astype(int).to_numpy(); y=test[target].astype(int).to_numpy()
 if len(np.unique(ytr))<2 or len(np.unique(y))<2:
  return {'rows':int(len(test)),'positives':int(y.sum()),'eligible':False,'reason':'single_class'}
 m=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=2000,solver='lbfgs',random_state=20260827))
 m.fit(train[features].to_numpy(float),ytr)
 p=m.predict_proba(test[features].to_numpy(float))[:,1]
 return {'rows':int(len(test)),'positives':int(y.sum()),'eligible':bool(len(test)>=30 and y.sum()>=5),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 ds={k:load(v) for k,v in FILES.items()}
 train=ds['FRESH24']
 result={}
 eligible_all=True; deltas=[]
 for name in ['UNSEEN24','REPLICATION3']:
  te=ds[name]; rr={}
  for t in TARGETS:
   b=eval_one(train,te,t,BASE); f=eval_one(train,te,t,BASE+['owner_preexists_mode_transition'])
   if not (b.get('eligible') and f.get('eligible')):
    eligible_all=False
    rr[t]={'baseline':b,'candidate':f,'delta':None}
   else:
    de={'aucLift':f['auc']-b['auc'],'apLift':f['ap']-b['ap'],'logLossImprovement':b['logLoss']-f['logLoss']}
    rr[t]={'baseline':b,'candidate':f,'delta':de}; deltas.append((name,t,de))
  result[name]=rr
 support={k:{'rows':int(len(d)),'markets':int(d.marketId.nunique()),'preexistingRows':int(d.owner_preexists_mode_transition.sum()),'newerRows':int((1-d.owner_preexists_mode_transition).sum())} for k,d in ds.items()}
 if eligible_all:
  bytarget={}
  for t in TARGETS:
   xs=[de for n,tt,de in deltas if tt==t]
   bytarget[t]={'meanAucLift':float(np.mean([x['aucLift'] for x in xs])),'meanApLift':float(np.mean([x['apLift'] for x in xs])),'meanLogLossImprovement':float(np.mean([x['logLossImprovement'] for x in xs]))}
  nonneg=sum(de['aucLift']>=0 for _,_,de in deltas); worst=min(de['aucLift'] for _,_,de in deltas)
  keep=all(v['meanAucLift']>0 and v['meanApLift']>=0 and v['meanLogLossImprovement']>=0 for v in bytarget.values()) and nonneg>=5 and worst>=-0.02
  status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
 else:
  bytarget={}; nonneg=None; worst=None; keep=False; status='TESTED_INCONCLUSIVE'
 rep={
  'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'status':status,
  'candidate':'Predictor-only test of whether a live weak-side resting owner predating the current Formation-mode transition adds local path-quality information beyond geometry, owner counts, raw owner age and raw mode age.',
  'semanticNovelty':'Unlike prior prearm timing/control and rolling KEEP/PULL/REPRICE experiments, no order timing/action is changed. This isolates the relational lifecycle fact that an already-live queue option survived across a Formation state transition.',
  'layerAssignment':{'rawReceiptOwnerState':'EXECUTION_INFORMATION','ownerPreexistsModeTransition':'EXECUTION_LIFECYCLE_BELIEF_CANDIDATE','portfolioPayoff':'LOGIC_STATE_CONTEXT','authority':'NOT_ACTION_AUTHORITY'},
  'cohort':{'execution':'RECEIPT_CLOCK_REALISTIC_HFT_NON_LIVE','phase':'60-300s','weakOwnerRequired':True,'special20260816Sealed':True,'echtgeldTraining':False,'support':support},
  'model':{'type':'fixed standardized logistic regression','train':'FRESH24','independentHoldouts':['UNSEEN24','REPLICATION3'],'baselineFeatures':BASE,'candidateIncrement':['owner_preexists_mode_transition'],'thresholdSweep':False},
  'results':result,'summary':{'allHoldoutsEligible':eligible_all,'targetMeans':bytarget,'nonnegativeAucLiftsOf6':nonneg,'worstAucLift':worst,'keepRulePass':keep},
  'guards':{'researchOnly':True,'no8781Change':True,'noLiveR3Change':True,'noNewEchtgeld':True,'noDreamFill':True,'futureLabelsOfflineOnly':True,'winnerSettlementRuntimeForbidden':True}
 }
 p=OUT/'r4_owner_preexists_mode_transition_v1.json';p.write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'status':status,'support':support,'summary':rep['summary'],'results':result},ensure_ascii=False))
if __name__=='__main__':main()
