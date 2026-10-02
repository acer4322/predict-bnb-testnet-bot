from __future__ import annotations
import json, math, os, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_residual_repeat_repair_escalation_v0 import run_recovery
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
FEATURES=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty','absTrackingError','trackingError','actualMakerNet','actualCombinedGross','secondsLeft','recoveryBid','recoveryAsk','recoverySpreadTicks','pairAskSum','pairBidSum','marginalSurplusChunkAvgCost','lockedPairEdgePerShare','lastMakerFillAgeMs','lastMakerFillSideIsRecovery','directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery','futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery']

def finite(v):
 try:
  x=float(v); return x if math.isfinite(x) else np.nan
 except: return np.nan

def mat(rows):
 return np.asarray([[finite((r.get('features') or {}).get(k)) for k in FEATURES] for r in rows],float)

def fit_model():
 rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
 rows=[r for r in rows if r.get('deltaPnlDiagnostic') is not None and math.isfinite(float(r['deltaPnlDiagnostic'])) and abs(float(r['deltaPnlDiagnostic']))>1e-12]
 rows.sort(key=lambda r:int(r.get('checkpointMs') or 0)); cut=max(1,int(len(rows)*0.75)); tr,va=rows[:cut],rows[cut:]
 def new(): return Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',HistGradientBoostingClassifier(max_iter=180,max_leaf_nodes=8,l2_regularization=5,learning_rate=.05,random_state=17))])
 m=new(); ytr=np.asarray([float(r['deltaPnlDiagnostic'])>0 for r in tr],int); yv=np.asarray([float(r['deltaPnlDiagnostic'])>0 for r in va],int); m.fit(mat(tr),ytr); p=m.predict_proba(mat(va))[:,1]; pred=(p>=.5).astype(int)
 metrics={'labeled':len(rows),'train':len(tr),'validation':len(va),'trainPositiveRate':float(ytr.mean()),'validationPositiveRate':float(yv.mean()),'validationAuc':float(roc_auc_score(yv,p)) if len(set(yv))>1 else None,'validationAp':float(average_precision_score(yv,p)),'validationBalancedAccuracy':float(balanced_accuracy_score(yv,pred))}
 # refit on all older counterfactual rows; current test markets are strictly later and untouched
 mall=new(); ya=np.asarray([float(r['deltaPnlDiagnostic'])>0 for r in rows],int); mall.fit(mat(rows),ya)
 return mall,metrics

def main():
 model,metrics=fit_model(); base_files=[D/'r2_closed_loop_mpc_authority_v0_0_15.json',D/'r2_closed_loop_mpc_authority_v0_15_30.json']; base={}
 for p in base_files:
  d=json.loads(p.read_text(encoding='utf-8'))
  for r in d['rows']:
   if r.get('eligible'): base[int(r['marketId'])]=r
 mids=sorted(base)
 lo=int(os.environ.get('BATCH_LO','0')); hi=int(os.environ.get('BATCH_HI',str(len(mids)))); out=[]
 for mid in mids[lo:hi]:
  try:r=run_recovery(mid,enable_intervention=True,candidate_delay_ms=10000,passive_priority=False)
  except Exception as e: out.append({'marketId':mid,'error':repr(e)}); continue
  iv=r.get('intervention'); b=base[mid]
  if not iv:
   out.append({'marketId':mid,'eligible':False,'baselinePnl':b['baselinePnl']}); continue
  feat=dict(iv.get('features') or {}); x=np.asarray([[finite(feat.get(k)) for k in FEATURES]],float); prob=float(model.predict_proba(x)[0,1]); action=prob>=.5
  bp=float(b['baselinePnl']); rp=float(b['replacePnl']); cp=rp if action else bp
  out.append({'marketId':mid,'eligible':True,'pReplacePositivePnl':prob,'authorityReplace':bool(action),'baselinePnl':bp,'replacePnl':rp,'deltaIfReplace':rp-bp,'chosenPnl':cp,'edge':feat.get('lockedPairEdgePerShare'),'secondsLeft':feat.get('secondsLeft'),'absTrackingError':feat.get('absTrackingError')})
 elig=[r for r in out if r.get('eligible')]; act=[r for r in elig if r.get('authorityReplace')]
 agg={'markets':len(out),'eligible':len(elig),'replaceChosen':len(act),'baselinePnl':sum(r['baselinePnl'] for r in elig),'policyPnl':sum(r['chosenPnl'] for r in elig),'deltaPnl':sum(r['chosenPnl']-r['baselinePnl'] for r in elig),'replaceWins':sum(r['deltaIfReplace']>0 for r in act),'replaceLosses':sum(r['deltaIfReplace']<0 for r in act)}
 report={'version':'R2_PNL_VALUE_AUTHORITY_V0','researchOnly':True,'dreamFillAllowed':False,'teacher':'Older counterfactual deltaPnlDiagnostic > 0 only. Runtime features strict-past execution/book/flow; no winner, settlement or future state runtime input. Natural probability threshold 0.5; no PnL threshold sweep.','features':FEATURES,'modelValidation':metrics,'batch':[lo,hi],'aggregate':agg,'rows':out}
 path=D/f'r2_pnl_value_authority_v0_{lo}_{hi}.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'report':str(path),'modelValidation':metrics,'aggregate':agg,'acted':act},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
