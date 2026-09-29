from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,mean_absolute_error

ROLES=('PROBE_CORE','ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND');ROUTES=('PASSIVE','ACTIVE')
STATE=['secondsLeft','floor','best','absNet','coverage','gross','debtUp','debtDown','totalDebt','targetDebtForActionSide','oldestRepairProgress','oldestRepairAgeMs','responsibilityCount','liveSlots','sideLiveSlots','repairLiveSlots','expandLiveSlots','pendingCancelCount','bookImbalance','spread','sideBid','sideAsk','sideMid','dominantMid','qLadderLive','qPendingActive','sideIsDominant','sideIsWeak']
ACTION=['price','qty','priceToBid','askToPrice','pairLegal','isRepairRole','isExpandRole']
def f(x):
 try:
  z=float(x);return z if math.isfinite(z) else 0.0
 except:return 0.0
def rc(r):return [1.0 if r.get('role')==x else 0.0 for x in ROLES]+[1.0 if r.get('route')==x else 0.0 for x in ROUTES]+[1.0 if r.get('side')=='UP' else 0.0]
def X(rows,act):
 a=np.asarray([[f(r.get(k)) for k in STATE] for r in rows],float)
 if not act:return a
 b=np.asarray([[f(r.get(k)) for k in ACTION]+rc(r) for r in rows],float);return np.c_[a,b]
def load(p):
 out=[]
 with Path(p).open(encoding='utf-8') as fh:
  for line in fh:
   if line.strip():out.append(json.loads(line))
 return out
def clsfit(tr,va,label,act,seed):
 x=X(tr,act);v=X(va,act);y=np.asarray([int(r[label]) for r in tr]);yy=np.asarray([int(r[label]) for r in va])
 if len(np.unique(y))<2 or len(np.unique(yy))<2:return {'auc':None,'ba':None,'n':len(yy),'positiveSupport':int(yy.sum())}
 m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,max_leaf_nodes=23,min_samples_leaf=20,l2_regularization=2.0,random_state=seed).fit(x,y);p=m.predict_proba(v)[:,1]
 return {'auc':float(roc_auc_score(yy,p)),'ba':float(balanced_accuracy_score(yy,(p>=.5).astype(int))),'n':len(yy),'positiveSupport':int(yy.sum())}
def regfit(tr,va,label,act,seed):
 x=X(tr,act);v=X(va,act);y=np.asarray([f(r[label]) for r in tr]);yy=np.asarray([f(r[label]) for r in va]);m=HistGradientBoostingRegressor(max_iter=180,learning_rate=.06,max_leaf_nodes=23,min_samples_leaf=20,l2_regularization=2.0,random_state=seed).fit(x,y);p=np.maximum(0.,m.predict(v));return {'mae':float(mean_absolute_error(yy,p)),'n':len(yy),'actualMean':float(np.mean(yy))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--rows',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows=load(a.rows);mids=sorted({int(r['marketId']) for r in rows},key=lambda m:min(int(x['windowEndMs']) for x in rows if int(x['marketId'])==m))
 specs=[(40,15),(55,15),(70,15),(85,15)];folds=[]
 for fi,(ntr,nva) in enumerate(specs,1):
  trm=set(mids[:ntr]);vam=set(mids[ntr:ntr+nva]);tr=[r for r in rows if int(r['marketId']) in trm];va=[r for r in rows if int(r['marketId']) in vam];fr={'fold':fi,'trainMarkets':ntr,'valMarkets':len(vam),'trainRows':len(tr),'valRows':len(va),'metrics':{}}
  for label in ('anyFill3s','anyFill5s','terminal5s'):
   s=clsfit(tr,va,label,False,1000+fi);q=clsfit(tr,va,label,True,2000+fi);fr['metrics'][label]={'stateOnly':s,'stateAction':q,'aucDelta':None if s['auc'] is None or q['auc'] is None else q['auc']-s['auc'],'baDelta':None if s['ba'] is None or q['ba'] is None else q['ba']-s['ba']}
  for label in ('fillQty5s','repairPayQty5s'):
   s=regfit(tr,va,label,False,3000+fi);q=regfit(tr,va,label,True,4000+fi);fr['metrics'][label]={'stateOnly':s,'stateAction':q,'maeImprovement':s['mae']-q['mae']}
  folds.append(fr);print(json.dumps({'fold':fi,'metrics':fr['metrics']},ensure_ascii=False),flush=True)
 keys=['anyFill3s','anyFill5s','terminal5s'];summary={}
 for k in keys:
  ds=[x['metrics'][k]['aucDelta'] for x in folds if x['metrics'][k]['aucDelta'] is not None];summary[k]={'meanAucDelta':float(np.mean(ds)),'minAucDelta':float(np.min(ds)),'positiveFolds':sum(x>0 for x in ds),'folds':len(ds)}
 for k in ('fillQty5s','repairPayQty5s'):
  ds=[x['metrics'][k]['maeImprovement'] for x in folds];summary[k]={'meanMaeImprovement':float(np.mean(ds)),'minMaeImprovement':float(np.min(ds)),'positiveFolds':sum(x>0 for x in ds),'folds':len(ds)}
 stable=all(summary[k]['positiveFolds']>=3 for k in summary)
 out={'version':'MANAGEMENT_V1_WORLD_ACTION_SIGNAL_TEMPORAL_CV','date':'2026-09-07','researchOnly':True,'rows':len(rows),'markets':len(mids),'folds':folds,'summary':summary,'decision':'ACTION_SIGNAL_TEMPORALLY_STABLE' if stable else 'ACTION_SIGNAL_NOT_YET_STABLE','guards':['forward-only market blocks','current V3B rows only','winner/Target future absent','state-only versus state+action ablation','no policy authority','NEW24-B untouched']}
 p=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary,'decision':out['decision']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
