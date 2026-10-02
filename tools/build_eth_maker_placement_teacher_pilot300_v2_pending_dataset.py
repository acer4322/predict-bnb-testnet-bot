from __future__ import annotations
import json,math,sqlite3,sys
from pathlib import Path
from collections import deque
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import build_eth_target_teacher_policy_v1_dataset as base
from tools.build_eth_maker_placement_teacher_pilot300_v1_dataset import BLOCKED
BOOK=ROOT/'data/wallet_maker_book_inference_eth5m.db'
PLAC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json'
OUTDIR=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_teacher_pilot300_v2_pending'
STEP=1000
PENDING_FEATURES=['pending_up_qty','pending_down_qty','pending_gross_qty','pending_net_qty','pending_abs_net_qty','pending_count','pending_up_count','pending_down_count','pending_oldest_age_s','pending_newest_age_s','pending_up_at_best_qty','pending_down_at_best_qty','pending_up_mean_offset_ticks','pending_down_mean_offset_ticks','effective_up_qty','effective_down_qty','effective_abs_net','effective_pair_coverage','effective_weak_side_up','effective_weak_gap','pending_materialized_weak_qty','pending_materialized_dom_qty','up_inside_empty_ticks','down_inside_empty_ticks','up_bid_depth_m1','up_bid_depth_m2','up_bid_depth_m3','down_bid_depth_m1','down_bid_depth_m2','down_bid_depth_m3']
FEATURES=base.FEATURES+PENDING_FEATURES

def side_depth(book,side,outcome_price):
    p=round(float(outcome_price),2)
    if side=='UP': return float(book['bids'].get(p,0.0))
    return float(book['asks'].get(round(1.0-p,2),0.0))

def pending_features(active,up,down,bf,book,t):
    pu=sum(float(x['intentLowerBound']) for x in active if x['side']=='UP'); pd=sum(float(x['intentLowerBound']) for x in active if x['side']=='DOWN')
    cu=sum(1 for x in active if x['side']=='UP'); cd=sum(1 for x in active if x['side']=='DOWN'); pg=pu+pd; pn=pu-pd
    ages=[(t-int(x['placementCarrierReadyMs']))/1000.0 for x in active]
    uoffs=[(float(x['targetPrice'])-bf['up_bid'])/.01 for x in active if x['side']=='UP']; doffs=[(float(x['targetPrice'])-bf['down_bid'])/.01 for x in active if x['side']=='DOWN']
    pub=sum(float(x['intentLowerBound']) for x in active if x['side']=='UP' and abs(float(x['targetPrice'])-bf['up_bid'])<=.005)
    pdb=sum(float(x['intentLowerBound']) for x in active if x['side']=='DOWN' and abs(float(x['targetPrice'])-bf['down_bid'])<=.005)
    eu=up+pu; ed=down+pd; eg=eu+ed; eab=abs(eu-ed); ew='UP' if eu<ed-1e-9 else 'DOWN' if ed<eu-1e-9 else None
    mw='UP' if up<down-1e-9 else 'DOWN' if down<up-1e-9 else None; md='DOWN' if mw=='UP' else 'UP' if mw=='DOWN' else None
    pmw=sum(float(x['intentLowerBound']) for x in active if mw and x['side']==mw); pmd=sum(float(x['intentLowerBound']) for x in active if md and x['side']==md)
    us=max(0.0,(bf['up_ask']-bf['up_bid'])/.01-1.0); ds=max(0.0,(bf['down_ask']-bf['down_bid'])/.01-1.0)
    f={'pending_up_qty':pu,'pending_down_qty':pd,'pending_gross_qty':pg,'pending_net_qty':pn,'pending_abs_net_qty':abs(pn),'pending_count':float(len(active)),'pending_up_count':float(cu),'pending_down_count':float(cd),'pending_oldest_age_s':max(ages) if ages else 0.0,'pending_newest_age_s':min(ages) if ages else 0.0,'pending_up_at_best_qty':pub,'pending_down_at_best_qty':pdb,'pending_up_mean_offset_ticks':float(np.mean(uoffs)) if uoffs else 0.0,'pending_down_mean_offset_ticks':float(np.mean(doffs)) if doffs else 0.0,'effective_up_qty':eu,'effective_down_qty':ed,'effective_abs_net':eab,'effective_pair_coverage':2*min(eu,ed)/eg if eg>1e-9 else 0.0,'effective_weak_side_up':1.0 if ew=='UP' else -1.0 if ew=='DOWN' else 0.0,'effective_weak_gap':eab,'pending_materialized_weak_qty':pmw,'pending_materialized_dom_qty':pmd,'up_inside_empty_ticks':us,'down_inside_empty_ticks':ds}
    for side,prefix,bid in [('UP','up',bf['up_bid']),('DOWN','down',bf['down_bid'])]:
        for k in (1,2,3): f[f'{prefix}_bid_depth_m{k}']=side_depth(book,side,bid-.01*k)
    return f

