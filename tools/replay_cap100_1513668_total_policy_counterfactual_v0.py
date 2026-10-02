from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MID=1513668
S=sqlite3.connect(ROOT/'data'/'strategy_cap100_echtgeld_v1.db'); S.row_factory=sqlite3.Row
E=sqlite3.connect(ROOT/'data'/'echtgeld_engine_v1.db'); E.row_factory=sqlite3.Row

def j(x):
    try:return json.loads(x or '{}')
    except:return {}

decs=S.execute('select * from our_decisions where market_id=? order by decision_ms',(MID,)).fetchall()
unresolved=None; hist_taker=None
for r in decs:
    p=j(r['payload_json']); acts=p.get('actions') or []
    if any(a.get('action')=='UNRESOLVED_GUARD_ENTER' for a in acts) and unresolved is None:
        unresolved=int(r['decision_ms'])
    if str(r['execution_choice'])=='TAKER' and hist_taker is None:
        hist_taker=int(r['decision_ms'])

orders=E.execute('select * from engine_cap100_orders where source_market_id=? order by created_at_ms',(MID,)).fetchall()
# Confirmed inventory immediately before unresolved.
fill_events=E.execute("select * from engine_cap100_events where source_market_id=? and event_type='FILL_DELTA' and occurred_at_ms<=? order by occurred_at_ms,seq",(MID,unresolved)).fetchall()
up=down=0.0
for ev in fill_events:
    side=str(ev['side']); q=float(ev['delta_shares'] or 0)
    if side=='UP': up+=q
    elif side=='DOWN': down+=q

row=S.execute('select * from our_decisions where market_id=? and decision_ms=?',(MID,unresolved)).fetchone()
pub=j(row['public_state_json']); portfolio=j(row['portfolio_state_json']); payload=j(row['payload_json'])
# Determine repair side from confirmed inventory, not model draw.
repair_side='UP' if down>up else 'DOWN' if up>down else None
ask = pub.get('predictUpAsk') if repair_side=='UP' else pub.get('predictDownAsk') if repair_side=='DOWN' else None
# Historical known final non-taker cashflow from engine order executions.
maker_cost=0.0; maker_up=maker_down=0.0
for o in orders:
    if str(o['role'])!='MAKER': continue
    q=float(o['filled_share_qty'] or 0); amt=float(o['filled_usdt_amount'] or 0)
    maker_cost += amt
    if str(o['side'])=='UP': maker_up+=q
    elif str(o['side'])=='DOWN': maker_down+=q
hist_taker_order=next((o for o in orders if str(o['role'])=='TAKER' and float(o['filled_share_qty'] or 0)>0),None)
hist_taker_qty=float(hist_taker_order['filled_share_qty'] or 0) if hist_taker_order else 0.0
hist_taker_cost=float(hist_taker_order['filled_usdt_amount'] or 0) if hist_taker_order else 0.0
# Fee estimate from historical taker execution audit: 2% collateral fee on notional.
fee_rate=0.02
# Counterfactual uses same 17.99 qty to isolate timing/price effect; public ask is NOT venue-confirmed.
cf_qty=hist_taker_qty
if ask is not None and math.isfinite(float(ask)):
    cf_principal=cf_qty*float(ask)
    cf_fee=cf_principal*fee_rate
    cf_cost=cf_principal+cf_fee
    # winner UP. Maker final confirmed inventory remains historical; only taker timing price differs.
    cf_payout=maker_up + (cf_qty if repair_side=='UP' else 0.0)
    cf_pnl=cf_payout-(maker_cost+cf_cost)
else:
    cf_principal=cf_fee=cf_cost=cf_payout=cf_pnl=None

# conservative price sensitivity because public ask != guaranteed venue fill
sens=[]
if ask is not None:
    for px in [float(ask), min(0.99,float(ask)+0.02), min(0.99,float(ask)+0.05), min(0.99,float(ask)+0.10)]:
        principal=cf_qty*px; fee=principal*fee_rate; cost=principal+fee
        payout=maker_up + (cf_qty if repair_side=='UP' else 0.0)
        sens.append({'assumedFillPrice':px,'pnlUsdt':payout-(maker_cost+cost)})

out={
 'version':'CAP100_1513668_TOTAL_POLICY_COUNTERFACTUAL_V0',
 'marketId':MID,
 'unresolvedGuardMs':unresolved,
 'historicalTakerDecisionMs':hist_taker,
 'historicalDelayAfterGuardMs': hist_taker-unresolved if unresolved and hist_taker else None,
 'confirmedInventoryAtGuard':{'upShares':up,'downShares':down,'net':up-down,'repairSide':repair_side},
 'publicStateAtGuard':{'upAsk':pub.get('predictUpAsk'),'downAsk':pub.get('predictDownAsk'),'secondsLeft':pub.get('secondsLeft') or pub.get('seconds_left')},
 'historical':{'makerUp':maker_up,'makerDown':maker_down,'makerCostUsdt':maker_cost,'takerQty':hist_taker_qty,'takerCostUsdt':hist_taker_cost,'winner':'UP','pnlUsdt':-17.65},
 'counterfactual':{
   'policy':'UNRESOLVED_GUARD => deterministic ACTIVE_CONVERSION immediately; no RNG gating',
   'qtyAssumption':'reuse historical taker filled quantity solely to isolate delay/price effect',
   'priceAssumption':'strict-past public ask at guard; NOT venue-confirmed, therefore diagnostic only',
   'repairSide':repair_side,
   'publicAskAtGuard':ask,
   'estimatedPnlUsdt':cf_pnl,
   'priceSensitivity':sens
 },
 'limits':[
   'Historical feed does not contain the counterfactual venue queue/orderbook for an order that was never submitted at the guard timestamp.',
   'This replay can estimate the economic cost of waiting but cannot prove the exact fill price or fill probability of an earlier live Taker order.',
   'Maker fills after the guard are kept at their historical realized path; a real earlier Taker could have changed subsequent controller actions.'
 ]
}
path=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'/'cap100_1513668_total_policy_counterfactual_v0.json'
path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
S.close(); E.close()
