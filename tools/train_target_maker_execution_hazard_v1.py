from __future__ import annotations
import bisect,json,math,sqlite3
from collections import defaultdict
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
GENERAL=OUT/'target_general_maker_side_hazard_v1.csv'
DATA=OUT/'target_maker_execution_hazard_v1_states.csv'
ART=OUT/'target_maker_execution_hazard_v1.joblib'
REPORT=OUT/'target_maker_execution_hazard_v1_report.json'
FINAL_HOLDOUT_START=1787137800000
MAX_TRACK_MS=10000; STEP_MS=1000; MIN_REST_MS=250; MAX_STATE_AGE=1500; SEED=20260820

BASE_FEATURES=['seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign','last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s','maker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s','up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge']
ORDER_FEATURES=['side_is_up','order_age_ms','quote_price','quote_offset_ticks','initial_level_depth','current_level_depth','depth_change_from_initial','cum_depletion_qty','cum_replenish_qty','depletion_ratio_initial','depletion_last1s_qty','replenish_last1s_qty','level_zero_seen','pass_through_now','pass_through_seen','ask_touch_now','ask_touch_seen','current_bid','current_ask','current_spread_ticks','current_bid_depth','time_since_last_depletion_ms','source_is_cancel_candidate']
FEATURES=BASE_FEATURES+ORDER_FEATURES

def dec(x):
 import zlib
 return json.loads(zlib.decompress(x).decode()) if x else None

def apply(st,ch):
 if not isinstance(ch,dict):return
 for k in ('bids','asks'):
  for z in ch.get(k,[]) or []:
   p=float(z['price']); a=float(z['after'])
   if a<=1e-12:st[k].pop(p,None)
   else:st[k][p]=a

def side_book(st,side):
 if not st['bids'] or not st['asks']:return None
 bb=max(st['bids']); ba=min(st['asks'])
 if side=='UP':
  return {'bid':bb,'ask':ba,'bid_depth':float(st['bids'].get(bb,0.0))}
 return {'bid':1.0-ba,'ask':1.0-bb,'bid_depth':float(st['asks'].get(ba,0.0))}

def native_loc(side,target_price): return ('bids',round(float(target_price),10)) if side=='UP' else ('asks',round(1.0-float(target_price),10))
def metric(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);both=len(set(y.tolist()))>1
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()),'predMean':float(p.mean()),'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}
def proxy_metrics(y,trigger):
 y=np.asarray(y,int);t=np.asarray(trigger,int);tp=int(((y==1)&(t==1)).sum());fp=int(((y==0)&(t==1)).sum());fn=int(((y==1)&(t==0)).sum());tn=len(y)-tp-fp-fn
 prec=tp/(tp+fp) if tp+fp else None;rec=tp/(tp+fn) if tp+fn else None
 return {'n':len(y),'triggerRate':float(t.mean()),'precision':prec,'recall':rec,'f1':(2*prec*rec/(prec+rec) if prec is not None and rec is not None and prec+rec else None),'tp':tp,'fp':fp,'fn':fn,'tn':tn}
def top(m,n=20):
 im=list(m.term_importances());nm=list(m.term_names_);ix=sorted(range(len(im)),key=lambda i:float(im[i]),reverse=True)[:n];return [{'term':str(nm[i]),'importance':float(im[i])} for i in ix]

