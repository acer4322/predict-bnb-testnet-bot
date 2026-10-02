from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_pinball_loss,mean_absolute_error

SEED=20260901
Q50=.50;Q80=.80

def stats(y,p,q):
 y=np.asarray(y,float);p=np.asarray(p,float)
 return {'n':int(len(y)),'pinball':float(mean_pinball_loss(y,p,alpha=q)),'coverage':float(np.mean(y<=p)),'mae':float(mean_absolute_error(y,p)),'actualMedian':float(np.median(y)),'predMedian':float(np.median(p)),'actualQ80':float(np.quantile(y,.8)),'predQ80':float(np.quantile(p,.8))}

def fitq(X,y,q):
 return HistGradientBoostingRegressor(loss='quantile',quantile=q,max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=3.0,random_state=SEED).fit(X,y)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--max-markets',type=int,default=0);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 d=np.load(a.dataset);X=d['X'];mid=d['market_id'];ends=d['end_ms'];rel=d['relation'];meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));F=meta['features'];ix={k:i for i,k in enumerate(F)}
 markets=sorted(set(map(int,mid.tolist())),key=lambda m:int(np.min(ends[mid==m])))
 if a.max_markets>0:
  keep=set(markets[-a.max_markets:]);k=np.isin(mid,list(keep));X=X[k];mid=mid[k];ends=ends[k];rel=rel[k];markets=[m for m in markets if m in keep]
 exclude={'candidate_price','candidate_notional_ratio','candidate_pair_sum','pair_edge','projected_floor_delta_ratio','projected_best_delta_ratio'}
 pred_names=[f for f in F if f not in exclude];cols=[ix[f] for f in pred_names]
 reserve=X[:,ix['net_pair_reserve_ratio']]
 phases={
  'baseAcquisitionRepair':((rel==1)&(reserve<=0),'candidate_pair_sum'),
  'reserveRepair':((rel==1)&(reserve>0),'candidate_pair_sum')
 }
 n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'calibration':set(markets[a1:a2]),'test':set(markets[a2:])};masks={k:np.isin(mid,list(v)) for k,v in sets.items()}
 results={};models={}
 for name,(pm,target_name) in phases.items():
  target=X[:,ix[target_name]];idx={k:np.where(pm&m)[0] for k,m in masks.items()};tr=idx['train'];te=idx['test']
  if min(len(tr),len(te))<50:
   results[name]={'status':'INSUFFICIENT_SUPPORT','counts':{k:int(len(v)) for k,v in idx.items()}};continue
  Xtr=X[tr][:,cols];Xte=X[te][:,cols];ytr=target[tr];yte=target[te]
  q50=fitq(Xtr,ytr,Q50);q80=fitq(Xtr,ytr,Q80);p50=q50.predict(Xte);p80=q80.predict(Xte)
  c50=float(np.quantile(ytr,Q50));c80=float(np.quantile(ytr,Q80));b50=np.full(len(yte),c50);b80=np.full(len(yte),c80)
  r50=stats(yte,p50,Q50);r80=stats(yte,p80,Q80);base50=stats(yte,b50,Q50);base80=stats(yte,b80,Q80)
  imp80=(base80['pinball']-r80['pinball'])/base80['pinball'] if base80['pinball']>0 else 0.;imp50=(base50['pinball']-r50['pinball'])/base50['pinball'] if base50['pinball']>0 else 0.
  results[name]={'target':target_name,'counts':{k:int(len(v)) for k,v in idx.items()},'q50':r50,'q80':r80,'constantQ50':base50,'constantQ80':base80,'q50PinballImprovementFrac':float(imp50),'q80PinballImprovementFrac':float(imp80)}
  models[name]={'q50':q50,'q80':q80,'target':target_name,'predictorFeatures':pred_names}
 base=results.get('baseAcquisitionRepair',{});reserve_r=results.get('reserveRepair',{});smoke=bool(a.max_markets)
 min_base=100 if smoke else 250;min_reserve=100 if smoke else 200
 def covok(r):return bool(r and .60<=r['coverage']<=.90)
 checks={
  'baseSupport':base.get('q80',{}).get('n',0)>=min_base,
  'reserveSupport':reserve_r.get('q80',{}).get('n',0)>=min_reserve,
  'baseQ80StrictlyImproves':bool(base.get('q80PinballImprovementFrac',-9)>0),
  'reserveQ80StrictlyImproves':bool(reserve_r.get('q80PinballImprovementFrac',-9)>0),
  'baseCoverage':covok(base.get('q80')),
  'reserveCoverage':covok(reserve_r.get('q80')),
  'frontierRespected':bool(int(meta.get('cutoff',0))<=1823545)
 }
 passed=all(checks.values())
 out={'version':'ETH_TARGET_CONDITIONAL_REPAIR_PRICE_ENVELOPE_V2','researchOnly':True,'maxMarkets':a.max_markets,'rows':int(len(X)),'markets':n,'marketSplit':{k:len(v) for k,v in sets.items()},'predictorFeatures':pred_names,'results':results,'passChecks':checks,'representationPass':bool(passed),'runtimeIntent':'Repair-only Target accepted pair-sum envelope. BASE_ACQUISITION uses net reserve<=0 q80; RESERVE_REPAIR uses net reserve>0 q80. Expand is intentionally outside this model.','boundary':['marketId<=1823545','Target actual Maker fills only','no winner/PnL','no HFT threshold tuning','safe Expand handled by separate value+placement layers']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'features':F,'predictorFeatures':pred_names,'models':models,'representationPass':passed,'runtimeIntent':out['runtimeIntent']},mp);print(json.dumps({'ok':True,'representationPass':passed,'markets':n,'rows':len(X),'results':results,'checks':checks,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
