from __future__ import annotations
import json,math,sqlite3,zlib
from collections import defaultdict,deque
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
BOOK=ROOT/'data/wallet_maker_book_inference_eth5m.db';PLAC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_init_book_stabilization_v4'
BLOCK={1813144,1813392,1813399,1813418,1813430,1813446,1813454,1813487,1813742,1813750,1813874,1814101,1814124,1814127,1814134,1814493,1814497,1814543,1815055,1815060,1815063,1815143,1815150,1815155,1815163,1815246}
FEATURES=['elapsed_s','seconds_left','book_age_s','updates_seen_scaled','order_count','order_count_delta_start','up_bid','up_ask','up_mid','spread','bid_depth','ask_depth','top3_bid','top3_ask','depth_imbalance','cum_add_log','cum_cut_log','cum_bid_add_log','cum_ask_add_log','cum_bid_cut_log','cum_ask_cut_log','updates_1s','updates_3s','updates_10s','add_1s_log','cut_1s_log','add_3s_log','cut_3s_log','add_10s_log','cut_10s_log','mid_range_3s','mid_range_10s','spread_mean_3s','spread_mean_10s','spread_range_3s','spread_range_10s','bid_depth_cv_3s','ask_depth_cv_3s','bid_depth_cv_10s','ask_depth_cv_10s','stable_mid_ms','stable_spread_ms','stable_best_bid_ms','stable_best_ask_ms','abs_mid_edge']

def dec(b):return json.loads(zlib.decompress(b).decode()) if b else None

def cv(xs):
    if len(xs)<2:return 0.
    m=float(np.mean(xs));return float(np.std(xs)/(abs(m)+1e-9))

def rng(xs):return float(max(xs)-min(xs)) if xs else 0.
def mean(xs):return float(np.mean(xs)) if xs else 0.

