from __future__ import annotations
import json, math, sqlite3, sys, zlib
from collections import deque, defaultdict
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import build_eth_target_teacher_policy_v1_dataset as base
BOOK=ROOT/'data/wallet_maker_book_inference_eth5m.db'; SNAP=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'; PLAC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_eventclock_v3'
BLOCK={1813144,1813392,1813399,1813418,1813430,1813446,1813454,1813487,1813742,1813750,1813874,1814101,1814124,1814127,1814134,1814493,1814497,1814543,1815055,1815060,1815063,1815143,1815150,1815155,1815163,1815246}
HORIZON=500
EXTRA=['update_add_qty','update_cut_qty','update_bid_add_qty','update_ask_add_qty','update_bid_cut_qty','update_ask_cut_qty','update_level_changes','updates_250ms','updates_1s','add_qty_250ms','cut_qty_250ms','add_qty_1s','cut_qty_1s','up_bid_d250','up_ask_d250','up_bid_depth_d250','up_ask_depth_d250','imbalance_d250','up_bid_d1','up_ask_d1','up_bid_depth_d1','up_ask_depth_d1','imbalance_d1','last_placement_age_ms','last_placement_side_up','last_placement_qty','placement_events_2s','placement_events_10s']
FEATURES=base.FEATURES+EXTRA

def dec(b):return json.loads(zlib.decompress(b).decode()) if b else None

def prior(hist,t,h,key):
    vals=[x for x in hist if x[0]<=t-h]
    return float(vals[-1][1].get(key,0.0)) if vals else float(hist[0][1].get(key,0.0)) if hist else 0.0

