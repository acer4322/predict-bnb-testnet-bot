from __future__ import annotations
import json, sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MID=1513668
SDB=sqlite3.connect(ROOT/'data'/'strategy_cap100_echtgeld_v1.db'); SDB.row_factory=sqlite3.Row
EDB=sqlite3.connect(ROOT/'data'/'echtgeld_engine_v1.db'); EDB.row_factory=sqlite3.Row

def j(s):
    try:return json.loads(s or '{}')
    except:return {}

decs=SDB.execute('select * from our_decisions where market_id=? order by decision_ms',(MID,)).fetchall()
fill_events=EDB.execute("select * from engine_cap100_events where source_market_id=? and event_type='FILL_DELTA' order by occurred_at_ms,seq",(MID,)).fetchall()
unresolved=None; wake=None; taker=None
for r in decs:
    p=j(r['payload_json']); acts=p.get('actions') or []
    for a in acts:
        if a.get('action')=='RESIDUAL_ARBITRATION_WAKE' and wake is None:wake=int(r['decision_ms'])
        if a.get('action')=='UNRESOLVED_GUARD_ENTER' and unresolved is None:unresolved=int(r['decision_ms'])
    if str(r['execution_choice'])=='TAKER' and taker is None:taker=int(r['decision_ms'])
row=SDB.execute('select * from our_decisions where market_id=? and decision_ms=?',(MID,unresolved)).fetchone()
pub=j(row['public_state_json']) if row else {}
portfolio=j(row['portfolio_state_json']) if row else {}
maker_rows=SDB.execute("select count(*) n,coalesce(sum(shares),0) s from our_fills where market_id=? and channel='MAKER'",(MID,)).fetchone()
orders=EDB.execute('select * from engine_cap100_orders where source_market_id=? order by created_at_ms',(MID,)).fetchall()
up=sum(float(x['filled_share_qty'] or 0) for x in orders if str(x['side'])=='UP')
down=sum(float(x['filled_share_qty'] or 0) for x in orders if str(x['side'])=='DOWN')
spent=sum(float(x['filled_usdt_amount'] or 0) for x in orders)
last_cancel=next((x for x in reversed(orders) if x['cancel_requested_at_ms']),None)
report={
 'marketId':MID,
 'historicalObserved':{
  'firstVenueFillMs':int(fill_events[0]['occurred_at_ms']) if fill_events else None,
  'residualWakeMs':wake,'unresolvedGuardMs':unresolved,'historicalTakerDecisionMs':taker,
  'unresolvedToHistoricalTakerMs':(taker-unresolved) if unresolved and taker else None,
  'finalUpShares':up,'finalDownShares':down,'capitalSpentUsdt':spent,'winner':'UP','pnlUsdt':up-spent,
  'rolloverOrphanMs':(int(last_cancel['completed_at_ms'])-int(last_cancel['cancel_requested_at_ms'])) if last_cancel and last_cancel['completed_at_ms'] else None,
 },
 'postFixDeterministicArbitration':{
  'activeInterventionRequiredAtMs':unresolved,
  'takerEligibilityRngRemovedForRequiredState':True,
  'pTaker1sRemainsObservationOnly':True,
  'requiredIntentSchedulingDelayMs':0,
  'visibleUpAskAtRequiredState':pub.get('predictUpAsk'),
  'visibleDownAskAtRequiredState':pub.get('predictDownAsk'),
  'counterfactualVenueFillPrice':None,
  'counterfactualVenueFillPriceReason':'Historical replay did not capture the Binance venue quote/orderbook that would have existed for a counterfactual order at this earlier timestamp; do not substitute public ask for a venue-confirmed fill.',
  'portfolioAtRequiredState':portfolio,
  'executionPriority':['LIVE_ORDER_RECONCILIATION_CANCEL_SAFETY','ACTIVE_TAKER_INTERVENTION_REQUIRED','PASSIVE_REPAIR_MAKER','NORMAL_MAKER_MAINTAIN_BURST'],
 },
 'recorder':{'makerFillDeltaRows':int(maker_rows['n']),'makerSharesRecorded':float(maker_rows['s']),'idempotencyKey':'client_order_id + engine FILL_DELTA seq'},
 'rolloverCancel':{'apiMethod':'BinancePredictionTradingClient.batch_cancel_orders_raw','uncertainCancelState':'CANCEL_UNKNOWN','uncertaintyFreezesEntries':True},
 'guards':{'paper8786DefaultActiveInterventionHook':False,'echtgeldOwner':'8781_ONLY','liveRestartPerformed':False}
}
out=ROOT/'data'/'research'/'cap100_market_1513668_postfix_report_v1.json'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'out':str(out),'requiredAtMs':unresolved,'oldDelayMs':report['historicalObserved']['unresolvedToHistoricalTakerMs'],'makerRows':report['recorder']['makerFillDeltaRows'],'pnl':report['historicalObserved']['pnlUsdt']},ensure_ascii=False,indent=2))
SDB.close(); EDB.close()