def main():
    OUT.mkdir(parents=True,exist_ok=True);src=json.load(open(PLAC,encoding='utf-8'))['rows'];by=defaultdict(list)
    for r in src:
        if r.get('highConfidencePlacement') and int(r['marketId']) not in BLOCK and r.get('placementCarrierReadyMs') is not None:by[int(r['marketId'])].append(r)
    con=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row
    X=[];y500=[];y2=[];y5=[];mids=[];times=[]
    for mi,mid in enumerate(sorted(by),1):
        ups=list(con.execute('select id,source_timestamp_ms,received_at_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by received_at_ms,id',(mid,)))
        if not ups:continue
        source_recv={}
        for u in ups: source_recv[int(u['source_timestamp_ms'])]=min(int(u['received_at_ms']),source_recv.get(int(u['source_timestamp_ms']),10**30))
        ps=[]
        for r in by[mid]:
            sr=int(r['placementCarrierReadyMs']);recv=source_recv.get(sr)
            if recv is not None:ps.append((int(recv),r))
        if not ps:continue
        first_t,first_r=min(ps,key=lambda x:x[0]);endrow=con.execute('select window_end_ms from maker_book_inference_markets where market_id=?',(mid,)).fetchone();end=int(endrow[0] or 0) if endrow else 0;start=end-300000 if end else int(ups[0]['received_at_ms']);book={'bids':{},'asks':{}};hist=deque();changes=deque();first_valid=None;start_oc=None;cum={'ba':0.,'aa':0.,'bc':0.,'ac':0.};last_mid=last_spread=last_bb=last_ba=None;last_mid_change=last_spread_change=last_bb_change=last_ba_change=None;seen=0
        for u in ups:
            t=int(u['received_at_ms'])
            if t>=first_t:break
            seen+=1
            curadd={'ba':0.,'aa':0.,'bc':0.,'ac':0.}
            if int(u['is_checkpoint']):
                book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
            else:
                ch=dec(u['changes_z']) or {}
                for side,key in [('bids','b'),('asks','a')]:
                    for x in ch.get(side,[]) or []:
                        p=float(x.get('price') or 0);after=float(x.get('after') or 0);d=float(x.get('delta') or 0)
                        if after<=1e-12:book[side].pop(p,None)
                        else:book[side][p]=after
                        if d>=0:curadd[key+'a']+=d
                        else:curadd[key+'c']+=-d
            for k in cum:cum[k]+=curadd[k]
            changes.append((t,curadd.copy()));
            while changes and t-changes[0][0]>10000:changes.popleft()
            if not book['bids'] or not book['asks']:continue
            bb=max(book['bids']);ba=min(book['asks']);bd=float(book['bids'][bb]);ad=float(book['asks'][ba]);midpx=(bb+ba)/2;sp=ba-bb;topb=sum(book['bids'][p] for p in sorted(book['bids'],reverse=True)[:3]);topa=sum(book['asks'][p] for p in sorted(book['asks'])[:3]);imb=(topb-topa)/(topb+topa+1e-9);oc=float(u['order_count'] or 0)
            if first_valid is None:first_valid=t
            if start_oc is None:start_oc=oc
            if last_mid is None or abs(midpx-last_mid)>1e-12:last_mid_change=t;last_mid=midpx
            if last_spread is None or abs(sp-last_spread)>1e-12:last_spread_change=t;last_spread=sp
            if last_bb is None or abs(bb-last_bb)>1e-12:last_bb_change=t;last_bb=bb
            if last_ba is None or abs(ba-last_ba)>1e-12:last_ba_change=t;last_ba=ba
            hist.append((t,{'mid':midpx,'spread':sp,'bd':bd,'ad':ad}));
            while hist and t-hist[0][0]>10000:hist.popleft()
            def H(ms,key):return [v[key] for tt,v in hist if t-tt<=ms]
            def C(ms,key):return sum(v[key] for tt,v in changes if t-tt<=ms)
            f={'elapsed_s':(t-start)/1000.,'seconds_left':(end-t)/1000. if end else 999.,'book_age_s':(t-first_valid)/1000.,'updates_seen_scaled':min(1.,seen/300.),'order_count':oc,'order_count_delta_start':oc-(start_oc or oc),'up_bid':bb,'up_ask':ba,'up_mid':midpx,'spread':sp,'bid_depth':bd,'ask_depth':ad,'top3_bid':float(topb),'top3_ask':float(topa),'depth_imbalance':float(imb),'cum_add_log':math.log1p(cum['ba']+cum['aa']),'cum_cut_log':math.log1p(cum['bc']+cum['ac']),'cum_bid_add_log':math.log1p(cum['ba']),'cum_ask_add_log':math.log1p(cum['aa']),'cum_bid_cut_log':math.log1p(cum['bc']),'cum_ask_cut_log':math.log1p(cum['ac']),'updates_1s':float(sum(t-tt<=1000 for tt,_ in changes)),'updates_3s':float(sum(t-tt<=3000 for tt,_ in changes)),'updates_10s':float(len(changes)),'add_1s_log':math.log1p(C(1000,'ba')+C(1000,'aa')),'cut_1s_log':math.log1p(C(1000,'bc')+C(1000,'ac')),'add_3s_log':math.log1p(C(3000,'ba')+C(3000,'aa')),'cut_3s_log':math.log1p(C(3000,'bc')+C(3000,'ac')),'add_10s_log':math.log1p(C(10000,'ba')+C(10000,'aa')),'cut_10s_log':math.log1p(C(10000,'bc')+C(10000,'ac')),'mid_range_3s':rng(H(3000,'mid')),'mid_range_10s':rng(H(10000,'mid')),'spread_mean_3s':mean(H(3000,'spread')),'spread_mean_10s':mean(H(10000,'spread')),'spread_range_3s':rng(H(3000,'spread')),'spread_range_10s':rng(H(10000,'spread')),'bid_depth_cv_3s':cv(H(3000,'bd')),'ask_depth_cv_3s':cv(H(3000,'ad')),'bid_depth_cv_10s':cv(H(10000,'bd')),'ask_depth_cv_10s':cv(H(10000,'ad')),'stable_mid_ms':float(t-(last_mid_change or t)),'stable_spread_ms':float(t-(last_spread_change or t)),'stable_best_bid_ms':float(t-(last_bb_change or t)),'stable_best_ask_ms':float(t-(last_ba_change or t)),'abs_mid_edge':abs(midpx-.5)}
            dt=first_t-t;X.append([float(f[k]) for k in FEATURES]);y500.append(int(0<dt<=500));y2.append(int(0<dt<=2000));y5.append(int(0<dt<=5000));mids.append(mid);times.append(t)
        if mi%50==0:print(json.dumps({'progress':mi,'markets':len(by),'rows':len(X),'p500':int(sum(y500)),'p2':int(sum(y2)),'p5':int(sum(y5))}),flush=True)
    np.savez_compressed(OUT/'dataset.npz',X=np.asarray(X,np.float32),y500=np.asarray(y500,np.int8),y2000=np.asarray(y2,np.int8),y5000=np.asarray(y5,np.int8),market_id=np.asarray(mids,np.int32),timestamp_ms=np.asarray(times,np.int64))
    meta={'version':'ETH_MAKER_INIT_BOOK_STABILIZATION_V4_DATASET','features':FEATURES,'rows':len(X),'markets':len(set(mids)),'rates':{'500ms':float(np.mean(y500)) if y500 else None,'2000ms':float(np.mean(y2)) if y2 else None,'5000ms':float(np.mean(y5)) if y5 else None},'boundary':'only states strictly before first high-confidence no18 Target Maker placement; labels ask whether first placement arrives within future 0.5/2/5s; no Target future inventory/action features','blocked':sorted(BLOCK)};(OUT/'dataset.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta,ensure_ascii=False),flush=True);con.close()
if __name__=='__main__':main()
