from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_min1_responsibility_accumulator_1513668_v0.json'
d=json.loads(SRC.read_text(encoding='utf-8'))
orders=d['orders']
# Structural fault study. Current V0 accumulator has no fill feedback, so a missed order is simply absent.
# V1 fault-aware policy requeues the missed notional/share responsibility into the same bucket on timeout;
# future same-bucket emissions consume it. REPAIR responsibility that remains < $1 at expiry escalates to ACTIVE_CONVERSION_REQUIRED.

def baseline():
    cost=sum(float(o['notional']) for o in orders)
    up=sum(float(o['shares']) for o in orders if o['side']=='UP')
    dn=sum(float(o['shares']) for o in orders if o['side']=='DOWN')
    return cost,up,dn,up-cost

def current_v0_drop(idx:int):
    xs=[o for i,o in enumerate(orders) if i!=idx]
    cost=sum(float(o['notional']) for o in xs)
    up=sum(float(o['shares']) for o in xs if o['side']=='UP')
    dn=sum(float(o['shares']) for o in xs if o['side']=='DOWN')
    return {'cost':cost,'upShares':up,'downShares':dn,'pnl':up-cost,'recoveryAction':'NONE_CURRENT_V0'}

def fault_aware(idx:int):
    miss=orders[idx]
    # Requeue shares, preserve responsibility bucket. At each later order of same bucket, append missed shares
    # but do not force price-cross; diagnostic uses that later order's price. If no later same bucket, escalate.
    xs=[]; pending=float(miss['shares']); recovered_at=None
    bucket=miss['bucket']
    for i,o in enumerate(orders):
        if i==idx: continue
        q=dict(o)
        if i>idx and recovered_at is None and q['bucket']==bucket:
            q['shares']=float(q['shares'])+pending
            q['notional']=float(q['shares'])*float(q['price'])
            recovered_at=i; pending=0.0
        xs.append(q)
    action='REQUEUE_TO_NEXT_SAME_BUCKET' if recovered_at is not None else ('ACTIVE_CONVERSION_REQUIRED' if bucket.startswith('REPAIR_') else 'PENDING_NORMAL_RESPONSIBILITY')
    cost=sum(float(o['notional']) for o in xs)
    up=sum(float(o['shares']) for o in xs if o['side']=='UP')
    dn=sum(float(o['shares']) for o in xs if o['side']=='DOWN')
    return {'cost':cost,'upShares':up,'downShares':dn,'pnl':up-cost,'recoveryAction':action,'recoveredAtOrderIndex':recovered_at,'pendingShares':pending}

base=baseline()
# representative: first UP, first DOWN, REPAIR_UP
sel=[]
for want in [('NORMAL_UP',0),('NORMAL_DOWN',0),('REPAIR_UP',0)]:
    bucket,_=want
    for i,o in enumerate(orders):
        if o['bucket']==bucket:
            sel.append(i); break
rows=[]
for idx in sel:
    o=orders[idx]
    rows.append({'missedOrderIndex':idx,'missed':{k:o[k] for k in ['tMs','side','price','bucket','notional','shares']},'currentV0':current_v0_drop(idx),'faultAwareV1':fault_aware(idx)})
out={'version':'CAP100_ACCUMULATOR_NO_FILL_RECOVERY_1513668_V0','marketId':1513668,'baseline':{'cost':base[0],'upShares':base[1],'downShares':base[2],'pnl':base[3]},'cases':rows,'interpretationGuards':['Current accumulator V0 is only an intent compressor and has no order-fill feedback; therefore it does NOT automatically replace an unfilled emitted order.','FaultAwareV1 is a structural supervisor counterfactual, not historical proof: missed responsibility is requeued into the same bucket; repair responsibility without a later carrier escalates instead of silently disappearing.','Later-order price is used only for accounting sensitivity; actual venue fill probability is not asserted.']}
p=ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_accumulator_no_fill_recovery_1513668_v0.json'; p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
