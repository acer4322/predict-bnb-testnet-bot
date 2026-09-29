from __future__ import annotations
import json, math, sqlite3, sys, zlib
from collections import deque, defaultdict
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import build_eth_target_teacher_policy_v1_dataset as base
BOOK=ROOT/'data/wallet_maker_book_inference_eth5m.db'
SNAP=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
PLAC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_teacher_pilot300_v2'
BLOCK={1813144,1813392,1813399,1813418,1813430,1813446,1813454,1813487,1813742,1813750,1813874,1814101,1814124,1814127,1814134,1814493,1814497,1814543,1815055,1815060,1815063,1815143,1815150,1815155,1815163,1815246}
EXTRA=['up_bid_d1','up_bid_d3','up_bid_d5','up_ask_d1','up_ask_d3','up_ask_d5','up_bid_depth_d1','up_bid_depth_d3','up_bid_depth_d5','up_ask_depth_d1','up_ask_depth_d3','up_ask_depth_d5','top3_bid_d1','top3_bid_d3','top3_bid_d5','top3_ask_d1','top3_ask_d3','top3_ask_d5','order_count_d1','order_count_d3','order_count_d5','imbalance_d1','imbalance_d3','imbalance_d5','add_qty_1s','cut_qty_1s','add_qty_3s','cut_qty_3s','add_qty_5s','cut_qty_5s','bid_add_minus_ask_add_3s','bid_cut_minus_ask_cut_3s','last_placement_age_s','last_placement_side_up','last_placement_offset_ticks','last_placement_qty','placement_events_5s','placement_events_15s','same_side_placement_run']
FEATURES=base.FEATURES+EXTRA

def dec(b): return json.loads(zlib.decompress(b).decode()) if b else None

