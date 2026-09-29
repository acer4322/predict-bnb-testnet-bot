from __future__ import annotations
import json,math,sqlite3,zlib
from collections import defaultdict,deque
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];BOOK=ROOT/'data/wallet_maker_book_inference_eth5m.db';PLAC=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_placement_no18_pilot300_v1.json';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_maker_init_sequence_lags_v5'
BLOCK={1813144,1813392,1813399,1813418,1813430,1813446,1813454,1813487,1813742,1813750,1813874,1814101,1814124,1814127,1814134,1814493,1814497,1814543,1815055,1815060,1815063,1815143,1815150,1815155,1815163,1815246}
BASE=['up_bid','up_ask','mid','spread','bid_depth','ask_depth','top3_bid','top3_ask','imbalance','order_count']; LAGS=[250,500,1000,2000,3000]; FEATURES=[]
FEATURES+=BASE
for h in LAGS:
    FEATURES += [f'lag{h}_{k}' for k in BASE] + [f'd{h}_{k}' for k in BASE]
FEATURES += ['update_add','update_cut','update_bid_add','update_ask_add','update_bid_cut','update_ask_cut','update_levels']
for h in LAGS: FEATURES += [f'updates_{h}',f'add_{h}',f'cut_{h}',f'bid_add_{h}',f'ask_add_{h}',f'bid_cut_{h}',f'ask_cut_{h}']
FEATURES += ['cum_add_log','cum_cut_log','stable_mid_ms','stable_spread_ms','stable_bid_ms','stable_ask_ms']

def dec(b):return json.loads(zlib.decompress(b).decode()) if b else None

def at(hist,t,h):
    vals=[x for x in hist if x[0]<=t-h]
    return vals[-1][1] if vals else hist[0][1] if hist else None

