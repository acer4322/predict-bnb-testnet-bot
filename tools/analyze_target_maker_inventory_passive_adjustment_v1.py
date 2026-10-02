from __future__ import annotations

import argparse, json, math, sqlite3, statistics, zlib
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BOOK_DB = ROOT / 'data' / 'wallet_maker_book_inference.db'
TARGET_DB = ROOT / 'data' / 'target_wallet_official_v1.db'
OUT = ROOT / 'data' / 'research' / 'target_maker_inventory_passive_adjustment_v1_report.json'
GRID = 0.01
TIME_BINS = [
    ('T300_240',240.0,300.000001),('T240_180',180.0,240.0),('T180_120',120.0,180.0),
    ('T120_60',60.0,120.0),('T60_30',30.0,60.0),('T30_15',15.0,30.0),('T15_0',-0.000001,15.0),
]
IMB_BINS = [
    ('NEAR_FLAT',0.0,0.10),('IMB_10_25',0.10,0.25),('IMB_25_50',0.25,0.50),('IMB_50_PLUS',0.50,1.000001),
]

def dec(v): return json.loads(zlib.decompress(v).decode('utf-8')) if v else None

def pct(xs,p):
    if not xs: return None
    a=sorted(float(x) for x in xs); pos=(len(a)-1)*p; lo=math.floor(pos); hi=math.ceil(pos); w=pos-lo
    return a[lo]*(1-w)+a[hi]*w

def stats(xs):
    a=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(a),'min':min(a) if a else None,'max':max(a) if a else None,'mean':statistics.mean(a) if a else None,
            'median':statistics.median(a) if a else None,'p25':pct(a,.25),'p75':pct(a,.75),'p90':pct(a,.9)}

def time_bin(sec):
    for n,lo,hi in TIME_BINS:
        if lo < sec <= hi: return n
    return None

def imb_bin(r):
    for n,lo,hi in IMB_BINS:
        if lo <= r < hi: return n
    return 'IMB_50_PLUS' if r >= 1 else None

def apply_changes(book, changes):
    for key in ('bids','asks'):
        side=book[key]
        for ch in changes.get(key,[]) if isinstance(changes,dict) else []:
            p=float(ch['price']); after=float(ch['after'])
            if after <= 1e-12: side.pop(p,None)
            else: side[p]=after

def side_relation(up, down, quote_side):
    net=up-down
    if abs(net) < 1e-9: return 'FLAT'
    dominant='UP' if net>0 else 'DOWN'; minority='DOWN' if net>0 else 'UP'
    if quote_side == minority: return 'MINORITY'
    if quote_side == dominant: return 'DOMINANT'
    return 'OTHER'

