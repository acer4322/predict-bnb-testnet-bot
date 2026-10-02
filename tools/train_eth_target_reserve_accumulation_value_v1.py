from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import build_eth_target_favorable_pair_safe_surplus_v1 as base
SEED=20260901

def custom_labels(row,states):
 t=row['t'];f60=[s for s in states if s['t']>=t and s['t']<=t+60000]
 if not f60:f60=[row['post']]
 ready=float(any(s['floor']>=1.0-1e-9 and s['best']>1e-9 for s in f60))
 old=base._ORIG_FUTURE_LABELS(row,states) if hasattr(base,'_ORIG_FUTURE_LABELS') else (0,0,np.nan,np.nan,0,0)
 return old[0],old[1],old[2],ready,old[4],old[5]

def metric(y,p,th=None):
 y=np.asarray(y,int);p=np.asarray(p,float)
 auc=float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None;ap=float(average_precision_score(y,p)) if y.sum()>0 else None
 if th is None:
  cand=np.unique(np.quantile(p,np.linspace(.02,.98,81)))
  best=(-1,None)
  for t in cand:
   b=balanced_accuracy_score(y,p>=t)
   if b>best[0]:best=(b,float(t))
  th=best[1]
 return {'n':len(y),'positiveRate':float(y.mean()),'auc':auc,'ap':ap,'threshold':float(th),'predPositiveRate':float(np.mean(p>=th)),'balancedAccuracy':float(balanced_accuracy_score(y,p>=th))}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--cutoff',type=int,default=1823545);ap.add_argument('--max-markets',type=int,default=800);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 if not hasattr(base,'_ORIG_FUTURE_LABELS'):base._ORIG_FUTURE_LABELS=base.future_labels
 base.future_labels=custom_labels
 rows,ms=base.build(a.db,a.cutoff,a.max_markets)
 F=list(base.FEATURES)+['pre_floor_abs','floor_budget_gap_abs'];bal=list(base.BALANCE_FEATURES)+['pre_floor_abs','floor_budget_gap_abs']
 X=[];Y=[];MID=[]
 for r in rows:
  pf=float(r['pre']['floor'])
  if pf < -1e-9 or pf >= 1.0-1e-9:continue
  x=list(r['x'])+[pf,max(0.,1.0-pf)]
  X.append(x);Y.append(int(r['y'][3]));MID.append(int(r['market']))
 X=np.asarray(X,np.float32);Y=np.asarray(Y,np.int8);MID=np.asarray(MID,np.int32)
 markets=sorted(set(map(int,MID.tolist())))
 n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'calibration':set(markets[a1:a2]),'test':set(markets[a2:])};masks={k:np.isin(MID,list(v)) for k,v in sets.items()}
 fi={f:i for i,f in enumerate(F)};balcols=[fi[f] for f in bal];econcols=list(range(len(F)))
 outm={};models={}
 for name,cols in [('balanceOnly',balcols),('economicFull',econcols)]:
  tr=np.where(masks['train'])[0];ca=np.where(masks['calibration'])[0];te=np.where(masks['test'])[0]
  m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=40,l2_regularization=3.0,class_weight='balanced',random_state=SEED).fit(X[tr][:,cols],Y[tr])
  pc=m.predict_proba(X[ca][:,cols])[:,1];cal=metric(Y[ca],pc);pt=m.predict_proba(X[te][:,cols])[:,1];test=metric(Y[te],pt,cal['threshold'])
  outm[name]={'calibration':cal,'test':test};models[name]={'model':m,'cols':cols,'threshold':cal['threshold']}
 lift=(outm['economicFull']['test']['auc'] or 0)-(outm['balanceOnly']['test']['auc'] or 0)
 t=outm['economicFull']['test'];checks={'testSupport':t['n']>=150,'positiveRateHealthy':.05<=t['positiveRate']<=.80,'economicAucGe065':(t['auc'] or 0)>=.65,'economicLiftGe003':lift>=.03,'thresholdNondegenerate':.01<outm['economicFull']['calibration']['threshold']<.99,'frontierRespected':a.cutoff<=1823545}
 out={'version':'ETH_TARGET_RESERVE_ACCUMULATION_VALUE_V1','researchOnly':True,'rows':int(len(X)),'markets':len(markets),'marketSplit':{k:len(v) for k,v in sets.items()},'task':{'balanceOnly':outm['balanceOnly'],'economicFull':outm['economicFull'],'aucLiftEconomic':float(lift)},'features':F,'balanceFeatures':bal,'passChecks':checks,'representationPass':all(checks.values()),'runtimeIntent':'When 0<=floor<1 USDT, rank passive Maker formation candidates by Target reserve-ready-60s value; Repair/safety authority remains separate.','boundary':['marketId<=1823545','Target Maker actual fills only','future floor>=1 only as offline label','winner/PnL absent','no runtime authority yet']}
 rd=Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');import joblib;joblib.dump({'version':out['version'],'features':F,'models':models,'representationPass':out['representationPass'],'runtimeIntent':out['runtimeIntent']},mp);print(json.dumps({'ok':True,'representationPass':out['representationPass'],'rows':len(X),'markets':len(markets),'task':out['task'],'checks':checks},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
