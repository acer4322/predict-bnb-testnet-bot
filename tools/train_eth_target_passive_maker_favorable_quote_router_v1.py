from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracyAt05':float(balanced_accuracy_score(y,pred))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--output');ap.add_argument('--model-out');ap.add_argument('--max-markets',type=int,default=0);a=ap.parse_args()
 d=np.load(a.dataset);X=d['X'];role=d['y_role'];weak=d['y_weak'];mid=d['market_id'];ts=d['timestamp_ms'];meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));F=meta['features'];ix={x:i for i,x in enumerate(F)};dev=set(map(int,meta.get('dev20ExcludedFromFormalTrainingByTrainer',[])))
 keep=np.asarray([(int(m) not in dev) and X[i,ix['seconds_left']]>180.0 for i,m in enumerate(mid)],bool);inds=np.where(keep)[0];X=X[inds];role=role[inds];weak=weak[inds];mid=mid[inds];ts=ts[inds]
 if a.max_markets>0:
  sm=sorted(set(map(int,mid.tolist())))[-a.max_markets:]; mk=np.isin(mid,sm);X=X[mk];role=role[mk];weak=weak[mk];mid=mid[mk];ts=ts[mk]
 # proposal economics at current passive best bid
 weak_is_up=X[:,ix['weak_side_up']]>0;avg_dom=np.where(weak_is_up,X[:,ix['avg_cost_down']],X[:,ix['avg_cost_up']]);mpair=avg_dom+X[:,ix['weak_bid']];medge=1.0-mpair
 X2=np.column_stack([X,mpair,medge]).astype(np.float32);F2=F+['weak_maker_pair_sum_bid','weak_maker_pair_edge_bid'];ix2={x:i for i,x in enumerate(F2)}
 n=len(X2);yw=np.zeros(n,np.int8);yd=np.zeros(n,np.int8);ya=np.zeros(n,np.int8)
 # market/time sorted already; label future current..+5s action occurrence
 by={}
 for i,m in enumerate(mid):by.setdefault(int(m),[]).append(i)
 for ids in by.values():
  j2=0
  for j,i in enumerate(ids):
   if j2<j:j2=j
   while j2+1<len(ids) and ts[ids[j2+1]]<=ts[i]+5000:j2+=1
   z=ids[j:j2+1];rm=role[z];ww=weak[z];mk=(rm==1);ya[i]=int(np.any(mk));yw[i]=int(np.any(mk & np.isfinite(ww) & (ww>=.5)));yd[i]=int(np.any(mk & np.isfinite(ww) & (ww<.5)))
 balance_names=['seconds_left','gross_shares','abs_net','imbalance_ratio','pair_coverage','weak_gap','last_action_age_s','maker_events_5s','taker_events_5s','maker_events_15s','taker_events_15s','maker_shares_10s','taker_shares_10s','events_seen_scaled']
 economic_names=balance_names+['floor','best_pnl','avg_cost_up','avg_cost_down','weak_bid','weak_ask','weak_spread','dom_bid','dom_ask','book_depth_imbalance','up_bid_depth','up_ask_depth','up_top3_bid_depth','up_top3_ask_depth','weak_maker_pair_sum_bid','weak_maker_pair_edge_bid']
 bi=[ix2[x] for x in balance_names];ei=[ix2[x] for x in economic_names]
 markets=sorted(set(map(int,mid.tolist())));cutm=markets[int(len(markets)*.70)];tr=mid<cutm;te=mid>=cutm;nonflat=X[:,ix['weak_gap']]>0.25;posfloor=X[:,ix['floor']]>=0
 tasks={
  'weak_maker_within5s':(yw,nonflat),
  'dominant_maker_within5s_positive_floor':(yd,nonflat&posfloor),
  'any_maker_within5s':(ya,np.ones(n,bool))
 };res={};models={}
 for tn,(y,mask) in tasks.items():
  it=np.where(tr&mask)[0];iv=np.where(te&mask)[0];yt=y[it];yv=y[iv];o={}
  if len(it)<500 or len(iv)<200 or len(np.unique(yt))<2 or len(np.unique(yv))<2:res[tn]={'status':'INSUFFICIENT_SUPPORT','trainN':int(len(it)),'testN':int(len(iv))};continue
  for name,cols in [('balanceOnly',bi),('economicQuote',ei)]:
   m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=80,l2_regularization=4.0,class_weight='balanced',random_state=20260901)
   m.fit(X2[it][:,cols],yt);p=m.predict_proba(X2[iv][:,cols])[:,1];o[name]=metric(yv,p);models[f'{tn}:{name}']=(m,cols)
  o['aucLiftEconomic']=float(o['economicQuote']['auc']-o['balanceOnly']['auc']);res[tn]=o
 w=res.get('weak_maker_within5s',{});d1=res.get('dominant_maker_within5s_positive_floor',{});aa=res.get('any_maker_within5s',{})
 checks={
  'weakAucGe065':bool(w.get('economicQuote',{}).get('auc') is not None and w['economicQuote']['auc']>=.65),
  'weakLiftGe003':bool((w.get('aucLiftEconomic') or -9)>=.03),
  'positiveFloorDominantAucGe062NonNegativeLift':bool(d1.get('economicQuote',{}).get('auc') is not None and d1['economicQuote']['auc']>=.62 and (d1.get('aucLiftEconomic') or -9)>=0),
  'anyMakerLiftNotBelowMinus001':bool((aa.get('aucLiftEconomic') or -9)>=-.01)
 }
 out={'version':'ETH_TARGET_PASSIVE_MAKER_FAVORABLE_QUOTE_ROUTER_V1','researchOnly':True,'rowsAfterFilters':int(n),'marketsAfterFilters':len(markets),'chronologyMarketCutoff':int(cutm),'trainRows':int(tr.sum()),'testRows':int(te.sum()),'features':{'balanceOnly':balance_names,'economicQuote':economic_names},'tasks':res,'passChecks':checks,'representationPass':bool(all(checks.values())),'boundary':['seconds_left>180 only','dev20 excluded','future5s Target action labels only','current strict-past book/inventory features only','no winner/PnL','no runtime authority']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'allFeatures':F2,'featureSets':out['features'],'models':models,'representationPass':out['representationPass']},mp);print(json.dumps({'ok':True,'representationPass':out['representationPass'],'rows':n,'markets':len(markets),'tasks':res,'checks':checks,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
