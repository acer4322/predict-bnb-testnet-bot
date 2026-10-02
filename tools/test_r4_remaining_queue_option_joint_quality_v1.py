from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_option_counterfactual_v1.json'
QPROG=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_progress_proxy_v0.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_remaining_queue_option_joint_quality_v1.json'
TZ=ZoneInfo('Asia/Taipei')
BASE=['orderAgeMs','quoteOffsetTicks','recoveryDeficit','secondsLeft']
INC=['initialVisibleDepth','currentVisibleDepth','depletionOverInitial','netDepletionOverInitial']

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'rows':int(len(y)),'positives':int(y.sum()),'negatives':int(len(y)-y.sum()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}

def main():
 cf=json.loads(SRC.read_text(encoding='utf-8')); qp=json.loads(QPROG.read_text(encoding='utf-8'))
 q={int(r['marketId']):r for r in qp['rows']}
 rows=[]
 for r in cf['rows']:
  if not r.get('eligible') or not r.get('queueFeatures') or r.get('queueValueTargetErrorArea') is None: continue
  mid=int(r['marketId']); z=q.get(mid)
  if z is None: continue
  f=r['queueFeatures']
  # strict joint advantage: KEEP has no more tracking error and no more final abs tracking, with >=1 strict improvement.
  a=float(r['keepTargetErrorArea']); b=float(r['reinsertTargetErrorArea'])
  c=float(r['keepFinalAbsTracking']); d=float(r['reinsertFinalAbsTracking'])
  y=int((a<=b+1e-9 and c<=d+1e-9) and (a<b-1e-9 or c<d-1e-9))
  x={
   'marketId':mid,'candidateAtMs':int(r['candidateAtMs']),'y':y,
   'orderAgeMs':float(f['orderAgeMs']),'quoteOffsetTicks':float(f['quoteOffsetTicks']),
   'recoveryDeficit':float(f['recoveryDeficit']),'secondsLeft':float(f['secondsLeft']),
   'initialVisibleDepth':float(z['initialVisibleDepth']),'currentVisibleDepth':float(z['currentVisibleDepth']),
   'depletionOverInitial':float(z['depletionOverInitial']),'netDepletionOverInitial':float(z['netDepletionOverInitial'])
  }; rows.append(x)
 rows=sorted(rows,key=lambda r:r['candidateAtMs']); tr=rows[:20]; te=rows[20:]
 def fit(feats):
  X=np.asarray([[r[k] for k in feats] for r in tr],float); y=np.asarray([r['y'] for r in tr],int)
  Xt=np.asarray([[r[k] for k in feats] for r in te],float); yt=np.asarray([r['y'] for r in te],int)
  if len(set(y))<2 or len(set(yt))<2:return None,None
  m=make_pipeline(StandardScaler(),LogisticRegression(C=1.0,max_iter=2000,solver='lbfgs',random_state=20260827)).fit(X,y)
  return met(yt,m.predict_proba(Xt)[:,1]),m
 b,_=fit(BASE); f,_=fit(BASE+INC)
 support=bool(len(te)>=10 and sum(r['y'] for r in te)>=2 and (len(te)-sum(r['y'] for r in te))>=2 and b and f)
 if support:
  delta={'aucLift':f['auc']-b['auc'],'apLift':f['ap']-b['ap'],'logLossImprovement':b['logLoss']-f['logLoss']}
  keep=bool(delta['aucLift']>=.03 and delta['apLift']>=0 and delta['logLossImprovement']>=0)
  status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED'
 else:
  delta=None;keep=False;status='TESTED_INCONCLUSIVE'
 rep={'version':'R4_REMAINING_QUEUE_OPTION_JOINT_QUALITY_V1','createdAt':datetime.now(TZ).isoformat(),'status':status,
 'semanticNovelty':'Incremental execution-lifecycle belief for joint KEEP quality across tracking-area and final abs tracking; prior queue-progress work only reported univariate relation to tracking-area difference.',
 'layerAssignment':{'queueProgress':'EXECUTION_INFORMATION','remainingQueueOptionJointQuality':'EXECUTION_LIFECYCLE_BELIEF_CANDIDATE','authority':'NOT_ACTION_AUTHORITY'},
 'cohort':{'source':'R2_QUEUE_OPTION_COUNTERFACTUAL_V1 + R2_QUEUE_PROGRESS_PROXY_V0','rows':len(rows),'trainRows':len(tr),'holdoutRows':len(te),'trainPositives':sum(r['y'] for r in tr),'holdoutPositives':sum(r['y'] for r in te),'special20260816Sealed':True,'echtgeldTraining':False},
 'features':{'baseline':BASE,'increment':INC},'baseline':b,'candidate':f,'delta':delta,'keepRulePass':keep,
 'guards':{'researchOnly':True,'noActionAuthority':True,'futureCounterfactualLabelsOfflineOnly':True,'noThresholdSweep':True,'noModelSweep':True,'noDreamFill':True,'no8781Change':True,'noLiveR3Change':True,'noNewEchtgeld':True}}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'cohort':rep['cohort'],'baseline':b,'candidate':f,'delta':delta},ensure_ascii=False))
if __name__=='__main__':main()