def qpast(hist,t,horizon,key):
    if not hist:return 0.0
    target=t-horizon
    prior=min(hist,key=lambda x:abs(x[0]-target))
    return float(prior[1].get(key,0.0))

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    pj=json.load(open(PLAC,encoding='utf-8')); placements=[r for r in pj['rows'] if r.get('highConfidencePlacement') and int(r['marketId']) not in BLOCK and r.get('placementCarrierReadyMs') is not None]
    bym=defaultdict(list)
    for r in placements: bym[int(r['marketId'])].append(r)
    b=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True); b.row_factory=sqlite3.Row
    s=sqlite3.connect(f'file:{SNAP.resolve().as_posix()}?mode=ro',uri=True); s.row_factory=sqlite3.Row
    X=[]; yh=[]; ys=[]; yw=[]; yo=[]; yq=[]; mids=[]; times=[]
    for mi,mid in enumerate(sorted(bym),1):
        endrow=b.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone(); end=int(endrow[0] or 0) if endrow else 0
        if not end: continue
        start=end-300000; pls=sorted(bym[mid],key=lambda r:int(r['placementCarrierReadyMs']))
        pbysec=defaultdict(list)
        for r in pls: pbysec[(int(r['placementCarrierReadyMs'])//1000)*1000].append(r)
        parents=[dict(r) for r in s.execute("select role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where asset='ETH' and market_id=? and first_event_ms is not null order by first_event_ms,parent_id",(mid,))]
        updates=list(b.execute('select source_timestamp_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)))
        book={'bids':{},'asks':{}}; ui=pi=0; up=down=cost=0.; hist=deque(); order_count=0; snap_hist=deque(); change_hist=deque(); past_pl=[]
        for t in range(start,end,1000):
            while ui<len(updates) and int(updates[ui]['source_timestamp_ms'])<t:
                r=updates[ui]; rt=int(r['source_timestamp_ms'])
                if int(r['is_checkpoint']): book={'bids':{float(k):float(v) for k,v in (dec(r['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(r['native_asks_z']) or {}).items()}}
                else:
                    ch=dec(r['changes_z']) or {}; base.apply_changes(book,ch)
                    for k,sgn in [('bids',1),('asks',-1)]:
                        for x in ch.get(k,[]) or []:
                            d=float(x.get('delta') or 0); change_hist.append((rt,k,d))
                order_count=int(r['order_count'] or 0); ui+=1
            while pi<len(parents) and int(parents[pi]['first_event_ms'])<t:
                p=parents[pi]; sh=float(p['shares'] or 0); px=float(p['average_price'] or 0); sd=str(p['side']); role=str(p['role'])
                if sd=='UP':up+=sh
                elif sd=='DOWN':down+=sh
                cost+=sh*px; hist.append((int(p['first_event_ms']),role,sd,sh,px)); pi+=1
            while hist and t-hist[0][0]>30000: hist.popleft()
            past_pl=[r for r in past_pl if t-int(r['placementCarrierReadyMs'])<=30000]
            while change_hist and t-change_hist[0][0]>6000: change_hist.popleft()
            bf=base.book_features(book,order_count)
            if bf is None: continue
            bf['seconds_left']=(end-t)/1000.0
            if bf['seconds_left']<=180: continue
            f,weak=base.state_features(up,down,cost,hist,t,pi,len(parents),bf)
            # trailing snapshot features, strictly past/current book state only
            cur={'up_bid':bf['up_bid'],'up_ask':bf['up_ask'],'up_bid_depth':bf['up_bid_depth'],'up_ask_depth':bf['up_ask_depth'],'top3_bid':bf['up_top3_bid_depth'],'top3_ask':bf['up_top3_ask_depth'],'order_count':bf['book_order_count'],'imbalance':bf['book_depth_imbalance']}
            for h,sec in [(1000,1),(3000,3),(5000,5)]:
                for src,dst in [('up_bid','up_bid'),('up_ask','up_ask'),('up_bid_depth','up_bid_depth'),('up_ask_depth','up_ask_depth'),('top3_bid','top3_bid'),('top3_ask','top3_ask'),('order_count','order_count'),('imbalance','imbalance')]: f[f'{dst}_d{sec}']=float(cur[src]-qpast(snap_hist,t,h,src)) if snap_hist else 0.0
                recent=[x for x in change_hist if t-x[0]<=h]; f[f'add_qty_{sec}s']=float(sum(max(0,x[2]) for x in recent)); f[f'cut_qty_{sec}s']=float(sum(max(0,-x[2]) for x in recent))
            rec3=[x for x in change_hist if t-x[0]<=3000]; f['bid_add_minus_ask_add_3s']=float(sum(max(0,x[2]) for x in rec3 if x[1]=='bids')-sum(max(0,x[2]) for x in rec3 if x[1]=='asks')); f['bid_cut_minus_ask_cut_3s']=float(sum(max(0,-x[2]) for x in rec3 if x[1]=='bids')-sum(max(0,-x[2]) for x in rec3 if x[1]=='asks'))
            last=past_pl[-1] if past_pl else None
            f['last_placement_age_s']=(t-int(last['placementCarrierReadyMs']))/1000 if last else 999.; f['last_placement_side_up']=1. if last and last['side']=='UP' else 0.; f['last_placement_offset_ticks']=float(last.get('offsetTicksAtPlacement') or 0.) if last else 0.; f['last_placement_qty']=float(last.get('intentLowerBound') or 0.) if last else 0.; f['placement_events_5s']=float(sum(t-int(x['placementCarrierReadyMs'])<=5000 for x in past_pl)); f['placement_events_15s']=float(sum(t-int(x['placementCarrierReadyMs'])<=15000 for x in past_pl))
            run=0
            if last:
                sd=last['side']
                for x in reversed(past_pl):
                    if x['side']==sd: run+=1
                    else: break
            f['same_side_placement_run']=float(run)
            lab=pbysec.get(t,[]); r=lab[0] if lab else None
            if r:
                side=str(r['side']); sideup=1. if side=='UP' else 0.; weaklab=np.nan if weak is None else (1. if side==weak else 0.); off=float(r.get('offsetTicksAtPlacement') or 0.); qty=math.log1p(float(r['intentLowerBound'])); haz=1
            else: haz=0; sideup=weaklab=off=qty=np.nan
            X.append([float(f.get(k,0.) or 0.) for k in FEATURES]); yh.append(haz); ys.append(sideup); yw.append(weaklab); yo.append(off); yq.append(qty); mids.append(mid); times.append(t)
            # current-second labels become history only after feature/label creation
            if lab: past_pl.extend(sorted(lab,key=lambda x:int(x['placementCarrierReadyMs'])))
            snap_hist.append((t,cur));
            while snap_hist and t-snap_hist[0][0]>6000:snap_hist.popleft()
        if mi%50==0: print(json.dumps({'progressMarkets':mi,'of':len(bym),'rows':len(X),'positives':int(sum(yh))}),flush=True)
    np.savez_compressed(OUT/'dataset.npz',X=np.asarray(X,np.float32),y_hazard=np.asarray(yh,np.int8),y_side_up=np.asarray(ys,np.float32),y_weak=np.asarray(yw,np.float32),y_offset_ticks=np.asarray(yo,np.float32),y_log_qty=np.asarray(yq,np.float32),market_id=np.asarray(mids,np.int32),timestamp_ms=np.asarray(times,np.int64))
    meta={'version':'ETH_MAKER_PLACEMENT_TEACHER_PILOT300_V2_DATASET','features':FEATURES,'rows':len(X),'markets':len(set(mids)),'placementPositives':int(sum(yh)),'positiveRate':float(np.mean(yh)) if yh else None,'blocked':sorted(BLOCK),'strictPast':'all book/churn/Target acquisition state and placement-history features are strictly before current second; current reconstructed placement is label only','newFeatures':EXTRA,'teacherForPastPlacementHistory':'Target reconstructed past placements in training; runtime must substitute own placement history'}
    (OUT/'dataset.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8'); print(json.dumps(meta,ensure_ascii=False),flush=True)
    b.close();s.close()
if __name__=='__main__':main()