def main():
    OUT.mkdir(parents=True,exist_ok=True);src=json.load(open(PLAC,encoding='utf-8'))['rows'];by=defaultdict(list)
    for r in src:
        if r.get('highConfidencePlacement') and int(r['marketId']) not in BLOCK and r.get('nearestSupportingAddMs') is not None:by[int(r['marketId'])].append(r)
    con=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True);con.row_factory=sqlite3.Row;X=[];y=[];mids=[];times=[]
    for mi,market_id in enumerate(sorted(by),1):
        ups=list(con.execute('select id,source_timestamp_ms,received_at_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by received_at_ms,id',(market_id,)))
        if not ups:continue
        source_recv={}
        for u in ups:source_recv[int(u['source_timestamp_ms'])]=min(int(u['received_at_ms']),source_recv.get(int(u['source_timestamp_ms']),10**30))
        pts=[]
        for r in by[market_id]:
            recv=source_recv.get(int(r['nearestSupportingAddMs']))
            if recv is not None:pts.append(int(recv))
        if not pts:continue
        first_t=min(pts);book={'bids':{},'asks':{}};hist=deque();chist=deque();cumadd=cumcut=0.;last={'mid':None,'spread':None,'up_bid':None,'up_ask':None};lastchg={k:None for k in last}
        for u in ups:
            t=int(u['received_at_ms'])
            if t>=first_t:break
            ca={'bid_add':0.,'ask_add':0.,'bid_cut':0.,'ask_cut':0.,'levels':0.}
            if int(u['is_checkpoint']):
                book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
            else:
                ch=dec(u['changes_z']) or {}
                for side in ('bids','asks'):
                    for r in ch.get(side,[]) or []:
                        p=float(r.get('price') or 0);after=float(r.get('after') or 0);d=float(r.get('delta') or 0);ca['levels']+=1
                        if after<=1e-12:book[side].pop(p,None)
                        else:book[side][p]=after
                        if d>=0:ca['bid_add' if side=='bids' else 'ask_add']+=d
                        else:ca['bid_cut' if side=='bids' else 'ask_cut']+=-d
            cumadd+=ca['bid_add']+ca['ask_add'];cumcut+=ca['bid_cut']+ca['ask_cut'];chist.append((t,ca));
            while chist and t-chist[0][0]>3200:chist.popleft()
            if not book['bids'] or not book['asks']:continue
            bb=max(book['bids']);ba=min(book['asks']);bd=float(book['bids'][bb]);ad=float(book['asks'][ba]);mid=(bb+ba)/2;sp=ba-bb;topb=float(sum(book['bids'][p] for p in sorted(book['bids'],reverse=True)[:3]));topa=float(sum(book['asks'][p] for p in sorted(book['asks'])[:3]));imb=(topb-topa)/(topb+topa+1e-9);cur={'up_bid':bb,'up_ask':ba,'mid':mid,'spread':sp,'bid_depth':bd,'ask_depth':ad,'top3_bid':topb,'top3_ask':topa,'imbalance':imb,'order_count':float(u['order_count'] or 0)}
            for k in last:
                v=cur[k]
                if last[k] is None or abs(v-last[k])>1e-12:lastchg[k]=t;last[k]=v
            f=dict(cur)
            for h in LAGS:
                q=at(hist,t,h) or cur
                for k in BASE:f[f'lag{h}_{k}']=float(q[k]);f[f'd{h}_{k}']=float(cur[k]-q[k])
                rr=[x for x in chist if t-x[0]<=h];f[f'updates_{h}']=float(len(rr));f[f'add_{h}']=float(sum(x[1]['bid_add']+x[1]['ask_add'] for x in rr));f[f'cut_{h}']=float(sum(x[1]['bid_cut']+x[1]['ask_cut'] for x in rr));f[f'bid_add_{h}']=float(sum(x[1]['bid_add'] for x in rr));f[f'ask_add_{h}']=float(sum(x[1]['ask_add'] for x in rr));f[f'bid_cut_{h}']=float(sum(x[1]['bid_cut'] for x in rr));f[f'ask_cut_{h}']=float(sum(x[1]['ask_cut'] for x in rr))
            f.update(update_add=ca['bid_add']+ca['ask_add'],update_cut=ca['bid_cut']+ca['ask_cut'],update_bid_add=ca['bid_add'],update_ask_add=ca['ask_add'],update_bid_cut=ca['bid_cut'],update_ask_cut=ca['ask_cut'],update_levels=float(ca['levels']),cum_add_log=math.log1p(cumadd),cum_cut_log=math.log1p(cumcut),stable_mid_ms=float(t-(lastchg['mid'] or t)),stable_spread_ms=float(t-(lastchg['spread'] or t)),stable_bid_ms=float(t-(lastchg['up_bid'] or t)),stable_ask_ms=float(t-(lastchg['up_ask'] or t)))
            dt=first_t-t;X.append([float(f[k]) for k in FEATURES]);y.append(int(0<dt<=500));mids.append(market_id);times.append(t);hist.append((t,cur));
            while hist and t-hist[0][0]>3200:hist.popleft()
        if mi%50==0:print(json.dumps({'progress':mi,'markets':len(by),'rows':len(X),'positives':int(sum(y))}),flush=True)
    np.savez_compressed(OUT/'dataset.npz',X=np.asarray(X,np.float32),y=np.asarray(y,np.int8),market_id=np.asarray(mids,np.int32),timestamp_ms=np.asarray(times,np.int64));meta={'version':'ETH_MAKER_INIT_SEQUENCE_LAGS_V5_DATASET','features':FEATURES,'rows':len(X),'markets':len(set(mids)),'positiveRate':float(np.mean(y)) if y else None,'boundary':'pre-first-placement only; nearestSupportingAdd receipt is label anchor; current and lagged public-book features only; no elapsed-time feature','blocked':sorted(BLOCK)};(OUT/'dataset.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print(json.dumps(meta,ensure_ascii=False),flush=True);con.close()
if __name__=='__main__':main()
