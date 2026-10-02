from __future__ import annotations
import json, sqlite3
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'strategy_target_compare_v1.db'
MID=1513668
VER='UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER'
SCALE=0.21164021164021163
BASE_SHARES=18.0
MIN_NOTIONAL=1.0

def bucket(reason:str,side:str)->str:
    r=(reason or '').upper()
    kind='REPAIR' if ('REPAIR' in r or 'UNRESOLVED' in r or 'RESIDUAL' in r) else 'NORMAL'
    return f'{kind}_{side}'

con=sqlite3.connect(DB); con.row_factory=sqlite3.Row
rows=con.execute("select side,price,shares,placed_at_ms,placement_state_json from our_orders where market_id=? and strategy_version=? and channel='MAKER' order by placed_at_ms,rowid",(MID,VER)).fetchall()
con.close()
acc=defaultdict(float)
acc_shares=defaultdict(float)
orders=[]
base=rows[0]['placed_at_ms'] if rows else 0
virtual=[]
for i,r in enumerate(rows,1):
    try: st=json.loads(r['placement_state_json'] or '{}')
    except: st={}
    reason=str(st.get('reason') or '')
    side=str(r['side'])
    px=float(r['price']); sh=float(r['shares'])
    scaled_sh=sh*SCALE
    scaled_notional=scaled_sh*px
    b=bucket(reason,side)
    acc[b]+=scaled_notional
    acc_shares[b]+=scaled_sh
    virtual.append({'i':i,'tMs':int(r['placed_at_ms'])-base,'side':side,'price':px,'reason':reason,'bucket':b,'scaledNotional':scaled_notional})
    if acc[b] >= MIN_NOTIONAL-1e-12:
        # Preserve all accumulated virtual notional for this responsibility bucket.
        # Quantity is executed at the latest intent price so the emitted order is legal.
        emit_notional=acc[b]
        emit_shares=emit_notional/px
        orders.append({'emitAtIntent':i,'tMs':int(r['placed_at_ms'])-base,'side':side,'price':px,'bucket':b,'notional':emit_notional,'shares':emit_shares,'virtualSharesAccumulated':acc_shares[b]})
        acc[b]=0.0; acc_shares[b]=0.0

summary={
 'version':'CAP100_MIN1_RESPONSIBILITY_ACCUMULATOR_1513668_V0',
 'marketId':MID,'scale':SCALE,'baseChildShares':BASE_SHARES,'scaledChildSharesNominal':BASE_SHARES*SCALE,'minNotionalUsdt':MIN_NOTIONAL,
 'virtualIntentCount':len(virtual),'emittedOrderCount':len(orders),
 'emittedNotionalUsdt':sum(x['notional'] for x in orders),'leftoverVirtualNotionalUsdt':sum(acc.values()),
 'emittedByBucket':{},'timeCoverage':{},'orders':orders,'leftovers':dict(acc),
 'guards':['Uses only fixed strict-past scale previously calibrated before market 1513668.','No HFT fill/PnL assumption. This is execution-shape compression only.','Buckets are separated by NORMAL/REPAIR and UP/DOWN; no cross-responsibility netting.']
}
for b in sorted(set([x['bucket'] for x in virtual]+list(acc))):
    xs=[x for x in orders if x['bucket']==b]
    summary['emittedByBucket'][b]={'orders':len(xs),'notional':sum(x['notional'] for x in xs),'leftover':acc.get(b,0.0)}
for sec in (5,10,30,60,120,180,240,300):
    summary['timeCoverage'][f'{sec}s']={'virtualIntents':sum(x['tMs']<=sec*1000 for x in virtual),'emittedOrders':sum(x['tMs']<=sec*1000 for x in orders),'emittedNotional':sum(x['notional'] for x in orders if x['tMs']<=sec*1000)}
out=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_min1_responsibility_accumulator_1513668_v0.json'
out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
