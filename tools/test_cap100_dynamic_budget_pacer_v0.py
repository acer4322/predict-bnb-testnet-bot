import json, sqlite3, statistics
from pathlib import Path
ROOT=Path('.')
DB=ROOT/'data/research/own_state_v0/own_state_replay_v0.db'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_dynamic_budget_pacer_v0.json'
markets=[1513698,1513997,1514018,1514073,1514152,1514162,1514169,1514198,1514248,1514249]
# strict-past conservative initial scale from prior work
BASE_SH=2.35
BUDGET=80.0
MIN_NOT=1.0
RESERVE_FLOOR=8.0
con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
# discover useful columns
cols=[r[1] for r in con.execute('pragma table_info(own_state_orders)')]
print('cols',cols)
# infer schema from existing rows
rows=con.execute('select * from own_state_orders where market_id=? order by created_at_ms limit 2',(markets[0],)).fetchall()
print('sample', [dict(r) for r in rows])

def get_orders(mid):
    rr=con.execute('select * from own_state_orders where market_id=? order by created_at_ms',(mid,)).fetchall()
    out=[]
    for r in rr:
        d=dict(r)
        # only maker-like source orders used by previous shape test; choose filled/placement rows with valid side/price
        side=str(d.get('side') or '').upper(); price=d.get('price')
        if side not in ('UP','DOWN') or price is None: continue
        try: price=float(price)
        except: continue
        if not (0<price<1): continue
        ts=int(d.get('created_at_ms') or d.get('placed_at_ms') or 0)
        role=str(d.get('role') or d.get('intent_type') or d.get('order_role') or 'NORMAL').upper()
        bucket=('REPAIR_' if 'REPAIR' in role else 'NORMAL_')+side
        out.append((ts,side,price,bucket))
    return out

def run(mid):
    src=get_orders(mid)
    if not src: return None
    t0=src[0][0]; buckets={k:0.0 for k in ['NORMAL_UP','NORMAL_DOWN','REPAIR_UP','REPAIR_DOWN']}
    spent=0.0; emitted=[]; virtual=[]
    for idx,(ts,side,price,bucket) in enumerate(src):
        elapsed=max(0,ts-t0); frac=min(1.0,elapsed/300000.0)
        remaining=max(0.0,BUDGET-spent)
        # budget trajectory: early spend ceiling grows with time, but allow a small early boot allowance.
        target_ceiling=min(BUDGET, 8.0 + 72.0*(frac**0.72))
        headroom=max(0.0,target_ceiling-spent)
        # preserve at least RESERVE_FLOOR until late phase for repair/late production
        usable=max(0.0, remaining - (RESERVE_FLOOR if frac<0.82 else 0.0))
        desired_not=BASE_SH*price
        virtual.append(desired_not)
        buckets[bucket]+=desired_not
        # repair can use more immediate headroom; normal waits for paced ceiling
        allowed = usable if bucket.startswith('REPAIR') else min(usable,headroom)
        if buckets[bucket]+1e-9 >= MIN_NOT and allowed+1e-9 >= MIN_NOT:
            notional=min(buckets[bucket],allowed)
            if notional+1e-9>=MIN_NOT:
                shares=notional/price
                emitted.append({'idx':idx,'atMs':elapsed,'side':side,'bucket':bucket,'price':price,'notional':notional,'shares':shares})
                spent+=notional; buckets[bucket]-=notional
    last=emitted[-1]['atMs'] if emitted else None
    return {'marketId':mid,'sourceOrders':len(src),'emittedOrders':len(emitted),'spent':spent,'lastEmitMs':last,'past120':bool(last is not None and last>=120000),'past180':bool(last is not None and last>=180000),'pending':buckets,'emitted':emitted}
res=[run(m) for m in markets]; res=[x for x in res if x]
summary={'markets':len(res),'under80':sum(x['spent']<=80+1e-9 for x in res),'maxSpent':max(x['spent'] for x in res),'medianSpent':statistics.median(x['spent'] for x in res),'past120':sum(x['past120'] for x in res),'past180':sum(x['past180'] for x in res),'medianLastEmitMs':statistics.median([x['lastEmitMs'] for x in res if x['lastEmitMs'] is not None])}
out={'version':'CAP100_DYNAMIC_BUDGET_PACER_V0','rules':{'budget':BUDGET,'baseShares':BASE_SH,'minNotional':MIN_NOT,'reserveFloor':RESERVE_FLOOR,'normalCeiling':'8 + 72*(elapsedFraction^0.72)','repair':'may use available capital ahead of normal pacing'},'summary':summary,'markets':res}
OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps({'out':str(OUT),'summary':summary,'markets':[{'id':x['marketId'],'spent':round(x['spent'],2),'orders':x['emittedOrders'],'last':x['lastEmitMs']} for x in res]},indent=2))
