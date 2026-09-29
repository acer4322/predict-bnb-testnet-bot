from __future__ import annotations
import bisect,json,math,sqlite3,zlib
from collections import defaultdict
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
GENERAL=OUT/'target_general_maker_side_hazard_v1.csv'
DATA=OUT/'target_maker_execution_hazard_v3_runtime_safe_states.csv'
ART=OUT/'target_maker_execution_hazard_v3_runtime_safe.joblib'
REPORT=OUT/'target_maker_execution_hazard_v3_runtime_safe_report.json'
FINAL_HOLDOUT_START=1787137800000
MAX_TRACK_MS=10000; MIN_REST_MS=250; SEED=20260820
BASE_FEATURES=['seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign','last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s','maker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s','up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge']
ORDER_FEATURES=['side_is_up','order_age_ms','quote_price','quote_offset_ticks','cum_depletion_qty','cum_replenish_qty','depletion_last1s_qty','replenish_last1s_qty','level_zero_seen','level_zero_last1s','pass_through_now','ask_touch_now','current_bid','current_ask','current_spread_ticks','current_bid_depth','time_since_last_depletion_ms']
FEATURES=BASE_FEATURES+ORDER_FEATURES

def dec(x): return json.loads(zlib.decompress(x).decode()) if x else None
def native_key(side,price): return ('bids',round(float(price),10)) if side=='UP' else ('asks',round(1.0-float(price),10))
def metric(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);both=len(set(y.tolist()))>1
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()),'predMean':float(p.mean()),'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}
def proxy(y,t):
 y=np.asarray(y,int);t=np.asarray(t,int);tp=int(((y==1)&(t==1)).sum());fp=int(((y==0)&(t==1)).sum());fn=int(((y==1)&(t==0)).sum());tn=len(y)-tp-fp-fn;prec=tp/(tp+fp) if tp+fp else None;rec=tp/(tp+fn) if tp+fn else None
 return {'triggerRate':float(t.mean()),'precision':prec,'recall':rec,'f1':2*prec*rec/(prec+rec) if prec is not None and rec is not None and prec+rec else None,'tp':tp,'fp':fp,'fn':fn,'tn':tn}
def top(m,n=20):
 im=list(m.term_importances());nm=list(m.term_names_);ix=sorted(range(len(im)),key=lambda i:float(im[i]),reverse=True)[:n];return [{'term':str(nm[i]),'importance':float(im[i])} for i in ix]
def sums_between(t,neg,pos,zero,lo,hi):
 a=bisect.bisect_right(t,lo);b=bisect.bisect_right(t,hi);return float(neg[b]-neg[a]),float(pos[b]-pos[a]),int(zero[b]-zero[a])