def main():
    OUT.mkdir(parents=True,exist_ok=True); src=json.load(open(PLAC,encoding='utf-8'))['rows']; raw=[r for r in src if r.get('highConfidencePlacement') and int(r['marketId']) not in BLOCK and r.get('placementCarrierReadyMs') is not None]
    bym=defaultdict(list)
    for r in raw:bym[int(r['marketId'])].append(dict(r))
    b=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True); b.row_factory=sqlite3.Row; s=sqlite3.connect(f'file:{SNAP.resolve().as_posix()}?mode=ro',uri=True); s.row_factory=sqlite3.Row
    X=[];yh=[];ys=[];yw=[];yo=[];yq=[];mids=[];times=[]; positives=0
    for mi,mid in enumerate(sorted(bym),1):
        updates=list(b.execute('select id,source_timestamp_ms,received_at_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by received_at_ms,id',(mid,)))
        if not updates:continue
        source_to_recv={}
        for r in updates: source_to_recv[int(r['source_timestamp_ms'])]=min(int(r['received_at_ms']),source_to_recv.get(int(r['source_timestamp_ms']),10**30))
        pls=[]
        for r in bym[mid]:
            sr=int(r['placementCarrierReadyMs']); recv=source_to_recv.get(sr)
            if recv is None:
                cand=[(abs(k-sr),v) for k,v in source_to_recv.items() if abs(k-sr)<=50]
                recv=min(cand)[1] if cand else None
            if recv is None:continue
            r['placementReceivedMs']=int(recv); pls.append(r)
        pls.sort(key=lambda r:r['placementReceivedMs'])
        parents=[dict(r) for r in s.execute("select role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where asset='ETH' and market_id=? and first_event_ms is not null order by first_event_ms,parent_id",(mid,))]
        endrow=b.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone(); end=int(endrow[0] or 0) if endrow else 0
        book={'bids':{},'asks':{}}; pi=0; up=down=cost=0.; ahist=deque(); shist=deque(); chist=deque(); phist=[]; pj=0
        for ur in updates:
            t=int(ur['received_at_ms']); source=int(ur['source_timestamp_ms'])
            if end and (end-t)/1000<=180: continue
            while pi<len(parents) and int(parents[pi]['first_event_ms'])<t:
                p=parents[pi]; sh=float(p['shares'] or 0);px=float(p['average_price'] or 0);sd=str(p['side']);role=str(p['role']); up+=sh if sd=='UP' else 0; down+=sh if sd=='DOWN' else 0; cost+=sh*px; ahist.append((int(p['first_event_ms']),role,sd,sh,px));pi+=1
            while ahist and t-ahist[0][0]>30000:ahist.popleft()
            while pj<len(pls) and int(pls[pj]['placementReceivedMs'])<t: phist.append(pls[pj]);pj+=1
            phist=[p for p in phist if t-int(p['placementReceivedMs'])<=30000]
            # apply current public update; labels are strictly after current receipt time
            curadds={'bidadd':0.,'askadd':0.,'bidcut':0.,'askcut':0.,'levels':0}
            if int(ur['is_checkpoint']):book={'bids':{float(k):float(v) for k,v in (dec(ur['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(ur['native_asks_z']) or {}).items()}}
            else:
                ch=dec(ur['changes_z']) or {}; base.apply_changes(book,ch)
                for k in ('bids','asks'):
                    for x in ch.get(k,[]) or []:
                        d=float(x.get('delta') or 0); curadds['levels']+=1
                        if d>=0:curadds['bidadd' if k=='bids' else 'askadd']+=d
                        else:curadds['bidcut' if k=='bids' else 'askcut']+=-d
            chist.append((t,curadds.copy()))
            while chist and t-chist[0][0]>1500:chist.popleft()
            bf=base.book_features(book,int(ur['order_count'] or 0))
            if bf is None:continue
            bf['seconds_left']=(end-t)/1000 if end else 999.
            f,weak=base.state_features(up,down,cost,ahist,t,pi,len(parents),bf)
            cur={'up_bid':bf['up_bid'],'up_ask':bf['up_ask'],'up_bid_depth':bf['up_bid_depth'],'up_ask_depth':bf['up_ask_depth'],'imbalance':bf['book_depth_imbalance']}
            f.update(update_add_qty=curadds['bidadd']+curadds['askadd'],update_cut_qty=curadds['bidcut']+curadds['askcut'],update_bid_add_qty=curadds['bidadd'],update_ask_add_qty=curadds['askadd'],update_bid_cut_qty=curadds['bidcut'],update_ask_cut_qty=curadds['askcut'],update_level_changes=float(curadds['levels']))
            for h,nm in [(250,'250ms'),(1000,'1s')]:
                rr=[x for x in chist if t-x[0]<=h]; f[f'updates_{nm}']=float(len(rr));f[f'add_qty_{nm}']=float(sum(x[1]['bidadd']+x[1]['askadd'] for x in rr));f[f'cut_qty_{nm}']=float(sum(x[1]['bidcut']+x[1]['askcut'] for x in rr))
                suffix='250' if h==250 else '1';f[f'up_bid_d{suffix}']=cur['up_bid']-prior(shist,t,h,'up_bid') if shist else 0.;f[f'up_ask_d{suffix}']=cur['up_ask']-prior(shist,t,h,'up_ask') if shist else 0.;f[f'up_bid_depth_d{suffix}']=cur['up_bid_depth']-prior(shist,t,h,'up_bid_depth') if shist else 0.;f[f'up_ask_depth_d{suffix}']=cur['up_ask_depth']-prior(shist,t,h,'up_ask_depth') if shist else 0.;f[f'imbalance_d{suffix}']=cur['imbalance']-prior(shist,t,h,'imbalance') if shist else 0.
            lp=phist[-1] if phist else None;f['last_placement_age_ms']=float(t-int(lp['placementReceivedMs'])) if lp else 1e6;f['last_placement_side_up']=1. if lp and lp['side']=='UP' else 0.;f['last_placement_qty']=float(lp['intentLowerBound']) if lp else 0.;f['placement_events_2s']=float(sum(t-int(x['placementReceivedMs'])<=2000 for x in phist));f['placement_events_10s']=float(sum(t-int(x['placementReceivedMs'])<=10000 for x in phist))
            fut=[p for p in pls if t<int(p['placementReceivedMs'])<=t+HORIZON]; r=fut[0] if fut else None
            if r:
                haz=1;positives+=1;sd=str(r['side']);sideup=1. if sd=='UP' else 0.;weaklab=np.nan if weak is None else (1. if sd==weak else 0.);qty=math.log1p(float(r['intentLowerBound']));cur_bid=bf['up_bid'] if sd=='UP' else bf['down_bid'];off=(float(r['targetPrice'])-float(cur_bid))/0.01
            else:haz=0;sideup=weaklab=qty=off=np.nan
            X.append([float(f.get(k,0.) or 0.) for k in FEATURES]);yh.append(haz);ys.append(sideup);yw.append(weaklab);yo.append(off);yq.append(qty);mids.append(mid);times.append(t);shist.append((t,cur));
            while shist and t-shist[0][0]>1500:shist.popleft()
        if mi%50==0:print(json.dumps({'progressMarkets':mi,'of':len(bym),'rows':len(X),'positives':positives}),flush=True)
    np.savez_compressed(OUT/'dataset.npz',X=np.asarray(X,np.float32),y_hazard=np.asarray(yh,np.int8),y_side_up=np.asarray(ys,np.float32),y_weak=np.asarray(yw,np.float32),y_offset_ticks=np.asarray(yo,np.float32),y_log_qty=np.asarray(yq,np.float32),market_id=np.asarray(mids,np.int32),timestamp_ms=np.asarray(times,np.int64))
    meta={'version':'ETH_MAKER_PLACEMENT_EVENTCLOCK_V3_DATASET','features':FEATURES,'rows':len(X),'markets':len(set(mids)),'positives':positives,'positiveRate':float(np.mean(yh)) if yh else None,'horizonMs':HORIZON,'blocked':sorted(BLOCK),'decisionClock':'maker_book_inference_updates.received_at_ms','strictPast':'current public update is available at its receipt time; label requires reconstructed placement received strictly after current receipt and <=500ms; Target acquisitions and prior placements strictly before decision time only','priceTarget':'Target Maker price minus current receipt-clock side best bid in 0.01 ticks for positive <=500ms placement states'}
    (OUT/'dataset.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta,ensure_ascii=False),flush=True);b.close();s.close()
if __name__=='__main__':main()