def main():
 g=pd.read_csv(GENERAL); g=g[g.market_end_ms.astype('int64')<FINAL_HOLDOUT_START].copy(); groups={int(m):q.sort_values('checkpoint_ms').reset_index(drop=True) for m,q in g.groupby('market_id')}; c=sqlite3.connect(BOOK_DB);c.row_factory=sqlite3.Row; rows=[]; cov=defaultdict(int)
 items=sorted(groups.items(),key=lambda kv:int(kv[1].market_end_ms.iloc[0])); limit=int(__import__('os').environ.get('EXEC_LIMIT_MARKETS','0') or 0); items=items[:limit] if limit>0 else items
 for mi,(mid,gg) in enumerate(items,1):
  ts=gg.checkpoint_ms.astype('int64').to_numpy(); mend=int(gg.market_end_ms.iloc[0])
  filled=[dict(r) for r in c.execute('''select parent_id,target_side,target_price,placement_first_ms,first_target_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null and first_target_ms is not null and first_target_ms>placement_first_ms order by placement_first_ms,parent_id''',(mid,))]
  canc=[dict(r) for r in c.execute('''select candidate_id,target_side,target_price,placement_source_ms,cancel_source_ms,confidence from maker_book_inference_v21_cancel_candidates where market_id=? and confidence>=.65 and placement_source_ms is not null and cancel_source_ms is not null and cancel_source_ms>placement_source_ms order by placement_source_ms,candidate_id''',(mid,))]
  orders=[]
  for r in filled:orders.append({'id':str(r['parent_id']),'side':str(r['target_side']),'price':float(r['target_price']),'start':int(r['placement_first_ms']),'end':int(r['first_target_ms']),'fill':1,'cancel':0})
  for r in canc:orders.append({'id':str(r['candidate_id']),'side':str(r['target_side']),'price':float(r['target_price']),'start':int(r['placement_source_ms']),'end':int(r['cancel_source_ms']),'fill':0,'cancel':1})
  if not orders:continue
  updates=list(c.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)))
  if not updates:continue
  # Replay updates once and store lightweight state snapshots plus quote-level change events for this market.
  st={'bids':{},'asks':{}}; snaps=[]; change_events=[]
  for u in updates:
   t=int(u['source_timestamp_ms'])
   if int(u['is_checkpoint']):st={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
   else:
    ch=dec(u['changes_z']) or {}
    for k in ('bids','asks'):
     for z in ch.get(k,[]) or []:change_events.append((t,k,round(float(z['price']),10),float(z['delta']),float(z['after'])))
    apply(st,ch)
   if st['bids'] and st['asks']:snaps.append((t,dict(st['bids']),dict(st['asks'])))
  if not snaps:continue
  snap_ts=[x[0] for x in snaps]; ce_ts=[x[0] for x in change_events]
  for o in orders:
   start=o['start']; end=o['end']; track_end=min(end,mend, start+MAX_TRACK_MS)
   # initial level depth from latest book <= placement.
   j0=bisect.bisect_right(snap_ts,start)-1
   if j0<0:continue
   native_side,native_px=native_loc(o['side'],o['price']); init=float(snaps[j0][1 if native_side=='bids' else 2].get(native_px,0.0)); cum_dep=cum_rep=0.0; zero_seen=pass_seen=ask_seen=False; last_dep=None; ce_i=bisect.bisect_right(ce_ts,start)
   k=0
   while True:
    cp=start+MIN_REST_MS+k*STEP_MS
    if cp>=track_end:break
    # accumulate public changes through cp at our quote level
    while ce_i<len(change_events) and change_events[ce_i][0]<=cp:
     t,sd,px,de,after=change_events[ce_i]
     if sd==native_side and abs(px-native_px)<=1e-9:
      if de<0:cum_dep+=-de;last_dep=t
      elif de>0:cum_rep+=de
      if after<=1e-12:zero_seen=True
     ce_i+=1
    sj=bisect.bisect_right(snap_ts,cp)-1
    if sj<0:k+=1;continue
    bt,bids,asks=snaps[sj]
    if cp-bt>MAX_STATE_AGE:k+=1;continue
    bst={'bids':bids,'asks':asks}; sb=side_book(bst,o['side'])
    if sb is None:k+=1;continue
    pass_now=sb['bid']<o['price']-1e-9; ask_now=sb['ask']<=o['price']+1e-9; pass_seen=pass_seen or pass_now; ask_seen=ask_seen or ask_now
    cur=float((bids if native_side=='bids' else asks).get(native_px,0.0)); lo=bisect.bisect_right(ce_ts,cp-1000); hi=bisect.bisect_right(ce_ts,cp); d1=r1=0.0
    for t,sd,px,de,after in change_events[lo:hi]:
     if sd==native_side and abs(px-native_px)<=1e-9:
      if de<0:d1+=-de
      elif de>0:r1+=de
    gi=bisect.bisect_right(ts,cp)-1
    if gi<0 or cp-int(ts[gi])>MAX_STATE_AGE:k+=1;continue
    gr=gg.iloc[gi]; y=int(o['fill'] and cp<end<=cp+1000)
    # canceled order is negative until cancellation; filled order negatives until final pre-fill checkpoint.
    z={f:gr.get(f,math.nan) for f in BASE_FEATURES}; z.update({'market_id':mid,'market_end_ms':mend,'checkpoint_ms':cp,'order_id':o['id'],'side':o['side'],'label_fill_next1s':y,'source_is_cancel_candidate':float(o['cancel']),'side_is_up':float(o['side']=='UP'),'order_age_ms':float(cp-start),'quote_price':o['price'],'quote_offset_ticks':float((sb['bid']-o['price'])/.01),'initial_level_depth':init,'current_level_depth':cur,'depth_change_from_initial':cur-init,'cum_depletion_qty':cum_dep,'cum_replenish_qty':cum_rep,'depletion_ratio_initial':cum_dep/max(init,18.0,1e-9),'depletion_last1s_qty':d1,'replenish_last1s_qty':r1,'level_zero_seen':float(zero_seen),'pass_through_now':float(pass_now),'pass_through_seen':float(pass_seen),'ask_touch_now':float(ask_now),'ask_touch_seen':float(ask_seen),'current_bid':sb['bid'],'current_ask':sb['ask'],'current_spread_ticks':float((sb['ask']-sb['bid'])/.01),'current_bid_depth':sb['bid_depth'],'time_since_last_depletion_ms':float(cp-last_dep) if last_dep is not None else math.nan});rows.append(z);k+=1
   cov['filledOrders']+=int(o['fill']);cov['cancelOrders']+=int(o['cancel'])
  if mi%50==0:print(json.dumps({'progressMarkets':mi,'rows':len(rows),'filled':cov['filledOrders'],'cancel':cov['cancelOrders']}),flush=True)
 c.close(); d=pd.DataFrame(rows).sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True);d.to_csv(DATA,index=False)
 mm=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=mm.market_id.astype(int).tolist();n=len(ids);a=int(n*.70);b=int(n*.85);sp={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])};parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in sp.items()};m=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=96,max_interaction_bins=32,interactions=8,outer_bags=4,learning_rate=.035,max_rounds=1800,early_stopping_rounds=80,min_samples_leaf=16,n_jobs=-2,random_state=SEED);m.fit(parts['train'][FEATURES].apply(pd.to_numeric,errors='coerce'),parts['train'].label_fill_next1s.astype(int));joblib.dump({'version':'TARGET_MAKER_EXECUTION_HAZARD_V1','features':FEATURES,'model':m,'trainingMarkets':sorted(sp['train']),'trainingMaxEndMs':int(parts['train'].market_end_ms.max()),'finalHoldoutStartMs':FINAL_HOLDOUT_START},ART)
 metrics={}
 for k,x in parts.items():
  y=x.label_fill_next1s.astype(int);p=m.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1];queue=((x.level_zero_seen>0)|(x.pass_through_seen>0)).astype(int);dep=((x.cum_depletion_qty>0)|(x.pass_through_seen>0)).astype(int);metrics[k]={'ebm':metric(y,p),'queueclearPass':proxy_metrics(y,queue),'depletePass':proxy_metrics(y,dep),'filledParentOnly':metric(y[x.source_is_cancel_candidate==0],p[x.source_is_cancel_candidate==0])}
 rep={'reportVersion':'TARGET_MAKER_EXECUTION_HAZARD_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'goal':'Learn next-1s passive Maker fill hazard for a known resting own order from strict-past public queue/depletion/order-age + own/public lifecycle, replacing hand-picked ask-touch/queue proxy.','dataset':{'rows':len(d),'markets':int(d.market_id.nunique()),'positiveRate':float(d.label_fill_next1s.mean()),'filledOrders':cov['filledOrders'],'cancelNegativeOrders':cov['cancelOrders'],'maxTrackMs':MAX_TRACK_MS,'stepMs':STEP_MS},'splitMarkets':{k:len(v) for k,v in sp.items()},'chronology':{'trainMaxEndMs':int(parts['train'].market_end_ms.max()),'testMaxEndMs':int(parts['test'].market_end_ms.max()),'reservedFinalClosedLoopStartMs':FINAL_HOLDOUT_START},'metrics':metrics,'topTerms':top(m),'artifact':str(ART),'guards':['Target actual fill/cancel outcomes are labels/censor evidence only.','Runtime features require only own order state plus public book/depth changes and own portfolio/lifecycle analogues.','v2.1 cancel candidates confidence>=0.65 are noisy negative/censor evidence, not private-order ground truth.','No winner/PnL input.','No threshold sweep.','Filled parents and cancel candidates are both capped at first10s to reduce long-lifetime dominance.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