def main():
    OUTDIR.mkdir(parents=True,exist_ok=True); src=json.loads(PLAC.read_text()); placements=[r for r in src['rows'] if r.get('highConfidencePlacement') and r.get('placementCarrierReadyMs') is not None]
    by={}
    for r in placements: by.setdefault(int(r['marketId']),[]).append(r)
    c=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row
    X=[];yh=[];ys=[];yw=[];yo=[];yq=[];mids=[];ts=[];positive=0;pending_states=0;positive_with_pending=0
    for mi,mid in enumerate(sorted(by),1):
        mr=c.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone()
        if not mr or not mr[0]:continue
        end=int(mr[0]);start=end-300000; pls=sorted(by[mid],key=lambda r:int(r['placementCarrierReadyMs']))
        parents=[dict(r) for r in c.execute("""select role,side,min(event_ms) first_event_ms,sum(shares) shares,case when sum(shares)>0 then sum(price*shares)/sum(shares) end average_price,coalesce(nullif(order_hash,''),source_leg_id) parent_id from maker_book_inference_wallet_events where market_id=? and quote_type='BID' and role in ('MAKER','TAKER') and side in ('UP','DOWN') group by role,side,coalesce(nullif(order_hash,''),source_leg_id) order by first_event_ms,parent_id""",(mid,))]
        updates=list(c.execute('select source_timestamp_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)))
        buckets={}
        for p in pls:buckets.setdefault((int(p['placementCarrierReadyMs'])//STEP)*STEP,p)
        book={'bids':{},'asks':{}};ui=pi=0;up=down=cost=0.;hist=deque();oc=0
        for t in range(start,end,STEP):
            sec=(end-t)/1000.0
            if sec<=180:continue
            while ui<len(updates) and int(updates[ui]['source_timestamp_ms'])<t:
                r=updates[ui]
                if int(r['is_checkpoint']):book={'bids':{float(k):float(v) for k,v in (base.dec(r['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (base.dec(r['native_asks_z']) or {}).items()}}
                else:base.apply_changes(book,base.dec(r['changes_z']) or {})
                oc=int(r['order_count'] or 0);ui+=1
            while pi<len(parents) and int(parents[pi]['first_event_ms'])<t:
                p=parents[pi];sh=float(p['shares'] or 0);px=float(p['average_price'] or 0);side=str(p['side']);role=str(p['role'])
                if sh>0:
                    if side=='UP':up+=sh
                    else:down+=sh
                    cost+=sh*px;hist.append((int(p['first_event_ms']),role,side,sh,px))
                    while hist and t-hist[0][0]>30000:hist.popleft()
                pi+=1
            bf=base.book_features(book,oc)
            if bf is None:continue
            bf['seconds_left']=sec;f,weak=base.state_features(up,down,cost,hist,t,pi,len(parents),bf)
            active=[x for x in pls if int(x['placementCarrierReadyMs'])<t<int(x['firstFillMs'])]
            pf=pending_features(active,up,down,bf,book,t);f.update(pf);pending_states+=int(bool(active))
            p=buckets.get(t);haz=1 if p is not None else 0;sideup=weaklab=offset=qty=np.nan
            if p is not None:
                side=str(p['side']);sideup=1. if side=='UP' else 0.;weaklab=np.nan if weak is None else (1. if side==weak else 0.);bid=bf['up_bid'] if side=='UP' else bf['down_bid'];offset=(float(p['targetPrice'])-bid)/.01;qty=math.log1p(float(p['intentLowerBound']));positive+=1;positive_with_pending+=int(bool(active))
            X.append([float(f.get(k,0.) or 0.) for k in FEATURES]);yh.append(haz);ys.append(sideup);yw.append(weaklab);yo.append(offset);yq.append(qty);mids.append(mid);ts.append(t)
        if mi%50==0:print(json.dumps({'progressMarkets':mi,'rows':len(X),'positives':positive}),flush=True)
    c.close();np.savez_compressed(OUTDIR/'dataset.npz',X=np.asarray(X,np.float32),y_hazard=np.asarray(yh,np.int8),y_side_up=np.asarray(ys,np.float32),y_weak=np.asarray(yw,np.float32),y_offset_ticks=np.asarray(yo,np.float32),y_log_qty=np.asarray(yq,np.float32),market_id=np.asarray(mids,np.int32),timestamp_ms=np.asarray(ts,np.int64))
    meta={'version':'ETH_MAKER_PLACEMENT_TEACHER_PILOT300_V2_PENDING_DATASET','features':FEATURES,'rows':len(X),'markets':len(set(mids)),'placementPositives':positive,'positiveRate':positive/len(X) if X else None,'statesWithPendingRate':pending_states/len(X) if X else None,'positiveWithPriorPendingRate':positive_with_pending/positive if positive else None,'blockedFromTrainingAndOfflineSplits':sorted(BLOCKED),'strictPast':'public book/filled inventory strict-past; pending_* is offline reconstructed Target own-order latent state from prior no18 placement until first confirmed fill; runtime counterpart is OUR own open/resting order ledger','newExposureBoundary':'seconds_left>180 only'};(OUTDIR/'dataset.meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(meta,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
