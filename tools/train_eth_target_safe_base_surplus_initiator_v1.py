from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,mean_pinball_loss

SEED=20260901

def clsmet(y,p,th=.5):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=th).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,'predPositiveRate':float(z.mean())}

def best_balanced_threshold(y,p):
 qs=np.unique(np.quantile(p,np.linspace(.05,.95,37)));best=(.5,-1)
 for th in qs:
  v=balanced_accuracy_score(y,(p>=th).astype(int)) if len(np.unique(y))>1 else 0
  if v>best[1]:best=(float(th),float(v))
 return best[0]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--max-markets',type=int,default=0);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 d=np.load(a.dataset);X=d['X'];Y=d['Y'];mid=d['market_id'];ends=d['end_ms'];rel=d['relation'];meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));F=meta['features'];ix={k:i for i,k in enumerate(F)}
 markets=sorted(set(map(int,mid.tolist())),key=lambda m:int(np.min(ends[mid==m])))
 if a.max_markets>0:
  keep=set(markets[-a.max_markets:]);k=np.isin(mid,list(keep));X=X[k];Y=Y[k];mid=mid[k];ends=ends[k];rel=rel[k];markets=[m for m in markets if m in keep]
 mask=(rel==0)&(X[:,ix['floor_ratio']]>=0)
 X=X[mask];Y=Y[mask];mid=mid[mask];ends=ends[mask]
 balance_names=['seconds_left','pair_coverage','absnet_ratio','gross_log','candidate_qty_log']
 economic_names=list(F)
 bi=[ix[k] for k in balance_names];ei=[ix[k] for k in economic_names]
 # price model excludes target/price-derived fields
 exclude_price={'candidate_price','candidate_notional_ratio','candidate_pair_sum','pair_edge','projected_floor_delta_ratio','projected_best_delta_ratio'}
 price_names=[f for f in F if f not in exclude_price];pi=[ix[f] for f in price_names]
 n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'calibration':set(markets[a1:a2]),'test':set(markets[a2:])};sm={k:np.isin(mid,list(v)) for k,v in sets.items()}
 tasks={};mods={}
 for name,j in [('durable_safe_surplus_30s',0),('safe_surplus_60s',1)]:
  tr=np.where(sm['train']&np.isfinite(Y[:,j]))[0];ca=np.where(sm['calibration']&np.isfinite(Y[:,j]))[0];te=np.where(sm['test']&np.isfinite(Y[:,j]))[0];yt=Y[tr,j].astype(int);yc=Y[ca,j].astype(int);yv=Y[te,j].astype(int)
  o={'counts':{'train':len(tr),'calibration':len(ca),'test':len(te)},'testPositive':int(yv.sum())}
  if min(len(tr),len(ca),len(te))<30 or len(np.unique(yt))<2 or len(np.unique(yc))<2 or len(np.unique(yv))<2:
   o['status']='INSUFFICIENT_SUPPORT';tasks[name]=o;continue
  for vn,cols in [('balanceOnly',bi),('economicFull',ei)]:
   m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=25,l2_regularization=3.0,class_weight='balanced',random_state=SEED).fit(X[tr][:,cols],yt)
   pc=m.predict_proba(X[ca][:,cols])[:,1];th=best_balanced_threshold(yc,pc);pv=m.predict_proba(X[te][:,cols])[:,1]
   o[vn]={'calibrationThreshold':th,'calibration':clsmet(yc,pc,th),'test':clsmet(yv,pv,th)};mods[f'{name}:{vn}']={'model':m,'cols':cols,'threshold':th,'features':[F[c] for c in cols]}
  o['aucLiftEconomic']=float(o['economicFull']['test']['auc']-o['balanceOnly']['test']['auc']);tasks[name]=o
 # q80 accepted candidate price envelope
 tr=np.where(sm['train'])[0];te=np.where(sm['test'])[0];target=X[:,ix['candidate_price']];q=.8
 qr=HistGradientBoostingRegressor(loss='quantile',quantile=q,max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=25,l2_regularization=3.0,random_state=SEED).fit(X[tr][:,pi],target[tr]);pred=qr.predict(X[te][:,pi]);const=float(np.quantile(target[tr],q));b=np.full(len(te),const)
 pin=float(mean_pinball_loss(target[te],pred,alpha=q));bpin=float(mean_pinball_loss(target[te],b,alpha=q));price={'n':int(len(te)),'pinball':pin,'constantPinball':bpin,'improvementFrac':float((bpin-pin)/bpin) if bpin>0 else 0.,'coverage':float(np.mean(target[te]<=pred)),'actualQ80':float(np.quantile(target[te],q)),'predMedian':float(np.median(pred))}
 mods['candidate_price_q80']={'model':qr,'features':price_names,'q':q}
 d30=tasks.get('durable_safe_surplus_30s',{});s60=tasks.get('safe_surplus_60s',{});smoke=bool(a.max_markets)
 checks={'durableSupport':d30.get('testPositive',0)>=20,'durableAuc':bool(d30.get('economicFull',{}).get('test',{}).get('auc') is not None and d30['economicFull']['test']['auc']>=.65),'safe60Auc':bool(s60.get('economicFull',{}).get('test',{}).get('auc') is not None and s60['economicFull']['test']['auc']>=.65),'economicLift':bool(max(d30.get('aucLiftEconomic',-9),s60.get('aucLiftEconomic',-9))>=.03),'priceQ80Improves':price['improvementFrac']>0,'priceCoverage':.60<=price['coverage']<=.90,'frontierRespected':bool(int(meta.get('cutoff',0))<=1823545)}
 passed=all(checks.values())
 out={'version':'ETH_TARGET_SAFE_BASE_SURPLUS_INITIATOR_V1','researchOnly':True,'maxMarkets':a.max_markets,'rows':int(len(X)),'markets':n,'marketSplit':{k:len(v) for k,v in sets.items()},'tasks':tasks,'priceEnvelopeQ80':price,'passChecks':checks,'representationPass':passed,'runtimeIntent':'At balanced non-loss safe base, generate UP/DOWN passive candidates, cap notional by current floor reserve, require q80 accepted-price support, then rank/authorize with durable/safe60 economic belief.','boundary':['marketId<=1823545','Target actual fills only','no winner/PnL','no HFT tuning','hard floor budget remains deterministic']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'features':F,'models':mods,'representationPass':passed,'runtimeIntent':out['runtimeIntent']},mp);print(json.dumps({'ok':True,'representationPass':passed,'rows':len(X),'markets':n,'tasks':tasks,'price':price,'checks':checks},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
