from __future__ import annotations
import argparse, json, math, sqlite3, zlib
from collections import deque
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
SNAP=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
BOOK=ROOT/'data/wallet_maker_book_inference_eth5m.db'
OUTDIR=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_target_teacher_policy_v1'
DEVRES=ROOT/'data/research/lan_worker_returns/eth-target-style-hft-dev20-v1b/result.json'

FEATURES=[
 'seconds_left','up_bid','up_ask','up_mid','down_bid','down_ask','down_mid','up_spread','down_spread',
 'up_bid_depth','up_ask_depth','up_top3_bid_depth','up_top3_ask_depth','book_order_count','book_depth_imbalance',
 'gross_shares','net_shares','abs_net','imbalance_ratio','pair_coverage','cost','floor','best_pnl','base_pair_shares',
 'avg_cost_up','avg_cost_down','surplus_ratio','weak_side_up','weak_gap','weak_bid','weak_ask','weak_mid','weak_spread',
 'dom_bid','dom_ask','dom_mid','marginal_pair_sum_weak','projected_floor_delta_weak_1','projected_floor_delta_dom_1',
 'last_action_age_s','last_role_taker','last_side_up','last_price','last_shares',
 'maker_events_5s','taker_events_5s','maker_events_15s','taker_events_15s','maker_shares_10s','taker_shares_10s',
 'events_seen_scaled'
]

def dec(blob):
    if blob is None: return None
    return json.loads(zlib.decompress(blob).decode('utf-8'))

def apply_changes(book, changes):
    if not isinstance(changes,dict): return
    for key in ('bids','asks'):
        for ch in changes.get(key,[]) or []:
            p=float(ch['price']); after=float(ch['after'])
            if after<=1e-12: book[key].pop(p,None)
            else: book[key][p]=after

def topn(d, reverse, n=3):
    if not d: return 0.0
    ks=sorted(d,reverse=reverse)[:n]
    return float(sum(float(d[k]) for k in ks))

def book_features(book, order_count):
    bids,asks=book['bids'],book['asks']
    if not bids or not asks: return None
    bb=max(bids); ba=min(asks)
    ub=float(bb); ua=float(ba); db=1.0-ua; da=1.0-ub
    ubq=float(bids[bb]); uaq=float(asks[ba]); tb=topn(bids,True); ta=topn(asks,False)
    return dict(up_bid=ub,up_ask=ua,up_mid=(ub+ua)/2,down_bid=db,down_ask=da,down_mid=(db+da)/2,
        up_spread=ua-ub,down_spread=da-db,up_bid_depth=ubq,up_ask_depth=uaq,
        up_top3_bid_depth=tb,up_top3_ask_depth=ta,book_order_count=float(order_count or 0),
        book_depth_imbalance=(tb-ta)/(tb+ta) if tb+ta>1e-12 else 0.0)

