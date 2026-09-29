from __future__ import annotations

import argparse, json, math, sqlite3, statistics, zlib
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'wallet_maker_book_inference.db'
OUT=ROOT/'data'/'research'/'target_maker_time_depth_layer_v1_report.json'
BINS=[('T300_240',240.0,300.000001),('T240_180',180.0,240.0),('T180_120',120.0,180.0),('T120_60',60.0,120.0),('T60_30',30.0,60.0),('T30_15',15.0,30.0),('T15_0',-0.000001,15.0)]
GRID=.01

def dec(v): return json.loads(zlib.decompress(v).decode('utf-8')) if v else None

def pct(xs,p):
    if not xs:return None
    a=sorted(float(x) for x in xs); pos=(len(a)-1)*p; lo=math.floor(pos); hi=math.ceil(pos); w=pos-lo
    return a[lo]*(1-w)+a[hi]*w

def stats(xs):
    a=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(a),'min':min(a) if a else None,'max':max(a) if a else None,'mean':statistics.mean(a) if a else None,'median':statistics.median(a) if a else None,'p25':pct(a,.25),'p75':pct(a,.75),'p90':pct(a,.9)}

def bname(sec):
    for n,lo,hi in BINS:
        if lo<sec<=hi:return n
    return None

def apply_changes(book,changes):
    for key in ('bids','asks'):
        side=book[key]
        for ch in changes.get(key,[]) if isinstance(changes,dict) else []:
            p=float(ch['price']); after=float(ch['after'])
            if after<=1e-12: side.pop(p,None)
            else: side[p]=after

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--markets-limit',type=int,default=0); ap.add_argument('--report',type=Path,default=OUT); args=ap.parse_args()
    c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on')
    try:
        latest=int(c.execute('select max(source_timestamp_ms) from maker_book_inference_updates').fetchone()[0] or 0)
        q='select market_id,window_end_ms from maker_book_inference_markets where window_end_ms<=? order by window_end_ms desc'
        vals=[dict(r) for r in c.execute(q,(latest-15000,))]
        if args.markets_limit>0: vals=vals[:args.markets_limit]
        markets={int(r['market_id']):int(r['window_end_ms']) for r in vals}
        out=[]; missing_state=0
        for mi,(m,end) in enumerate(markets.items(),1):
            parents=[dict(r) for r in c.execute('''select p.* from maker_book_inference_v21_parent_lifecycles p where p.market_id=? and p.placement_last_ms is not null and p.placement_supports_18=1 and p.placement_coverage>=.85 and p.fill_allocation_coverage>=.70 and p.confidence>=.75''',(m,))]
            alloc_last={}
            for a in c.execute("select parent_id,source_ms,update_id from maker_book_inference_v21_allocations where market_id=? and allocation_kind='PARENT_PLACEMENT'",(m,)):
                key=(str(a['parent_id']),int(a['source_ms'])); uid=int(a['update_id'])
                if uid>alloc_last.get(key,0): alloc_last[key]=uid
            wanted=defaultdict(list)
            for p in parents:
                uid=alloc_last.get((str(p['parent_id']),int(p['placement_last_ms'])))
                if uid is not None:wanted[uid].append(p)
            if not wanted: continue
            book={'bids':{},'asks':{}}
            for u in c.execute('select id,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by id',(m,)):
                if int(u['is_checkpoint']):
                    book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
                else: apply_changes(book,dec(u['changes_z']) or {})
                uid=int(u['id'])
                if uid not in wanted: continue
                for p in wanted[uid]:
                    side=str(p['native_book_side']); price=float(p['native_price']); sec=(end-int(p['placement_last_ms']))/1000; bn=bname(sec)
                    if not bn: continue
                    if side=='BID':
                        if not book['bids']: missing_state+=1; continue
                        touch=max(book['bids']); offset=(touch-price)/GRID; front=sum(v for px,v in book['bids'].items() if px>price+1e-9)
                    else:
                        if not book['asks']: missing_state+=1; continue
                        touch=min(book['asks']); offset=(price-touch)/GRID; front=sum(v for px,v in book['asks'].items() if px<price-1e-9)
                    out.append({'market':m,'bin':bn,'sec':sec,'targetSide':p['target_side'],'nativeSide':side,'nativePrice':price,'touch':touch,'offsetTicks':offset,'frontDepth':front,'levelSize':book[side.lower()+'s'].get(price,0.0),'restingMs':p['resting_ms']})
        by=defaultdict(list)
        for r in out: by[r['bin']].append(r)
        bins={}
        for n,_,_ in BINS:
            rs=by.get(n,[]); offs=[r['offsetTicks'] for r in rs]
            bins[n]={'placementsWithDepth':len(rs),'markets':len({r['market'] for r in rs}),'offsetTicks':stats(offs),'offsetBucketShare':{'atTouch':sum(abs(x)<.25 for x in offs)/len(offs) if offs else None,'oneTick':sum(.75<=x<1.25 for x in offs)/len(offs) if offs else None,'twoTicks':sum(1.75<=x<2.25 for x in offs)/len(offs) if offs else None,'threeTicks':sum(2.75<=x<3.25 for x in offs)/len(offs) if offs else None,'fourPlusTicks':sum(x>=3.75 for x in offs)/len(offs) if offs else None},'frontDepthShares':stats([r['frontDepth'] for r in rs]),'levelSizeAfterPlacement':stats([r['levelSize'] for r in rs]),'restingMs':stats([r['restingMs'] for r in rs if r['restingMs'] is not None])}
        report={'reportVersion':'TARGET_MAKER_TIME_DEPTH_LAYER_V1','researchOnly':True,'layer':'TIME_ONLY','inventoryUsed':False,'marketDirectionUsed':False,'method':{'sample':'same high-confidence anchored 18-share placement contract as TARGET_MAKER_TIME_LAYER_V1','depthClock':'book state immediately after the allocated placement update','offset':'UP/BID: (best_bid-quote)/0.01; DOWN/native ASK: (quote-best_ask)/0.01','frontDepth':'public shares strictly ahead of inferred quote price after placement','warning':'placement ownership remains probabilistic; touch/depth reconstruction is public-book state, not private queue position'},'coverage':{'marketsRequested':len(markets),'placementsWithDepth':len(out),'missingBookState':missing_state,'latestSourceMs':latest},'byTime':bins}
        args.report.parent.mkdir(parents=True,exist_ok=True); args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:c.close()
if __name__=='__main__':main()