def summarize(rows):
    out={}
    for rel in ('MINORITY','DOMINANT','FLAT'):
        rs=[r for r in rows if r['relation']==rel]
        if not rs: continue
        acts=[str(r.get('postAction') or '') for r in rs]
        out[rel]={
            'n':len(rs),'markets':len({r['market'] for r in rs}),
            'offsetTicks':stats([r['offsetTicks'] for r in rs]),
            'frontDepthShares':stats([r['frontDepth'] for r in rs]),
            'restingMs':stats([r['restingMs'] for r in rs if r['restingMs'] is not None]),
            'samePriceRefillRate':sum(a=='SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT' for a in acts)/len(acts),
            'reprice1To3TicksRate':sum(a=='REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT' for a in acts)/len(acts),
            'noConfirmedNextParent5sRate':sum(a=='NO_CONFIRMED_NEXT_PARENT_5S' for a in acts)/len(acts),
            'priorAbsNetShares':stats([r['absNet'] for r in rs]),
            'priorImbalanceRatio':stats([r['imbalanceRatio'] for r in rs]),
        }
    nmin=len([r for r in rows if r['relation']=='MINORITY']); ndom=len([r for r in rows if r['relation']=='DOMINANT'])
    if nmin+ndom:
        out['minorityPlacementShareAmongImbalancedAnchors']=nmin/(nmin+ndom)
    if nmin and ndom:
        mo=statistics.mean(r['offsetTicks'] for r in rows if r['relation']=='MINORITY')
        do=statistics.mean(r['offsetTicks'] for r in rows if r['relation']=='DOMINANT')
        out['meanOffsetMinorityMinusDominantTicks']=mo-do
        out['interpretationSign']='NEGATIVE means minority side is quoted closer to touch'
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--markets-limit',type=int,default=200); ap.add_argument('--report',type=Path,default=OUT); args=ap.parse_args()
    b=sqlite3.connect(f'file:{BOOK_DB.resolve().as_posix()}?mode=ro',uri=True,timeout=30); b.row_factory=sqlite3.Row; b.execute('pragma query_only=on')
    t=sqlite3.connect(f'file:{TARGET_DB.resolve().as_posix()}?mode=ro',uri=True,timeout=30); t.row_factory=sqlite3.Row; t.execute('pragma query_only=on')
    try:
        latest=int(b.execute('select max(source_timestamp_ms) from maker_book_inference_updates').fetchone()[0] or 0)
        vals=[dict(r) for r in b.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms<=? order by window_end_ms desc',(latest-15000,))]
        if args.markets_limit>0: vals=vals[:args.markets_limit]
        markets={int(r['market_id']):int(r['window_end_ms']) for r in vals}
        rows=[]; skipped_no_inventory=0; missing_book=0
        for m,end in markets.items():
            legs=[dict(r) for r in t.execute("select event_ms,side,shares from wallet_shadow_target_events where market_id=? and asset='BTC' and role='MAKER' order by event_ms,id",(m,))]
            times=[int(x['event_ms']) for x in legs]
            cum_up=[0.0]; cum_down=[0.0]
            for x in legs:
                cum_up.append(cum_up[-1] + (float(x['shares']) if str(x['side'])=='UP' else 0.0))
                cum_down.append(cum_down[-1] + (float(x['shares']) if str(x['side'])=='DOWN' else 0.0))
            parents=[dict(r) for r in b.execute('''select p.* from maker_book_inference_v21_parent_lifecycles p where p.market_id=?
                and p.placement_last_ms is not null and p.placement_supports_18=1 and p.placement_coverage>=.85
                and p.fill_allocation_coverage>=.70 and p.confidence>=.75''',(m,))]
            alloc_last={}
            for a in b.execute("select parent_id,source_ms,update_id from maker_book_inference_v21_allocations where market_id=? and allocation_kind='PARENT_PLACEMENT'",(m,)):
                key=(str(a['parent_id']),int(a['source_ms'])); uid=int(a['update_id'])
                if uid>alloc_last.get(key,0): alloc_last[key]=uid
            wanted=defaultdict(list)
            for p in parents:
                uid=alloc_last.get((str(p['parent_id']),int(p['placement_last_ms'])))
                if uid is not None: wanted[uid].append(p)
            if not wanted: continue
            book={'bids':{},'asks':{}}
            for u in b.execute('select id,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by id',(m,)):
                if int(u['is_checkpoint']):
                    book={'bids':{float(k):float(v) for k,v in (dec(u['native_bids_z']) or {}).items()},
                          'asks':{float(k):float(v) for k,v in (dec(u['native_asks_z']) or {}).items()}}
                else: apply_changes(book,dec(u['changes_z']) or {})
                uid=int(u['id'])
                if uid not in wanted: continue
                for p in wanted[uid]:
                    place=int(p['placement_last_ms']); sec=(end-place)/1000.0; tb=time_bin(sec)
                    if tb is None: continue
                    idx=bisect_left(times,place)  # strictly prior event_ms only
                    up=cum_up[idx]; down=cum_down[idx]; gross=up+down; net=up-down; absnet=abs(net)
                    if gross<=1e-9:
                        relation='FLAT'; ratio=0.0
                    else:
                        relation=side_relation(up,down,str(p['target_side'])); ratio=absnet/gross
                    ib=imb_bin(ratio)
                    side=str(p['native_book_side']); price=float(p['native_price'])
                    if side=='BID':
                        if not book['bids']: missing_book+=1; continue
                        touch=max(book['bids']); offset=(touch-price)/GRID; front=sum(v for px,v in book['bids'].items() if px>price+1e-9)
                    else:
                        if not book['asks']: missing_book+=1; continue
                        touch=min(book['asks']); offset=(price-touch)/GRID; front=sum(v for px,v in book['asks'].items() if px<price-1e-9)
                    rows.append({'market':m,'secondsLeft':sec,'timeBin':tb,'imbBin':ib,'priorMakerUp':up,'priorMakerDown':down,
                                 'net':net,'absNet':absnet,'gross':gross,'imbalanceRatio':ratio,'relation':relation,'targetSide':p['target_side'],
                                 'offsetTicks':offset,'frontDepth':front,'restingMs':p['resting_ms'],'postAction':p['post_action'],
                                 'postActionDelayMs':p['post_action_delay_ms']})
        imbalanced=[r for r in rows if r['relation'] in ('MINORITY','DOMINANT')]
        by_time={n:summarize([r for r in rows if r['timeBin']==n]) for n,_,_ in TIME_BINS}
        by_imb={n:summarize([r for r in rows if r['imbBin']==n]) for n,_,_ in IMB_BINS}
        matrix={}
        for tn,_,_ in TIME_BINS:
            matrix[tn]={}
            for inn,_,_ in IMB_BINS:
                rr=[r for r in rows if r['timeBin']==tn and r['imbBin']==inn]
                if rr: matrix[tn][inn]=summarize(rr)
        market_skews=[]
        for m in sorted({r['market'] for r in imbalanced}):
            rr=[r for r in imbalanced if r['market']==m]
            mn=[r for r in rr if r['relation']=='MINORITY']; dm=[r for r in rr if r['relation']=='DOMINANT']
            if mn and dm:
                market_skews.append(statistics.mean(r['offsetTicks'] for r in mn)-statistics.mean(r['offsetTicks'] for r in dm))
        report={
            'reportVersion':'TARGET_MAKER_INVENTORY_PASSIVE_ADJUSTMENT_V1','researchOnly':True,'layer':'INVENTORY_ONLY_CONDITIONED_ON_TIME','marketDirectionUsed':False,
            'method':{
                'inventory':'strict-past Target MAKER fills only from official wallet events; event_ms < inferred placement_last_ms',
                'placement':'same high-confidence anchored ±18 placement contract as Layer1',
                'depth':'public book immediately after placement allocation update',
                'relation':'MINORITY means quote side is the smaller strict-past Maker inventory side; DOMINANT means larger side',
                'importantLimit':'anchored placements are later-confirmed by Target Maker fills, so count/activity asymmetry can include fill-selection/adverse-selection effects; depth skew is the cleaner first-pass signal',
            },
            'coverage':{'marketsRequested':len(markets),'placementsWithInventoryAndDepth':len(rows),'imbalancedPlacements':len(imbalanced),'marketsWithImbalancedPlacements':len({r['market'] for r in imbalanced}),'latestSourceMs':latest,'missingBook':missing_book},
            'overall':summarize(rows),
            'byTime':by_time,'byImbalance':by_imb,'timeByImbalance':matrix,
            'marketLevelOffsetSkewMinorityMinusDominant':stats(market_skews),
            'guards':['No winner/SIMPLE3/spot/chainlink direction used.','This is Layer2 only; favorable/unfavorable imbalance belongs to Layer3.','Do not treat anchored placement counts as direct ground-truth quote submission rates.'],
        }
        args.report.parent.mkdir(parents=True,exist_ok=True); args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'coverage':report['coverage'],'overall':report['overall'],'byImbalance':report['byImbalance'],'marketLevelOffsetSkewMinorityMinusDominant':report['marketLevelOffsetSkewMinorityMinusDominant'],'report':str(args.report)},ensure_ascii=False,indent=2))
    finally:
        b.close(); t.close()

if __name__=='__main__': main()