def state_features(up,down,cost,hist,t,evt_idx,total_events,bf):
    gross=up+down; net=up-down; ab=abs(net); base=min(up,down)
    floor=base-cost; best=max(up,down)-cost
    avg_up=sum(x[4]*x[3] for x in hist if x[2]=='UP')/sum(x[3] for x in hist if x[2]=='UP') if any(x[2]=='UP' and x[3]>0 for x in hist) else 0.0
    avg_dn=sum(x[4]*x[3] for x in hist if x[2]=='DOWN')/sum(x[3] for x in hist if x[2]=='DOWN') if any(x[2]=='DOWN' and x[3]>0 for x in hist) else 0.0
    weak='UP' if up<down-1e-9 else 'DOWN' if down<up-1e-9 else None
    dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
    def sidevals(side):
        if side=='UP': return bf['up_bid'],bf['up_ask'],bf['up_mid'],bf['up_spread']
        if side=='DOWN': return bf['down_bid'],bf['down_ask'],bf['down_mid'],bf['down_spread']
        return 0.,0.,0.,0.
    wb,wa,wm,ws=sidevals(weak); db,da,dm,_=sidevals(dom)
    avg_dom=avg_dn if dom=='DOWN' else avg_up if dom=='UP' else 0.0
    last=hist[-1] if hist else None
    r5=[x for x in hist if t-x[0]<=5000]; r15=[x for x in hist if t-x[0]<=15000]; r10=[x for x in hist if t-x[0]<=10000]
    f=dict(bf)
    f.update(dict(gross_shares=gross,net_shares=net,abs_net=ab,imbalance_ratio=ab/gross if gross else 0.,
        pair_coverage=(2*base/gross) if gross else 0.,cost=cost,floor=floor,best_pnl=best,base_pair_shares=base,
        avg_cost_up=avg_up,avg_cost_down=avg_dn,surplus_ratio=ab/gross if gross else 0.,
        weak_side_up=1.0 if weak=='UP' else -1.0 if weak=='DOWN' else 0.0,weak_gap=ab,
        weak_bid=wb,weak_ask=wa,weak_mid=wm,weak_spread=ws,dom_bid=db,dom_ask=da,dom_mid=dm,
        marginal_pair_sum_weak=(avg_dom+wa) if weak else 0.0,
        projected_floor_delta_weak_1=(1.0-wa) if weak else 0.0,
        projected_floor_delta_dom_1=(-da) if dom else 0.0,
        last_action_age_s=(t-last[0])/1000.0 if last else 999.0,
        last_role_taker=1.0 if last and last[1]=='TAKER' else 0.0,last_side_up=1.0 if last and last[2]=='UP' else 0.0,
        last_price=float(last[4]) if last else 0.0,last_shares=float(last[3]) if last else 0.0,
        maker_events_5s=float(sum(x[1]=='MAKER' for x in r5)),taker_events_5s=float(sum(x[1]=='TAKER' for x in r5)),
        maker_events_15s=float(sum(x[1]=='MAKER' for x in r15)),taker_events_15s=float(sum(x[1]=='TAKER' for x in r15)),
        maker_shares_10s=float(sum(x[3] for x in r10 if x[1]=='MAKER')),taker_shares_10s=float(sum(x[3] for x in r10 if x[1]=='TAKER')),
        events_seen_scaled=float(min(1.0,evt_idx/50.0))))
    return f,weak

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--step-ms',type=int,default=1000); ap.add_argument('--outdir',default=str(OUTDIR)); a=ap.parse_args()
    outdir=Path(a.outdir); outdir.mkdir(parents=True,exist_ok=True)
    dev=set()
    if DEVRES.exists():
        try: dev=set(map(int,json.loads(DEVRES.read_text(encoding='utf-8')).get('cohort',[])))
        except Exception: pass
    s=sqlite3.connect(f'file:{SNAP.resolve().as_posix()}?mode=ro',uri=True); s.row_factory=sqlite3.Row
    b=sqlite3.connect(f'file:{BOOK.resolve().as_posix()}?mode=ro',uri=True); b.row_factory=sqlite3.Row
    target_m={int(r[0]):int(r[1]) for r in s.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    book_m={int(r[0]):int(r[1] or 0) for r in b.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')}
    mids=sorted(set(target_m)&set(book_m), key=lambda m:(target_m[m],m))
    X=[]; yrole=[]; yweak=[]; yside=[]; yqty=[]; mids_out=[]; ts_out=[]; action_role=[]
    role_counts={0:0,1:0,2:0}; weak_n=0; actions=0
    for mi,mid in enumerate(mids,1):
        end=int(target_m[mid] or book_m[mid]); start=end-300000
        parents=[dict(r) for r in s.execute("select role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where asset='ETH' and market_id=? and first_event_ms is not null order by first_event_ms,parent_id",(mid,))]
        if not parents: continue
        updates=list(b.execute('select id,source_timestamp_ms,order_count,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(mid,)))
        if not updates: continue
        # bucket target actions by second, preserving first action in bucket
        buckets={}
        for p in parents:
            t=int(p['first_event_ms']); buck=(t//a.step_ms)*a.step_ms
            if buck not in buckets: buckets[buck]=p
        book={'bids':{},'asks':{}}; ui=0; pi=0; up=down=cost=0.; hist=deque(); last_order_count=0
        # use whole 5m window; state is strict-past to each bucket start
        for t in range(start,end,a.step_ms):
            while ui<len(updates) and int(updates[ui]['source_timestamp_ms'])<t:
                r=updates[ui]
                if int(r['is_checkpoint']):
                    book={'bids':{float(k):float(v) for k,v in (dec(r['native_bids_z']) or {}).items()},
                          'asks':{float(k):float(v) for k,v in (dec(r['native_asks_z']) or {}).items()}}
                else: apply_changes(book,dec(r['changes_z']) or {})
                last_order_count=int(r['order_count'] or 0); ui+=1
            while pi<len(parents) and int(parents[pi]['first_event_ms'])<t:
                p=parents[pi]; sh=float(p['shares'] or 0); px=float(p['average_price'] or 0); side=str(p['side']); role=str(p['role'])
                if sh>0 and px>=0:
                    if side=='UP': up+=sh
                    elif side=='DOWN': down+=sh
                    cost+=sh*px
                    hist.append((int(p['first_event_ms']),role,side,sh,px))
                    while hist and t-hist[0][0]>30000: hist.popleft()
                pi+=1
            bf=book_features(book,last_order_count)
            if bf is None: continue
            bf['seconds_left']=(end-t)/1000.0
            f,weak=state_features(up,down,cost,hist,t,pi,len(parents),bf)
            p=buckets.get(t)
            if p is None:
                role=0; weaklab=np.nan; sidelab=np.nan; qtylab=np.nan
            else:
                role=1 if str(p['role'])=='MAKER' else 2
                side=str(p['side']); sidelab=1.0 if side=='UP' else 0.0
                if weak is None: weaklab=np.nan
                else: weaklab=1.0 if side==weak else 0.0
                qtylab=math.log1p(max(0.0,float(p['shares'] or 0)))
                actions+=1
                if math.isfinite(weaklab): weak_n+=1
            X.append([float(f.get(k,0.) or 0.) for k in FEATURES]); yrole.append(role); yweak.append(weaklab); yside.append(sidelab); yqty.append(qtylab); mids_out.append(mid); ts_out.append(t); action_role.append(role)
            role_counts[role]=role_counts.get(role,0)+1
        if mi%100==0: print(json.dumps({'progress':mi,'markets':len(mids),'rows':len(X),'actions':actions}),flush=True)
    arr=np.asarray(X,dtype=np.float32); yr=np.asarray(yrole,dtype=np.int8); yw=np.asarray(yweak,dtype=np.float32); ys=np.asarray(yside,dtype=np.float32); yq=np.asarray(yqty,dtype=np.float32); mm=np.asarray(mids_out,dtype=np.int32); tt=np.asarray(ts_out,dtype=np.int64)
    np.savez_compressed(outdir/'dataset.npz',X=arr,y_role=yr,y_weak=yw,y_side_up=ys,y_log_qty=yq,market_id=mm,timestamp_ms=tt)
    meta={'version':'ETH_TARGET_TEACHER_POLICY_V1_DATASET','features':FEATURES,'rows':int(len(arr)),'markets':int(len(set(mids_out))),'overlapMarkets':len(mids),'dev20ExcludedFromFormalTrainingByTrainer':sorted(dev),'roleCounts':{str(k):int(v) for k,v in role_counts.items()},'actions':actions,'nonflatActionLabels':weak_n,'stepMs':a.step_ms,'strictPast':'book source_timestamp_ms < decision bucket; Target parents first_event_ms < bucket are state only; current bucket Target action is label only','sources':{'target':str(SNAP),'book':str(BOOK)}}
    (outdir/'dataset.meta.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
    print(json.dumps(meta,indent=2))
    s.close(); b.close()
if __name__=='__main__': main()
