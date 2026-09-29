from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import build_eth_target_favorable_pair_safe_surplus_v1 as base
SEED=20260901

def metric(y,p,th=None):
 y=np.asarray(y,int);p=np.asarray(p,float);auc=float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None;ap=float(average_precision_score(y,p)) if y.sum()>0 else None
 if th is None:
  cand=np.unique(np.quantile(p,np.linspace(.02,.98,81)));best=(-1,.5)
  for t in cand:
   b=balanced_accuracy_score(y,p>=t)
   if b>best[0]:best=(b,float(t))
  th=best[1]
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':auc,'ap':ap,'threshold':float(th),'predPositiveRate':float(np.mean(p>=th)),'balancedAccuracy':float(balanced_accuracy_score(y,p>=th))}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--cutoff',type=int,default=1823545);ap.add_argument('--max-markets',type=int,default=800);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 rows,_=base.build(a.db,a.cutoff,a.max_markets);F=list(base.FEATURES)+['pre_floor_abs','floor_budget_gap_abs'];fi={f:i for i,f in enumerate(F)};by={}
 for r in rows:by.setdefault(int(r['market']),[]).append(r)
 X=[];Y=[];MID=[]
 for mid,rs in by.items():
  rs=sorted(rs,key=lambda z:(int(z['t']),int(z['eventIndex'])))
  for i,r in enumerate(rs):
   pf=float(r['pre']['floor'])
   if int(r['rel'])!=0 or pf < -1e-9 or pf >= 1.0-1e-9:continue
   x=list(r['x'])+[pf,max(0.,1.-pf)];side='UP' if x[base.FEATURES.index('candidate_side_up')]>=.5 else 'DOWN';p1=float(x[base.FEATURES.index('candidate_price')]);lab=0
   for z in rs[i+1:]:
    if int(z['t'])-int(r['t'])>30000:break
    zside='UP' if z['x'][base.FEATURES.index('candidate_side_up')]>=.5 else 'DOWN'
    if zside==side:continue
    p2=float(z['x'][base.FEATURES.index('candidate_price')]);cheap=(p1+p2)<1.0-1e-9;floor_up=float(z['post']['floor'])>pf+1e-9;best_pos=float(z['post']['best'])>1e-9
    if cheap and floor_up and best_pos:lab=1
    break
   X.append(x);Y.append(lab);MID.append(mid)
 X=np.asarray(X,np.float32);Y=np.asarray(Y,np.int8);MID=np.asarray(MID,np.int32);markets=sorted(set(map(int,MID.tolist())));n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'calibration':set(markets[a1:a2]),'test':set(markets[a2:])};masks={k:np.isin(MID,list(v)) for k,v in sets.items()}
 bal_names=list(base.BALANCE_FEATURES)+['pre_floor_abs','floor_budget_gap_abs'];balcols=[fi[f] for f in bal_names];econcols=list(range(len(F)));outs={};models={}
 for name,cols in [('balanceOnly',balcols),('economicFull',econcols)]:
  tr=np.where(masks['train'])[0];ca=np.where(masks['calibration'])[0];te=np.where(masks['test'])[0];m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=3.,class_weight='balanced',random_state=SEED).fit(X[tr][:,cols],Y[tr]);pc=m.predict_proba(X[ca][:,cols])[:,1];cal=metric(Y[ca],pc);pt=m.predict_proba(X[te][:,cols])[:,1];test=metric(Y[te],pt,cal['threshold']);outs[name]={'calibration':cal,'test':test};models[name]={'model':m,'cols':cols,'threshold':cal['threshold']}
 lift=(outs['economicFull']['test']['auc'] or 0)-(outs['balanceOnly']['test']['auc'] or 0);t=outs['economicFull']['test'];checks={'testSupport':t['n']>=100,'positiveRateHealthy':.03<=t['positiveRate']<=.70,'economicAucGe065':(t['auc'] or 0)>=.65,'economicLiftGe003':lift>=.03,'thresholdNondegenerate':.01<outs['economicFull']['calibration']['threshold']<.99,'frontierRespected':a.cutoff<=1823545};out={'version':'ETH_TARGET_RESERVE_PAIR_CYCLE_INITIATION_V2','researchOnly':True,'rows':int(len(X)),'markets':len(markets),'task':{'balanceOnly':outs['balanceOnly'],'economicFull':outs['economicFull'],'aucLiftEconomic':float(lift)},'features':F,'balanceFeatures':bal_names,'passChecks':checks,'representationPass':all(checks.values()),'runtimeIntent':'At balanced 0<=floor<1, score a single passive Maker first-leg candidate for probability of a later opposite-side cheap-pair cycle that increases floor. Never pre-submit the second side.','boundary':['marketId<=1823545','actual Target Maker fills only','future opposite fill used only as offline label','winner/PnL absent','no HFT tuning']};rd=Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'features':F,'models':models,'representationPass':out['representationPass'],'runtimeIntent':out['runtimeIntent']},mp);print(json.dumps({'ok':True,'representationPass':out['representationPass'],'rows':len(X),'markets':len(markets),'task':out['task'],'checks':checks},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