def main():
 import os
 g=pd.read_csv(GENERAL);g=g[g.market_end_ms.astype('int64')<FINAL_HOLDOUT_START].copy();items=sorted(g.groupby('market_id'),key=lambda kv:int(kv[1].market_end_ms.iloc[0]));limit=int(os.environ.get('EXEC_LIMIT_MARKETS','0') or 0);tail=str(os.environ.get('EXEC_TAIL_MARKETS','0')).lower() in {'1','true','yes'}; items=(items[-limit:] if tail else items[:limit]) if limit>0 else items
 c=sqlite3.connect(BOOK_DB);c.row_factory=sqlite3.Row;rows=[];cov=defaultdict(int)
 for mi,(mid,gg) in enumerate(items,1):
  mid=int(mid);gg=gg.sort_values('checkpoint_ms').reset_index(drop=True);cts=gg.checkpoint_ms.astype('int64').to_numpy();mend=int(gg.market_end_ms.iloc[0])
  filled=[dict(r) for r in c.execute('''select parent_id,target_side,target_price,placement_first_ms,first_target_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null and first_target_ms is not null and first_target_ms>placement_first_ms order by placement_first_ms,parent_id''',(mid,))]
  canc=[dict(r) for r in c.execute('''select candidate_id,target_side,target_price,placement_source_ms,cancel_source_ms from maker_book_inference_v21_cancel_candidates where market_id=? and confidence>=.65 and placement_source_ms is not null and cancel_source_ms is not null and cancel_source_ms>placement_source_ms order by placement_source_ms,candidate_id''',(mid,))]
  orders=[]
  for r in filled:orders.append({'id':str(r['parent_id']),'side':str(r['target_side']),'price':float(r['target_price']),'start':int(r['placement_first_ms']),'end':int(r['first_target_ms']),'fill':1,'cancel':0})
  for r in canc:orders.append({'id':str(r['candidate_id']),'side':str(r['target_side']),'price':float(r['target_price']),'start':int(r['placement_source_ms']),'end':int(r['cancel_source_ms']),'fill':0,'cancel':1})
  if not orders:continue
  keys={native_key(o['side'],o['price']) for o in orders}; ev=defaultdict(list)
  for u in c.execute('select source_timestamp_ms,changes_z from maker_book_inference_updates where market_id=? and is_checkpoint=0 and changes_z is not null order by source_timestamp_ms,id',(mid,)):
   t=int(u['source_timestamp_ms']);ch=dec(u['changes_z']) or {}
   for sd in ('bids','asks'):
    for z in ch.get(sd,[]) or []:
     key=(sd,round(float(z['price']),10))
     if key in keys:ev[key].append((t,float(z['delta']),float(z['after'])))
  idx={}
  for key,xs in ev.items():
   t=[x[0] for x in xs];neg=[0.];pos=[0.];zero=[0]
   for _,de,after in xs:neg.append(neg[-1]+(-de if de<0 else 0));pos.append(pos[-1]+(de if de>0 else 0));zero.append(zero[-1]+int(after<=1e-12))
   idx[key]=(t,neg,pos,zero)
  for o in orders:
   first_cp=o['start']+MIN_REST_MS;track_end=min(o['end'],o['start']+MAX_TRACK_MS,mend);a=bisect.bisect_left(cts,first_cp);b=bisect.bisect_left(cts,track_end);key=native_key(o['side'],o['price']);data=idx.get(key,([], [0.],[0.],[0]))
   t,neg,pos,zero=data
   for gi in range(a,b):
    cp=int(cts[gi]);gr=gg.iloc[gi];age=cp-o['start'];bid=float(gr['up_bid'] if o['side']=='UP' else gr['down_bid']);ask=float(gr['up_ask'] if o['side']=='UP' else gr['down_ask']);spread=float(gr['up_spread_ticks'] if o['side']=='UP' else gr['down_spread_ticks']);bd=float(gr['up_bid_depth'] if o['side']=='UP' else gr['down_bid_depth']);cumd,cumr,zseen=sums_between(t,neg,pos,zero,o['start'],cp);d1,r1,z1=sums_between(t,neg,pos,zero,cp-1000,cp);j=bisect.bisect_right(t,cp)-1;lastdep=math.nan
    while j>=0:
     if ev.get(key,[None])[j][1]<0:lastdep=float(cp-t[j]);break
     j-=1
    y=int(o['fill'] and cp<o['end']<=cp+1000);z={f:gr.get(f,math.nan) for f in BASE_FEATURES};z.update({'market_id':mid,'market_end_ms':mend,'checkpoint_ms':cp,'order_id':o['id'],'side':o['side'],'label_fill_next1s':y,'side_is_up':float(o['side']=='UP'),'order_age_ms':float(age),'quote_price':o['price'],'quote_offset_ticks':float((bid-o['price'])/.01),'cum_depletion_qty':cumd,'cum_replenish_qty':cumr,'depletion_last1s_qty':d1,'replenish_last1s_qty':r1,'level_zero_seen':float(zseen>0),'level_zero_last1s':float(z1>0),'pass_through_now':float(bid<o['price']-1e-9),'ask_touch_now':float(ask<=o['price']+1e-9),'current_bid':bid,'current_ask':ask,'current_spread_ticks':spread,'current_bid_depth':bd,'time_since_last_depletion_ms':lastdep,'source_is_cancel_candidate':float(o['cancel'])});rows.append(z)
   cov['filledOrders']+=int(o['fill']);cov['cancelOrders']+=int(o['cancel'])
  if mi%50==0:print(json.dumps({'progressMarkets':mi,'rows':len(rows),'filled':cov['filledOrders'],'cancel':cov['cancelOrders']}),flush=True)
 c.close();d=pd.DataFrame(rows).sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True);d.to_csv(DATA,index=False)
 mm=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=mm.market_id.astype(int).tolist();n=len(ids);a=int(n*.70);b=int(n*.85);sp={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])};parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in sp.items()};m=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=96,max_interaction_bins=32,interactions=8,outer_bags=4,learning_rate=.035,max_rounds=1600,early_stopping_rounds=80,min_samples_leaf=16,n_jobs=-2,random_state=SEED);m.fit(parts['train'][FEATURES].apply(pd.to_numeric,errors='coerce'),parts['train'].label_fill_next1s.astype(int));joblib.dump({'version':'TARGET_MAKER_EXECUTION_HAZARD_V3_RUNTIME_SAFE','features':FEATURES,'model':m,'trainingMarkets':sorted(sp['train']),'trainingMaxEndMs':int(parts['train'].market_end_ms.max()),'finalHoldoutStartMs':FINAL_HOLDOUT_START},ART)
 metrics={}
 for k,x in parts.items():
  y=x.label_fill_next1s.astype(int);p=m.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1];qc=((x.level_zero_last1s>0)|(x.pass_through_now>0)).astype(int);dp=((x.depletion_last1s_qty>0)|(x.pass_through_now>0)).astype(int);metrics[k]={'ebm':metric(y,p),'queueclearPassRecent':proxy(y,qc),'depletePassRecent':proxy(y,dp),'filledParentOnly':metric(y[x.source_is_cancel_candidate==0],p[x.source_is_cancel_candidate==0])}
 rep={'reportVersion':'TARGET_MAKER_EXECUTION_HAZARD_V3_RUNTIME_SAFE','researchOnly':True,'runtimeTargetDataAllowed':False,'goal':'Learn next-1s fill hazard for a known resting Maker order from strict-past current book/lifecycle plus quote-level public depletion history.','dataset':{'rows':len(d),'markets':int(d.market_id.nunique()),'positiveRate':float(d.label_fill_next1s.mean()),'filledOrders':cov['filledOrders'],'cancelNegativeOrders':cov['cancelOrders'],'maxTrackMs':MAX_TRACK_MS},'splitMarkets':{k:len(v) for k,v in sp.items()},'chronology':{'trainMaxEndMs':int(parts['train'].market_end_ms.max()),'testMaxEndMs':int(parts['test'].market_end_ms.max()),'reservedFinalClosedLoopStartMs':FINAL_HOLDOUT_START},'metrics':metrics,'topTerms':top(m),'artifact':str(ART),'guards':['Target fill is label only.','Cancel candidates confidence>=0.65 contribute noisy negative/censor states only.','Runtime features use own resting order + public top book + public changes at that quote level + own lifecycle analogue; teacher-source identity is excluded from FEATURES.','No winner/PnL input and no threshold sweep.','All source markets end before final start75 closed-loop holdout.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
