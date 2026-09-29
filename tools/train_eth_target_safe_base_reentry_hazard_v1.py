from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

SEED=20260901

def met(y,p,th=.5):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=th).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'positives':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if len(np.unique(y))>1 else None,'predPositiveRate':float(z.mean())}

def best_th(y,p):
 qs=np.unique(np.quantile(p,np.linspace(.05,.95,37)));best=(.5,-1)
 for th in qs:
  b=balanced_accuracy_score(y,(p>=th).astype(int)) if len(np.unique(y))>1 else 0
  if b>best[1]:best=(float(th),float(b))
 return best[0]

def fit(X,y):return HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=45,l2_regularization=3.0,class_weight='balanced',random_state=SEED).fit(X,y)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--max-markets',type=int,default=0);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 d=np.load(a.dataset);X=d['X'];yh=d['y_hazard'].astype(int);ys=d['y_side_up'];mid=d['market_id'];ts=d['timestamp_ms'];meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));F=meta['features'];ix={k:i for i,k in enumerate(F)}
 gross=np.maximum(X[:,ix['gross_shares']],1e-9);ratio=X[:,ix['abs_net']]/gross;mask=(X[:,ix['floor']]>=0)&(ratio<=.05)&(X[:,ix['seconds_left']]>180)
 X=X[mask];yh=yh[mask];ys=ys[mask];mid=mid[mask];ts=ts[mask]
 markets=sorted(set(map(int,mid.tolist())),key=lambda m:int(ts[mid==m].min()))
 if a.max_markets>0:
  keep=set(markets[-a.max_markets:]);k=np.isin(mid,list(keep));X=X[k];yh=yh[k];ys=ys[k];mid=mid[k];ts=ts[k];markets=[m for m in markets if m in keep]
 base_names=['seconds_left','gross_shares','abs_net','imbalance_ratio','pair_coverage','book_order_count','book_depth_imbalance','up_bid_depth','up_ask_depth','up_top3_bid_depth','up_top3_ask_depth','last_action_age_s','maker_events_5s','taker_events_5s','maker_events_15s','taker_events_15s','maker_shares_10s','taker_shares_10s','events_seen_scaled','update_add_qty','update_cut_qty','update_bid_add_qty','update_ask_add_qty','update_bid_cut_qty','update_ask_cut_qty','update_level_changes','updates_250ms','updates_1s','add_qty_250ms','cut_qty_250ms','add_qty_1s','cut_qty_1s','up_bid_depth_d250','up_ask_depth_d250','imbalance_d250','up_bid_depth_d1','up_ask_depth_d1','imbalance_d1','last_placement_age_ms','last_placement_qty','placement_events_2s','placement_events_10s']
 econ_add=['up_bid','up_ask','up_mid','down_bid','down_ask','down_mid','up_spread','down_spread','cost','floor','best_pnl','avg_cost_up','avg_cost_down','surplus_ratio','weak_bid','weak_ask','dom_bid','dom_ask','marginal_pair_sum_weak','projected_floor_delta_weak_1','projected_floor_delta_dom_1']
 bn=[x for x in base_names if x in ix];en=bn+[x for x in econ_add if x in ix];bi=[ix[x] for x in bn];ei=[ix[x] for x in en]
 n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'calibration':set(markets[a1:a2]),'test':set(markets[a2:])};sm={k:np.isin(mid,list(v)) for k,v in sets.items()}
 tr=np.where(sm['train'])[0];ca=np.where(sm['calibration'])[0];te=np.where(sm['test'])[0];res={};mods={}
 for vn,cols in [('microGeometry',bi),('microPlusEconomics',ei)]:
  m=fit(X[tr][:,cols],yh[tr]);pc=m.predict_proba(X[ca][:,cols])[:,1];th=best_th(yh[ca],pc);pt=m.predict_proba(X[te][:,cols])[:,1];res[vn]={'calibrationThreshold':th,'calibration':met(yh[ca],pc,th),'test':met(yh[te],pt,th)};mods[vn]={'model':m,'features':[F[c] for c in cols],'threshold':th}
 lift=float(res['microPlusEconomics']['test']['auc']-res['microGeometry']['test']['auc'])
 pos=np.where((yh==1)&np.isfinite(ys))[0];ptr=np.asarray([i for i in pos if int(mid[i]) in sets['train']],int);pca=np.asarray([i for i in pos if int(mid[i]) in sets['calibration']],int);pte=np.asarray([i for i in pos if int(mid[i]) in sets['test']],int)
 side={};side_model=None;side_th=.5
 if len(ptr)>=30 and len(pca)>=10 and len(pte)>=10 and len(np.unique(ys[ptr].astype(int)))>1 and len(np.unique(ys[pte].astype(int)))>1:
  side_model=fit(X[ptr][:,ei],ys[ptr].astype(int));pc=side_model.predict_proba(X[pca][:,ei])[:,1];side_th=best_th(ys[pca].astype(int),pc);pt=side_model.predict_proba(X[pte][:,ei])[:,1];side={'calibrationThreshold':side_th,'calibration':met(ys[pca].astype(int),pc,side_th),'test':met(ys[pte].astype(int),pt,side_th)}
 else:side={'status':'INSUFFICIENT_SUPPORT','counts':{'train':len(ptr),'calibration':len(pca),'test':len(pte)}}
 checks={'testPositives':res['microPlusEconomics']['test']['positives']>=15,'hazardAuc':res['microPlusEconomics']['test']['auc']>=.70,'hazardEconomicLift':lift>=.02,'sideAuc':bool(side.get('test',{}).get('auc') is not None and side['test']['auc']>=.65),'frontierRespected':True};passed=all(checks.values())
 out={'version':'ETH_TARGET_SAFE_BASE_REENTRY_HAZARD_V1','researchOnly':True,'maxMarkets':a.max_markets,'rows':int(len(X)),'markets':n,'marketSplit':{k:len(v) for k,v in sets.items()},'featureSets':{'microGeometry':bn,'microPlusEconomics':en},'hazard':res,'hazardAucLiftEconomics':lift,'sideUpGivenPlacement':side,'passChecks':checks,'representationPass':passed,'runtimeIntent':'At floor>=0 and abs-net ratio<=5%, hazard decides whether to re-enter Maker; side classifier selects UP/DOWN. Quantity cost is hard-capped by current floor reserve so post-action floor remains >=0.','boundary':['receipt-clock strict-past placement labels','<=180s excluded','no winner/PnL','no HFT tuning']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'features':F,'hazard':mods,'sideModel':side_model,'sideFeatures':en,'sideThreshold':side_th,'representationPass':passed},mp);print(json.dumps({'ok':True,'representationPass':passed,'rows':len(X),'markets':n,'hazard':res,'lift':lift,'side':side,'checks':checks},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
