from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_min1_responsibility_accumulator_1513668_v0.json'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/cap100_observable_carrier_viability_1513668_v2.json'
d=json.load(open(SRC,encoding='utf-8'))
orders=d['orders']

def bucket_class(b): return 'REPAIR' if str(b).startswith('REPAIR_') else 'NORMAL'

def decide_after_fault(i, fault):
    o=orders[i]; b=o['bucket']; cls=bucket_class(b)
    # Only current observable semantics. No future-search is used to choose action.
    if fault=='CANCEL_UNKNOWN':
        return {'action':'FREEZE_AND_RECONCILE','atMs':o['tMs'],'reason':'ORDER_OWNERSHIP_UNKNOWN'}
    if cls=='REPAIR':
        if fault in {'REJECT','NO_FILL_TIMEOUT'}:
            return {'action':'ACTIVE_CONVERSION_REQUIRED','atMs':o['tMs'],'reason':'NO_CONFIRMED_PASSIVE_REPAIR_CARRIER'}
        if fault=='PARTIAL50':
            return {'action':'PASSIVE_CARRY_REMAINDER','atMs':o['tMs'],'reason':'CONFIRMED_PARTIAL_CARRIER_EXISTS'}
    # Normal production can be requeued without active escalation.
    if fault in {'REJECT','NO_FILL_TIMEOUT','PARTIAL50'}:
        return {'action':'REQUEUE_SAME_BUCKET','atMs':o['tMs'],'reason':'NORMAL_PRODUCTION_RESPONSIBILITY'}
    return {'action':'HOLD_DEFINED','atMs':o['tMs'],'reason':'DEFINED_FALLBACK'}

cases=[]
# representative existing indices from prior curriculum
specs=[
 ('partial50_early_up',0,'PARTIAL50'),
 ('reject_early_down',3,'REJECT'),
 ('two_up_no_fill_first',0,'NO_FILL_TIMEOUT'),
 ('repair_up_reject',11,'REJECT'),
 ('repair_up_no_fill_timeout',11,'NO_FILL_TIMEOUT'),
 ('repair_up_partial50',11,'PARTIAL50'),
 ('cancel_unknown_early_down',3,'CANCEL_UNKNOWN'),
]
for name,i,f in specs:
    cases.append({'name':name,'orderIndex':i,'order':orders[i],'fault':f,'decision':decide_after_fault(i,f)})
report={
 'version':'CAP100_OBSERVABLE_CARRIER_VIABILITY_1513668_V2',
 'marketId':1513668,
 'policy':{
   'futurePredictionUsed':False,
   'fixedRepairDeadlineUsed':False,
   'repairCarrierRule':'A repair responsibility may remain passive only while a confirmed compatible passive carrier exists (e.g. partial/resting/accepted order). If the carrier is rejected or times out with no confirmed carrier, escalate immediately to ACTIVE_CONVERSION_REQUIRED.',
   'normalRule':'Normal production failures requeue to same bucket; no active conversion merely because a normal Maker order failed.',
   'unknownRule':'Ownership uncertainty freezes new exposure and reconciles before any reassignment.'
 },
 'cases':cases,
 'comparisonToV1':{
   'repairRejectV1EscalationAtMs':55793,
   'repairRejectV2EscalationAtMs':orders[11]['tMs'],
   'latencyReductionMs':55793-orders[11]['tMs']
 },
 'guards':[
   'No winner, future order, future fill, HFT survival prediction, or future Target action is used in the decision rule.',
   'This is a responsibility-state test, not a claim that immediate active conversion has positive PnL.',
   'A real implementation must distinguish accepted-resting, partial, rejected, expired, and ambiguous venue states from 8781 ground truth.'
 ]
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'out':str(OUT),'comparison':report['comparisonToV1'],'cases':[{'name':x['name'],'action':x['decision']['action'],'atMs':x['decision']['atMs']} for x in cases]},ensure_ascii=False,indent=2))
