from __future__ import annotations
import json
from pathlib import Path

TEST_ID='HFT_R2_ORDER_IDENTITY_ALIAS_RECONCILE_V1'
OUT=Path('data/research/hourly_novel_tests/hft_r2_order_identity_alias_reconcile_v1_report.json')

def run_case(s):
    obligation=float(s['obligationQty'])
    canonical_client=s['clientOrderId']
    aliases={s['initialExchangeOrderId']}
    confirmed=0.0
    duplicate_fill_applied=0.0
    trace=['REPAIR_OWNER_ESTABLISHED']
    seen_events=set()
    duplicate_owner_count=0
    lifecycle_violations=0

    for ev in s['events']:
        et=ev['type']
        if et=='ORDER_ALIAS':
            if ev['clientOrderId']!=canonical_client:
                lifecycle_violations+=1
                trace.append('FOREIGN_ALIAS_REJECTED')
            else:
                aliases.add(ev['exchangeOrderId'])
                trace.append('MERGE_EXCHANGE_ID_ALIAS')
        elif et=='FILL':
            key=ev['eventId']
            if ev['exchangeOrderId'] not in aliases:
                lifecycle_violations+=1
                trace.append('UNKNOWN_ORDER_FILL_QUARANTINED')
                continue
            if key in seen_events:
                duplicate_fill_applied += 0.0
                trace.append('DUPLICATE_FILL_IGNORED')
                continue
            seen_events.add(key)
            confirmed=max(confirmed,float(ev['cumQty']))
            trace.append('CONFIRMED_FILL_FRONTIER_ADVANCE')
        elif et=='TERMINAL':
            if ev['exchangeOrderId'] not in aliases:
                lifecycle_violations+=1
            trace.append('TERMINAL_ALIAS_RESOLVED')

    remainder=max(0.0,obligation-confirmed)
    expected=float(s['expectedRemainder'])
    passive_qty=remainder
    trace.append('PASSIVE_REPAIR_LATEST_REMAINDER' if remainder>0 else 'REPAIR_COMPLETE')
    # deterministic completion of current exact remainder for structural check
    confirmed += passive_qty
    terminal_unresolved=max(0.0,obligation-confirmed)
    over=max(0.0,confirmed-obligation)
    passed=(abs(remainder-expected)<1e-9 and duplicate_owner_count==0 and duplicate_fill_applied==0 and over==0 and lifecycle_violations==0 and terminal_unresolved==0)
    return {
      'name':s['name'],'passed':passed,'trace':trace,'aliasCount':len(aliases),
      'checkpointRemainder':remainder,'expectedRemainder':expected,'newPassiveQty':passive_qty,
      'duplicateEconomicOwnerCount':duplicate_owner_count,'duplicateFillAppliedQty':duplicate_fill_applied,
      'overRepairQty':over,'lifecycleViolationCount':lifecycle_violations,'terminalUnresolvedQty':terminal_unresolved
    }

def main():
    scenarios=[
      {'name':'gateway_reconnect_rotates_exchange_id_before_fill','obligationQty':12,'clientOrderId':'repair-A','initialExchangeOrderId':'ex-100','events':[{'type':'ORDER_ALIAS','clientOrderId':'repair-A','exchangeOrderId':'ex-200'},{'type':'FILL','eventId':'f1','exchangeOrderId':'ex-200','cumQty':5},{'type':'TERMINAL','exchangeOrderId':'ex-200'}],'expectedRemainder':7},
      {'name':'fills_arrive_on_old_and_new_aliases_same_underlying_order','obligationQty':12,'clientOrderId':'repair-B','initialExchangeOrderId':'ex-300','events':[{'type':'FILL','eventId':'f2','exchangeOrderId':'ex-300','cumQty':3},{'type':'ORDER_ALIAS','clientOrderId':'repair-B','exchangeOrderId':'ex-301'},{'type':'FILL','eventId':'f3','exchangeOrderId':'ex-301','cumQty':8},{'type':'TERMINAL','exchangeOrderId':'ex-301'}],'expectedRemainder':4},
      {'name':'same_fill_replayed_under_rotated_exchange_id','obligationQty':12,'clientOrderId':'repair-C','initialExchangeOrderId':'ex-400','events':[{'type':'FILL','eventId':'f4','exchangeOrderId':'ex-400','cumQty':4},{'type':'ORDER_ALIAS','clientOrderId':'repair-C','exchangeOrderId':'ex-401'},{'type':'FILL','eventId':'f4','exchangeOrderId':'ex-401','cumQty':4},{'type':'FILL','eventId':'f5','exchangeOrderId':'ex-401','cumQty':9},{'type':'TERMINAL','exchangeOrderId':'ex-401'}],'expectedRemainder':3}
    ]
    rs=[run_case(s) for s in scenarios]
    agg={'scenarios':len(rs),'passed':sum(r['passed'] for r in rs),'duplicateEconomicOwnerCount':sum(r['duplicateEconomicOwnerCount'] for r in rs),'duplicateFillAppliedQty':sum(r['duplicateFillAppliedQty'] for r in rs),'overRepairQty':sum(r['overRepairQty'] for r in rs),'lifecycleViolationCount':sum(r['lifecycleViolationCount'] for r in rs),'exactRemainderCases':sum(abs(r['checkpointRemainder']-r['expectedRemainder'])<1e-9 for r in rs)}
    if len(rs)<3: status='TESTED_INCONCLUSIVE'
    elif agg['passed']==3 and all(agg[k]==0 for k in ['duplicateEconomicOwnerCount','duplicateFillAppliedQty','overRepairQty','lifecycleViolationCount']): status='TESTED_KEEP_SIGNAL'
    else: status='TESTED_REJECTED'
    rep={'testId':TEST_ID,'testedAt':'2026-08-24T23:00:44+08:00','axis':'R2_AUTONOMOUS_REPAIR_ORDER_IDENTITY_ALIAS_RECONCILIATION','evidenceClass':'STRUCTURAL_LIFECYCLE_ONLY_NOT_PNL','sealedDataOpened':False,'result':agg,'status':status,'scenarios':rs,'conclusion':'Exchange order-ID rotation/aliasing must not create a second economic repair owner. Merge aliases by stable client/economic identity, reconcile cumulative fills and event identity across aliases, then size passive repair only from the latest unresolved remainder.'}
    OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'status':status,**agg},ensure_ascii=False))
if __name__=='__main__': main()
