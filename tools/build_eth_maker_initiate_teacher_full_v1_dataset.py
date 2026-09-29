from __future__ import annotations
import json,sqlite3,zlib,math,sys
from pathlib import Path
from collections import deque
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import build_eth_target_teacher_policy_v1_dataset as base
from tools.build_eth_maker_placement_teacher_pilot300_v1_dataset import BLOCKED
DB=ROOT/'data/wallet_maker_book_inference_eth5m.db'; PLAC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_full_v1.json'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_initiate_teacher_full_v1'; STEP=1000
FEATURES=['seconds_left','seconds_from_start','up_bid','up_ask','up_mid','down_bid','down_ask','down_mid','up_spread','down_spread','up_bid_depth','up_ask_depth','up_top3_bid_depth','up_top3_ask_depth','book_order_count','book_depth_imbalance','up_inside_empty_ticks','down_inside_empty_ticks','up_bid_depth_m1','up_bid_depth_m2','up_bid_depth_m3','down_bid_depth_m1','down_bid_depth_m2','down_bid_depth_m3','up_mid_delta_1s','up_mid_delta_3s','up_mid_delta_5s','up_spread_delta_1s','up_spread_delta_3s','up_spread_delta_5s','order_count_delta_1s','order_count_delta_3s','order_count_delta_5s','imbalance_delta_1s','imbalance_delta_3s','imbalance_delta_5s']
def side_depth(book,side,p):
 p=round(float(p),2); return float(book['bids'].get(p,0.)) if side=='UP' else float(book['asks'].get(round(1-p,2),0.))
def qhist(hist,t,lag,key,cur):
 target=t-lag; cand=None
 for tt,f in hist:
  if tt<=target:cand=f
  else:break
 return cur-(cand.get(key,cur) if cand else cur)
def main():
 OUT.mkdir(parents=True,exist_ok=True); j=json.loads(PLAC.read_text()); by={}
 for r in j['rows']:
  if r.get('highConfidencePlacement') and r.get('placementCarrierReadyMs') is not None:by.setdefault(int(r['marketId']),[]).append(r)
 c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 X=[];yh=[];ys=[];yo=[];yq=[];mids=[];ts=[];pos=0;eligible_pre=0;no_pre=0
 for mi,mid in enumerate(sorted(by),1):
  mr=c.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone()
  if not mr or not mr[0]:continue
  end=int(mr[0]);start=end-300000; first_acq=c.execute("select min(event_ms) from maker_book_inference_wallet_events where market_id=? and quote_type='BID' and role in ('MAKER','TAKER')",(mid,)).fetchone()[0]
  if first_acq is None:continue
  first_acq=int(first_acq); fp=min(by[mid],key=lambda r:int(r['placementCarrierReadyMs'])); place=int(fp['placementCarrierReadyMs']); preplace=place<first_acq and (end-place)/1000.0>180
  if preplace:eligible_pre+=1; stop=((place//STEP)*STEP)+STEP
  else:no_pre+=1; stop=min(((first_acq//STEP)*STEP),end-180000)
  stop=min(stop,end-180000+STEP)
  updates=list(c.execute('select source_timestamp_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)))
  book={'bids':{},'asks':{}};ui=0;oc=0;hist=deque()
  for t in range(start,min(stop,end-180000+STEP),STEP):
   if (end-t)/1000.0<=180:break
   while ui<len(updates) and int(updates[ui]['source_timestamp_ms'])<t:
    r=updates[ui]
    if int(r['is_checkpoint']):book={'bids':{float(k):float(v) for k,v in (base.dec(r['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (base.dec(r['native_asks_z']) or {}).items()}}
    else:base.apply_changes(book,base.dec(r['changes_z']) or {})
    oc=int(r['order_count'] or 0);ui+=1
   bf=base.book_features(book,oc)
   if bf is None:continue
   f=dict(bf);f['seconds_left']=(end-t)/1000.;f['seconds_from_start']=(t-start)/1000.;f['up_inside_empty_ticks']=max(0.,bf['up_spread']/.01-1);f['down_inside_empty_ticks']=max(0.,bf['down_spread']/.01-1)
   for side,prefix,bid in [('UP','up',bf['up_bid']),('DOWN','down',bf['down_bid'])]:
    for k in (1,2,3):f[f'{prefix}_bid_depth_m{k}']=side_depth(book,side,bid-.01*k)
   for lag,s in [(1000,'1s'),(3000,'3s'),(5000,'5s')]:
    f[f'up_mid_delta_{s}']=qhist(hist,t,lag,'up_mid',bf['up_mid']);f[f'up_spread_delta_{s}']=qhist(hist,t,lag,'up_spread',bf['up_spread']);f[f'order_count_delta_{s}']=qhist(hist,t,lag,'book_order_count',bf['book_order_count']);f[f'imbalance_delta_{s}']=qhist(hist,t,lag,'book_depth_imbalance',bf['book_depth_imbalance'])
   buck=(place//STEP)*STEP; p=fp if preplace and t==buck else None; hazard=1 if p else 0;sideup=offset=qty=np.nan
   if p:
    side=str(p['side']);sideup=1. if side=='UP' else 0.;bid=bf['up_bid'] if side=='UP' else bf['down_bid'];offset=(float(p['targetPrice'])-bid)/.01;qty=math.log1p(float(p['intentLowerBound']));pos+=1
   X.append([float(f.get(k,0.) or 0.) for k in FEATURES]);yh.append(hazard);ys.append(sideup);yo.append(offset);yq.append(qty);mids.append(mid);ts.append(t);hist.append((t,dict(f)))
   while hist and t-hist[0][0]>6000:hist.popleft()
  if mi%100==0:print(json.dumps({'progressMarkets':mi,'rows':len(X),'positives':pos}),flush=True)
 c.close();np.savez_compressed(OUT/'dataset.npz',X=np.asarray(X,np.float32),y_hazard=np.asarray(yh,np.int8),y_side_up=np.asarray(ys,np.float32),y_offset_ticks=np.asarray(yo,np.float32),y_log_qty=np.asarray(yq,np.float32),market_id=np.asarray(mids,np.int32),timestamp_ms=np.asarray(ts,np.int64))
 meta={'version':'ETH_MAKER_INITIATE_TEACHER_FULL_V1_DATASET','features':FEATURES,'rows':len(X),'markets':len(set(mids)),'positiveInitiations':pos,'marketsPrepositionMakerBeforeFirstAcquisition':eligible_pre,'marketsNoMakerPrepositionBeforeFirstAcquisition':no_pre,'positiveRate':pos/len(X) if X else None,'blocked':sorted(BLOCKED),'strictPast':'public book source_timestamp_ms < state second; no Target acquisition state enters inputs; positive only when reconstructed high-confidence first Maker placement occurs before first Target acquisition','safety':'seconds_left>180 only'};(OUT/'dataset.meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(meta,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
