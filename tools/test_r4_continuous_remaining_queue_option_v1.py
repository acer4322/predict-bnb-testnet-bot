from __future__ import annotations
import json, math, sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
from scipy.stats import spearmanr
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_queue_option_counterfactual_v1 import run_recovery

SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_option_counterfactual_v1.json'
QPROG=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_progress_proxy_v0.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_continuous_remaining_queue_option_v1.json'
CACHE=ROOT/'data/research/r4_v0/hourly/r4_continuous_remaining_queue_option_v1_labels.json'
TZ=ZoneInfo('Asia/Taipei')
BASE=['log1p_orderAgeMs','quoteOffsetTicks','partialFillRatio','recoveryDeficit','secondsLeft']
INC=['initialVisibleDepth','currentVisibleDepth','depletionOverInitial','netDepletionOverInitial']

def finite(x):
 try: return math.isfinite(float(x))
 except: return False

def metrics(y,p):
 y=np.asarray(y,float);p=np.clip(np.asarray(p,float),0,1)
 rho=spearmanr(y,p).statistic if len(y)>=3 and len(set(np.round(y,10)))>1 else float('nan')
 return {'rows':int(len(y)),'positiveRows':int(np.sum(y>1e-12)),'meanActual':float(np.mean(y)) if len(y) else None,'meanPred':float(np.mean(p)) if len(y) else None,'spearman':float(rho) if math.isfinite(float(rho)) else None,'mae':float(mean_absolute_error(y,p)) if len(y) else None,'absMeanCalibrationBias':float(abs(np.mean(p)-np.mean(y))) if len(y) else None}

def main():
 src=json.loads(SRC.read_text(encoding='utf-8')); qp=json.loads(QPROG.read_text(encoding='utf-8'))
 qmap={int(r['marketId']):r for r in qp.get('rows',[])}
 eligible=sorted([r for r in src['rows'] if r.get('eligible') and r.get('queueFeatures') and int(r['marketId']) in qmap],key=lambda r:int(r['candidateAtMs']))
 label_rows=[]
 for i,r in enumerate(eligible,1):
  mid=int(r['marketId']); rr=run_recovery(mid,queue_reinsert=False); lab=rr.get('candidateChildKeepLabels') or {}; f=rr.get('candidateQueueFeatures') or r.get('queueFeatures') or {}; q=qmap[mid]
  rem=float(f.get('remainingQty') or 0.0); fill5=float(lab.get('fillShares5s') or 0.0)
  if rem<=1e-9: continue
  vals={
   'marketId':mid,'candidateAtMs':int(rr.get('candidateAtMs') or r['candidateAtMs']),'realizedFraction5s':min(1.0,max(0.0,fill5/rem)),'fillShares5s':fill5,'remainingQty':rem,
   'log1p_orderAgeMs':math.log1p(max(0.0,float(f.get('orderAgeMs') or 0.0))),
   'quoteOffsetTicks':float(f.get('quoteOffsetTicks') or 0.0),'partialFillRatio':float(f.get('partialFillRatio') or 0.0),'recoveryDeficit':float(f.get('recoveryDeficit') or 0.0),'secondsLeft':float(f.get('secondsLeft') or 0.0),
   'initialVisibleDepth':q.get('initialVisibleDepth'),'currentVisibleDepth':q.get('currentVisibleDepth'),'depletionOverInitial':q.get('depletionOverInitial'),'netDepletionOverInitial':q.get('netDepletionOverInitial')
  }
  if all(finite(vals[k]) for k in BASE+INC): label_rows.append(vals)
  print(json.dumps({'progress':i,'marketId':mid,'fillShares5s':fill5,'remainingQty':rem,'fraction':vals['realizedFraction5s']},ensure_ascii=False),flush=True)
 CACHE.write_text(json.dumps({'version':'R4_CONTINUOUS_REMAINING_QUEUE_OPTION_LABEL_CACHE_V1','rows':label_rows},indent=2),encoding='utf-8')
 rows=sorted(label_rows,key=lambda z:z['candidateAtMs']); tr=rows[:20]; te=rows[20:]
 def fit(feats):
  if len(tr)<10 or len(te)<1:return None
  X=np.asarray([[r[k] for k in feats] for r in tr],float); y=np.asarray([r['realizedFraction5s'] for r in tr],float)
  Xt=np.asarray([[r[k] for k in feats] for r in te],float); yt=np.asarray([r['realizedFraction5s'] for r in te],float)
  m=make_pipeline(StandardScaler(),Ridge(alpha=1.0)).fit(X,y); p=np.clip(m.predict(Xt),0,1)
  return metrics(yt,p)
 b=fit(BASE); f=fit(BASE+INC)
 support=bool(len(te)>=10 and sum(r['realizedFraction5s']>1e-12 for r in te)>=3 and b and f and b['spearman'] is not None and f['spearman'] is not None)
 delta=None;keep=False
 if support:
  delta={'spearmanLift':f['spearman']-b['spearman'],'maeImprovement':b['mae']-f['mae'],'calibrationBiasImprovement':b['absMeanCalibrationBias']-f['absMeanCalibrationBias']}
  keep=bool(f['spearman']>=.30 and delta['spearmanLift']>=.10 and delta['maeImprovement']>=0 and delta['calibrationBiasImprovement']>=0)
 status='TESTED_KEEP_SIGNAL' if keep else ('TESTED_REJECTED' if support else 'TESTED_INCONCLUSIVE')
 rep={'version':'R4_CONTINUOUS_REMAINING_QUEUE_OPTION_V1','createdAt':datetime.now(TZ).isoformat(),'status':status,
 'semanticNovelty':'Continuous 5s realized remaining-share fraction of the existing resting recovery child; unlike prior binary KEEP-vs-reinsert joint-quality classification, this estimates the execution resource itself and compares calibrated continuous prediction.',
 'layerAssignment':{'queueDepthDepletion':'EXECUTION_INFORMATION','remainingExecutableShares5s':'EXECUTION_LIFECYCLE_BELIEF_CANDIDATE','portfolioResponsibility':'LOGIC_CONTEXT','authority':'NOT_ACTION_AUTHORITY'},
 'cohort':{'source':'existing eligible R2 queue-option realistic-HFT markets; KEEP branch rerun only','eligibleSourceRows':len(eligible),'reproducedRows':len(rows),'trainRows':len(tr),'holdoutRows':len(te),'holdoutPositiveRows':sum(r['realizedFraction5s']>1e-12 for r in te),'special20260816Sealed':True,'echtgeldTraining':False},
 'target':'candidateChildKeepLabels.fillShares5s / candidateQueueFeatures.remainingQty','features':{'baseline':BASE,'increment':INC},'model':'StandardScaler + Ridge(alpha=1.0), clipped [0,1]','baseline':b,'candidate':f,'delta':delta,'keepRulePass':keep,
 'guards':{'researchOnly':True,'futureFillLabelsOfflineOnly':True,'noActionAuthority':True,'noThresholdSweep':True,'noModelSweep':True,'noDreamFill':True,'no8781Change':True,'noLiveR3Change':True,'noNewEchtgeld':True}}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'cohort':rep['cohort'],'baseline':b,'candidate':f,'delta':delta},ensure_ascii=False))
if __name__=='__main__':main()
