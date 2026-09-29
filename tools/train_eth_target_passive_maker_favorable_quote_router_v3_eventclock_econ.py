from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=.5).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracyAt05':float(balanced_accuracy_score(y,z))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--max-markets',type=int,default=0);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 d=np.load(a.dataset);X=d['X'];yh=d['y_hazard'].astype(int);ys=d['y_side_up'];mid=d['market_id'];ts=d['timestamp_ms'];meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));F=meta['features'];ix={x:i for i,x in enumerate(F)}
 markets=sorted(set(map(int,mid.tolist())),key=lambda m:int(ts[mid==m].min()))
 if a.max_markets>0:
  sm=set(markets[-a.max_markets:]);k=np.isin(mid,list(sm));X=X[k];yh=yh[k];ys=ys[k];mid=mid[k];ts=ts[k];markets=[m for m in markets if m in sm]
 # source builder already filters seconds_left>180; retain explicit safety check
 k=X[:,ix['seconds_left']]>180;X=X[k];yh=yh[k];ys=ys[k];mid=mid[k];ts=ts[k]
 weak_up=X[:,ix['weak_side_up']]>0;nonflat=np.abs(X[:,ix['weak_side_up']])>.5;posfloor=X[:,ix['floor']]>=0
 maker_side_up=np.where(np.isfinite(ys),ys>=.5,False);weak_h=((yh==1)&np.isfinite(ys)&(maker_side_up==weak_up)).astype(np.int8);dom_h=((yh==1)&np.isfinite(ys)&(maker_side_up!=weak_up)).astype(np.int8)
 avg_dom=np.where(weak_up,X[:,ix['avg_cost_down']],X[:,ix['avg_cost_up']]);mpair=avg_dom+X[:,ix['weak_bid']];medge=1.0-mpair;X2=np.column_stack([X,mpair,medge]).astype(np.float32);F2=F+['weak_maker_pair_sum_bid','weak_maker_pair_edge_bid'];ix2={x:i for i,x in enumerate(F2)}
 # baseline intentionally includes queue/depth/update/placement history but excludes price economics/cost/floor.
 base_names=['seconds_left','gross_shares','abs_net','imbalance_ratio','pair_coverage','weak_gap','book_order_count','book_depth_imbalance','up_bid_depth','up_ask_depth','up_top3_bid_depth','up_top3_ask_depth','last_action_age_s','maker_events_5s','taker_events_5s','maker_events_15s','taker_events_15s','maker_shares_10s','taker_shares_10s','events_seen_scaled','update_add_qty','update_cut_qty','update_bid_add_qty','update_ask_add_qty','update_bid_cut_qty','update_ask_cut_qty','update_level_changes','updates_250ms','updates_1s','add_qty_250ms','cut_qty_250ms','add_qty_1s','cut_qty_1s','up_bid_depth_d250','up_ask_depth_d250','imbalance_d250','up_bid_depth_d1','up_ask_depth_d1','imbalance_d1','last_placement_age_ms','last_placement_qty','placement_events_2s','placement_events_10s']
 econ_add=['floor','best_pnl','avg_cost_up','avg_cost_down','weak_bid','weak_ask','weak_spread','dom_bid','dom_ask','marginal_pair_sum_weak','projected_floor_delta_weak_1','projected_floor_delta_dom_1','weak_maker_pair_sum_bid','weak_maker_pair_edge_bid']
 bn=[x for x in base_names if x in ix2];en=bn+[x for x in econ_add if x in ix2];bi=[ix2[x] for x in bn];ei=[ix2[x] for x in en]
 n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'validation':set(markets[a1:a2]),'test':set(markets[a2:])};mtrain=np.isin(mid,list(sets['train']));mtest=np.isin(mid,list(sets['test']))
 tasks={'weak_now_placement_hazard_500ms':(weak_h,nonflat),'dominant_now_placement_hazard_500ms_positive_floor':(dom_h,nonflat&posfloor),'any_placement_hazard_500ms':(yh,np.ones(len(yh),bool))};res={};mods={}
 for name,(y,mask) in tasks.items():
  it=np.where(mtrain&mask)[0];iv=np.where(mtest&mask)[0];yt=y[it];yv=y[iv];o={}
  if len(it)<300 or len(iv)<100 or len(np.unique(yt))<2 or len(np.unique(yv))<2:res[name]={'status':'INSUFFICIENT_SUPPORT','trainN':int(len(it)),'testN':int(len(iv)),'testPositive':int(yv.sum())};continue
  for vn,cols in [('microBalance',bi),('microPlusEconomics',ei)]:
   m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=60,l2_regularization=4.0,class_weight='balanced',random_state=20260901);m.fit(X2[it][:,cols],yt);p=m.predict_proba(X2[iv][:,cols])[:,1];o[vn]=met(yv,p);mods[f'{name}:{vn}']=(m,cols)
  o['aucLiftEconomics']=float(o['microPlusEconomics']['auc']-o['microBalance']['auc']);res[name]=o
 w=res.get('weak_now_placement_hazard_500ms',{});d0=res.get('dominant_now_placement_hazard_500ms_positive_floor',{});aa=res.get('any_placement_hazard_500ms',{})
 checks={'weakAucGe068LiftGe002':bool(w.get('microPlusEconomics',{}).get('auc') is not None and w['microPlusEconomics']['auc']>=.68 and (w.get('aucLiftEconomics') or -9)>=.02),'dominantAucGe062LiftGe0015':bool(d0.get('microPlusEconomics',{}).get('auc') is not None and d0['microPlusEconomics']['auc']>=.62 and (d0.get('aucLiftEconomics') or -9)>=.015),'anyAucGe072LiftGe001':bool(aa.get('microPlusEconomics',{}).get('auc') is not None and aa['microPlusEconomics']['auc']>=.72 and (aa.get('aucLiftEconomics') or -9)>=.01)}
 out={'version':'ETH_TARGET_PASSIVE_MAKER_FAVORABLE_QUOTE_ROUTER_V3_EVENTCLOCK_ECON','researchOnly':True,'maxMarkets':a.max_markets,'rows':int(len(X2)),'markets':len(markets),'marketSplit':{k:len(v) for k,v in sets.items()},'featureSets':{'microBalance':bn,'microPlusEconomics':en},'tasks':res,'passChecks':checks,'representationPass':bool(all(checks.values())),'boundary':['receipt-clock current update','placement strictly future <=500ms label','source seconds_left>180','microstructure baseline already strong; treatment isolates economics increment','no winner/PnL/future price','no runtime authority']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'allFeatures':F2,'featureSets':out['featureSets'],'models':mods,'representationPass':out['representationPass']},mp);print(json.dumps({'ok':True,'representationPass':out['representationPass'],'rows':len(X2),'markets':len(markets),'tasks':res,'checks':checks,'output':str(op)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
