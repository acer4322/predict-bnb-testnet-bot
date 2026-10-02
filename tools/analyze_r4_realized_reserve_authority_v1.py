from __future__ import annotations
import sqlite3,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/echtgeld_engine_v1.db'
OUT=ROOT/'data/research/r4_v0/r4_realized_reserve_authority_v1.json'
MIDS=[1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531]
EPS=1e-9

def floor(st): return min(st['up'],st['down'])-st['cost']
def side_rel(st,side):
    if abs(st['up']-st['down'])<EPS:return 'FLAT'
    surplus='UP' if st['up']>st['down'] else 'DOWN'
    return 'SURPLUS_SIDE' if side==surplus else 'WEAK_SIDE'

def add(st,side,q,cost):
    st[side.lower()]+=q; st['cost']+=cost

def sub(st,side,q,cost):
    st[side.lower()]-=q; st['cost']-=cost

def main():
    c=sqlite3.connect(DB);c.row_factory=sqlite3.Row
    ph=','.join('?'*len(MIDS))
    orders={r['client_order_id']:dict(r) for r in c.execute(f"select * from engine_cap100_orders where source_market_id in ({ph})",MIDS)}
    evs=[dict(r) for r in c.execute(f"select * from engine_cap100_events where source_market_id in ({ph}) order by source_market_id,occurred_at_ms,seq",MIDS)]
    by={m:[] for m in MIDS}
    for e in evs:by[e['source_market_id']].append(e)
    rows=[]
    for m,es in by.items():
        com={'up':0.0,'down':0.0,'cost':0.0}; real={'up':0.0,'down':0.0,'cost':0.0}
        active=set(); filled={}; phantom_ms=0; last_t=None; phantom_windows=[]; open_ph=None; orders_during=[]; reserve_spend_orders=[]
        for e in es:
            t=int(e['occurred_at_ms']);
            if last_t is not None and floor(com)>0 and floor(real)<=0: phantom_ms += max(0,t-last_t)
            isphant=floor(com)>0 and floor(real)<=0
            if isphant and open_ph is None: open_ph=t
            if (not isphant) and open_ph is not None: phantom_windows.append([open_ph,t]);open_ph=None
            oid=e.get('client_order_id'); typ=e['event_type']; o=orders.get(oid)
            if typ in ('ORDER_RESTING','ORDER_ACCEPTED') and o and oid not in active:
                q=float(o.get('requested_shares') or 0); px=float(o.get('requested_price') or 0); add(com,o['side'],q,q*px);active.add(oid);filled.setdefault(oid,0.0)
                rel=side_rel(real,o['side'])
                if floor(com)>0 and floor(real)<=0:
                    orders_during.append({'t':t,'order':oid,'role':o['role'],'side':o['side'],'relationRealized':rel,'requestedShares':q,'requestedPrice':px,'committedFloor':floor(com),'realizedFloor':floor(real)})
                    if rel=='SURPLUS_SIDE': reserve_spend_orders.append(orders_during[-1])
            elif typ=='FILL_DELTA' and o:
                q=float(e.get('delta_shares') or 0); usd=float(e.get('delta_usdt') or (q*float(e.get('fill_price') or o.get('requested_price') or 0))); add(real,o['side'],q,usd);filled[oid]=filled.get(oid,0)+q
            elif typ in ('ORDER_CANCELED','ORDER_REJECTED') and o and oid in active:
                rem=max(0.0,float(o.get('requested_shares') or 0)-filled.get(oid,0.0)); px=float(o.get('requested_price') or 0)
                if rem>EPS: sub(com,o['side'],rem,rem*px)
                active.discard(oid)
            last_t=t
        if open_ph is not None and last_t is not None: phantom_windows.append([open_ph,last_t])
        rows.append({'marketId':m,'events':len(es),'phantomReserveMs':phantom_ms,'phantomReserveSeconds':phantom_ms/1000,'phantomWindows':phantom_windows,'ordersPlacedDuringPhantom':len(orders_during),'surplusSideOrdersDuringPhantom':len(reserve_spend_orders),'examples':reserve_spend_orders[:5],'finalCommittedFloor':floor(com),'finalRealizedFloor':floor(real)})
    total_ph=sum(r['phantomReserveMs'] for r in rows)
    summary={'markets':len(rows),'marketsWithPhantomReserve':sum(r['phantomReserveMs']>0 for r in rows),'totalPhantomReserveSeconds':total_ph/1000,'medianPhantomSeconds':statistics.median([r['phantomReserveSeconds'] for r in rows]),'ordersPlacedDuringPhantom':sum(r['ordersPlacedDuringPhantom'] for r in rows),'surplusSideOrdersDuringPhantom':sum(r['surplusSideOrdersDuringPhantom'] for r in rows),'marketsWithSurplusSpendDuringPhantom':sum(r['surplusSideOrdersDuringPhantom']>0 for r in rows)}
    out={'version':'R4_REALIZED_RESERVE_AUTHORITY_V1','boundary':'Frozen 8 Echtgeld markets only; execution-environment calibration. COMMITTED ledger counts accepted/resting requested qty until terminal cancel/reject; REALIZED ledger counts FILL_DELTA only. No threshold fitting or model selection.','summary':summary,'markets':rows}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'out':str(OUT),'summary':summary,'markets':rows},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
