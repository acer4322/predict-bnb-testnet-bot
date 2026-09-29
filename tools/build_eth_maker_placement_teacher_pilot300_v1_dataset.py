from __future__ import annotations
import json,math,sqlite3
from pathlib import Path
from collections import deque
import numpy as np,sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import build_eth_target_teacher_policy_v1_dataset as base
BOOK=ROOT/'data/wallet_maker_book_inference_eth5m.db'
PLAC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json'
OUTDIR=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_teacher_pilot300_v1'
STEP=1000
BLOCKED={1813144,1813392,1813399,1813418,1813430,1813446,1813454,1813487,1813742,1813750,1813874,1814101,1814124,1814127,1814134,1814493,1814497,1814543,1815055,1815060,1815063,1815143,1815150,1815155,1815163,1815246}

def main():
    out=OUTDIR; out.mkdir(parents=True,exist_ok=True)
    src=json.loads(PLAC.read_text()); placements=[r for r in src['rows'] if r.get('highConfidencePlacement') and r.get('placementCarrierReadyMs') is not None]
    by={}
    for r in placements: by.setdefault(int(r['marketId']),[]).append(r)
    c=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row
    X=[]; yh=[]; ys=[]; yw=[]; yo=[]; yq=[]; mids=[]; ts=[]; positive=0
    for mi,mid in enumerate(sorted(by),1):
        mr=c.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone()
        if not mr or not mr[0]: continue
        end=int(mr[0]); start=end-300000
        # actual Target acquisitions, used only when strictly before state time
        parents=[dict(r) for r in c.execute("""select role,side,min(event_ms) first_event_ms,sum(shares) shares,case when sum(shares)>0 then sum(price*shares)/sum(shares) end average_price,coalesce(nullif(order_hash,''),source_leg_id) parent_id from maker_book_inference_wallet_events where market_id=? and quote_type='BID' and role in ('MAKER','TAKER') and side in ('UP','DOWN') group by role,side,coalesce(nullif(order_hash,''),source_leg_id) order by first_event_ms,parent_id""",(mid,))]
        updates=list(c.execute('select source_timestamp_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)))
        pls=sorted(by[mid],key=lambda r:int(r['placementCarrierReadyMs']))
        buckets={}
        for p in pls:
            t=int(p['placementCarrierReadyMs']); buck=(t//STEP)*STEP
            # first reconstructed placement in a second only; later same-second placements remain excluded from label ambiguity
            buckets.setdefault(buck,p)
        book={'bids':{},'asks':{}}; ui=0; pi=0; up=down=cost=0.; hist=deque(); order_count=0
        for t in range(start,end,STEP):
            seconds_left=(end-t)/1000.0
            if seconds_left<=180.0: continue
            while ui<len(updates) and int(updates[ui]['source_timestamp_ms'])<t:
                r=updates[ui]
                if int(r['is_checkpoint']): book={'bids':{float(k):float(v) for k,v in (base.dec(r['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (base.dec(r['native_asks_z']) or {}).items()}}
                else: base.apply_changes(book,base.dec(r['changes_z']) or {})
                order_count=int(r['order_count'] or 0); ui+=1
            while pi<len(parents) and int(parents[pi]['first_event_ms'])<t:
                p=parents[pi]; sh=float(p['shares'] or 0); px=float(p['average_price'] or 0); side=str(p['side']); role=str(p['role'])
                if sh>0:
                    if side=='UP': up+=sh
                    else: down+=sh
                    cost+=sh*px; hist.append((int(p['first_event_ms']),role,side,sh,px))
                    while hist and t-hist[0][0]>30000: hist.popleft()
                pi+=1
            bf=base.book_features(book,order_count)
            if bf is None: continue
            bf['seconds_left']=seconds_left; f,weak=base.state_features(up,down,cost,hist,t,pi,len(parents),bf)
            p=buckets.get(t); hazard=1 if p is not None else 0
            sideup=weaklab=offset=qty=np.nan
            if p is not None:
                side=str(p['side']); sideup=1.0 if side=='UP' else 0.0
                weaklab=np.nan if weak is None else (1.0 if side==weak else 0.0)
                side_bid=bf['up_bid'] if side=='UP' else bf['down_bid']; offset=(float(p['targetPrice'])-float(side_bid))/0.01
                qty=math.log1p(float(p['intentLowerBound'])); positive+=1
            X.append([float(f.get(k,0.) or 0.) for k in base.FEATURES]); yh.append(hazard); ys.append(sideup); yw.append(weaklab); yo.append(offset); yq.append(qty); mids.append(mid); ts.append(t)
        if mi%50==0: print(json.dumps({'progressMarkets':mi,'of':len(by),'rows':len(X),'positives':positive}),flush=True)
    c.close()
    np.savez_compressed(out/'dataset.npz',X=np.asarray(X,np.float32),y_hazard=np.asarray(yh,np.int8),y_side_up=np.asarray(ys,np.float32),y_weak=np.asarray(yw,np.float32),y_offset_ticks=np.asarray(yo,np.float32),y_log_qty=np.asarray(yq,np.float32),market_id=np.asarray(mids,np.int32),timestamp_ms=np.asarray(ts,np.int64))
    meta={'version':'ETH_MAKER_PLACEMENT_TEACHER_PILOT300_V1_DATASET','features':base.FEATURES,'rows':len(X),'markets':len(set(mids)),'placementPositives':positive,'positiveRate':positive/len(X) if X else None,'blockedFromTrainingAndOfflineSplits':sorted(BLOCKED),'strictPast':'book source_timestamp_ms < state second; Target acquisition parents first_event_ms < state second only; reconstructed no18 placement in current second is label only','placementSource':str(PLAC),'newExposureBoundary':'dataset only contains states with seconds_left > 180','quantityTarget':'log1p(max(observed Maker fill,1/targetPrice)) lower bound, not exact requested quantity','priceTarget':'target Maker price minus strict-pre side best bid in 0.01 ticks'}
    (out/'dataset.meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(meta,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
