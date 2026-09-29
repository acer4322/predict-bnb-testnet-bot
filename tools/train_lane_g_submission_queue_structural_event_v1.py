from __future__ import annotations
import argparse,json,math,joblib,os
from pathlib import Path
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss,balanced_accuracy_score
BASE=['secondsLeft','floor','best','absNet','coverage','gross','scopeDebtQty','obligationOutstanding','obligationRepaid','obligationProgress','representedRepairQuota','representationMargin','riskDebtOutstanding','scopeRiskCreditTotal','scopeRiskCreditConsumed','availableExpandCredit','repairProgressClocks','liveSlots','activeKeys','repairLiveSlots','expandLiveSlots','pendingCancelCount','bookImbalance','spread','sideBid','sideAsk','sideMid','sideIsDominant','sideIsWeak','price','qty','priceToBid','askToPrice','pairLegal','isRepairRole','isExpandRole','roleRepair','roleExpand','roleCore','sideUp']
QUEUE=['queueInitial','queueInitialToOrder','currentOwnLevelQty','distanceFromSameBestTicks','sameTopQty','oppositeTopQty','sameTop3Qty','oppositeTop3Qty','nativeSpreadTicks','nativeBookImbalance','sameBookLevels','oppositeBookLevels','orderCountAtAccept','ownLevelIsBest']

def safe(x):
  try:
    z=float(x); return z if math.isfinite(z) else np.nan
  except Exception:return np.nan

def enrich(r):
  z=dict(r); role=str(r.get('role') or ''); z['roleRepair']=float('REPAIR' in role); z['roleExpand']=float('EXPAND' in role); z['roleCore']=float(role=='ECONOMIC_CORE'); z['sideUp']=float(str(r.get('side'))=='UP'); return z

def X(rows,fs): return np.asarray([[safe(r.get(f)) for f in fs] for r in rows],float)
def models():
  return {
   'LOGISTIC':Pipeline([('imp',SimpleImputer(strategy='median',keep_empty_features=True)),('sc',StandardScaler()),('m',LogisticRegression(C=1.0,class_weight='balanced',max_iter=2000,random_state=20260907))]),
   'EXTRA_TREES':Pipeline([('imp',SimpleImputer(strategy='median',keep_empty_features=True)),('m',ExtraTreesClassifier(n_estimators=300,min_samples_leaf=15,max_features='sqrt',class_weight='balanced',random_state=20260907,n_jobs=1))])}

def metric(y,p,mids,prior,other=None):
  y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-6,1-1e-6); pp=np.full(len(y),float(prior)); mids=np.asarray(mids,int)
  o={'n':len(y),'positive':int(y.sum()),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'logloss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p)),'priorLogloss':float(log_loss(y,pp,labels=[0,1])),'priorBrier':float(brier_score_loss(y,pp)),'balancedAccuracyAt05':float(balanced_accuracy_score(y,(p>=.5).astype(int))) if len(np.unique(y))>1 else None}
  o['loglossImprovementVsPrior']=o['priorLogloss']-o['logloss']; o['brierImprovementVsPrior']=o['priorBrier']-o['brier']; details=[]; wins=0
  for m in sorted(set(mids)):
    ix=np.where(mids==m)[0]; ll=float(log_loss(y[ix],p[ix],labels=[0,1])); d={'marketId':int(m),'n':len(ix),'logloss':ll}
    if other is not None:
      op=np.clip(np.asarray(other,float)[ix],1e-6,1-1e-6); bl=float(log_loss(y[ix],op,labels=[0,1])); d['baselineLogloss']=bl; d['winVsBaseline']=bool(ll<bl-1e-12); wins+=int(d['winVsBaseline'])
    details.append(d)
  o['marketDetails']=details
  if other is not None:o['marketWinsVsBaseline']=wins
  return o

def main():
  ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-output',required=True);a=ap.parse_args()
  d=json.loads(Path(a.source).read_text(encoding='utf-8')); rows=[enrich(r) for r in d['rows']]; mids=[]
  for r in rows:
    m=int(r['marketId']);
    if m not in mids:mids.append(m)
  # Preserve chronological order encoded by source corpus rows / market windows.
  cut=max(1,min(len(mids)-1,int(round(len(mids)*.70)))); trm=set(mids[:cut]); tem=set(mids[cut:]); tr=[r for r in rows if int(r['marketId']) in trm]; te=[r for r in rows if int(r['marketId']) in tem]
  ytr=np.asarray([int(r['fillFirst']) for r in tr]); yte=np.asarray([int(r['fillFirst']) for r in te]); mt=np.asarray([int(r['marketId']) for r in te]); prior=float(ytr.mean()); fsmap={'BASE':BASE,'BASE_QUEUE':BASE+QUEUE}; pred={}; fitted={}; metrics={}
  for name,md in models().items():
    pred[name]={}; fitted[name]={}
    for fsn,fs in fsmap.items():
      m=md if fsn=='BASE' else models()[name]; m.fit(X(tr,fs),ytr); pred[name][fsn]=m.predict_proba(X(te,fs))[:,1]; fitted[name][fsn]=m
    b=metric(yte,pred[name]['BASE'],mt,prior); q=metric(yte,pred[name]['BASE_QUEUE'],mt,prior,pred[name]['BASE']); metrics[name]={'BASE':b,'BASE_QUEUE':q,'delta':{'auc':None if b['auc'] is None or q['auc'] is None else q['auc']-b['auc'],'logloss':b['logloss']-q['logloss'],'brier':b['brier']-q['brier'],'marketWins':q.get('marketWinsVsBaseline',0)}}
  gates={}
  for n,z in metrics.items():
    dlt=z['delta']; gates[n]={'overallImproves':bool((dlt['auc'] or -9)>0 and dlt['logloss']>0 and dlt['brier']>0),'market21of30':bool(dlt['marketWins']>=21),'pass':bool((dlt['auc'] or -9)>0 and dlt['logloss']>0 and dlt['brier']>0 and dlt['marketWins']>=21)}
  out={'version':'LANE_G_SUBMISSION_QUEUE_STRUCTURAL_EVENT_MODEL_V1_20260907','researchOnly':True,'runtimeAuthority':False,'coverage':{'rows':len(rows),'markets':len(mids),'trainMarkets':len(trm),'validationMarkets':len(tem),'trainRows':len(tr),'validationRows':len(te),'trainFillRate':prior,'validationFillRate':float(yte.mean())},'split':{'trainMarketIds':mids[:cut],'validationMarketIds':mids[cut:]},'features':fsmap,'metrics':metrics,'gates':gates,'submissionQueuePass':any(v['pass'] for v in gates.values()),'boundary':['same chronological 70/30 H100 split','exchange-acceptance strict-past features only','no post-accept trajectory','no fixed decision window','no winner/terminal PnL/Target future','no threshold sweep','consumed only/no fresh/no 8781']}
  op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8'); mp=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'submission_queue_models.joblib') if a.model_output.upper()=='AUTO' else Path(a.model_output); joblib.dump({'version':out['version'],'features':fsmap,'models':fitted,'trainPrior':prior,'split':out['split']},mp)
  compact={n:{'BASE':{k:v for k,v in z['BASE'].items() if k!='marketDetails'},'BASE_QUEUE':{k:v for k,v in z['BASE_QUEUE'].items() if k!='marketDetails'},'delta':z['delta']} for n,z in metrics.items()}
  print(json.dumps({'ok':True,'coverage':out['coverage'],'metrics':compact,'gates':gates,'submissionQueuePass':out['submissionQueuePass']},indent=2,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
