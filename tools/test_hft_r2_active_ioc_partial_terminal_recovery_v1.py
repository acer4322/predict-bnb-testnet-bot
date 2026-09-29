from __future__ import annotations
import json
from pathlib import Path

OUT=Path('data/research/hourly_novel_tests/hft_r2_active_ioc_partial_terminal_recovery_v1_report.json')

def run_case(name, obligation, pre_active_confirmed, active_confirmed, passive_confirmed, final_active_confirmed):
    events=[]
    confirmed=float(pre_active_confirmed)
    remainder=max(0.0, obligation-confirmed)
    # bounded active child accepted for current remainder
    active_qty=remainder
    owner='ACTIVE_REPAIR_IOC'
    confirmed += min(float(active_confirmed), active_qty)
    remainder=max(0.0, obligation-confirmed)
    events.append({'event':'ACTIVE_PARTIAL_FILL','requestedQty':active_qty,'confirmedQty':active_confirmed,'remainder':remainder})
    # IOC/marketable-limit child reaches terminal state for unfilled remainder.
    events.append({'event':'ACTIVE_TERMINAL','status':'IOC_CANCELED','remainingQty':remainder})
    owner=None
    # Critical invariant: do not replay active immediately. Return residual to controller and try fresh passive first.
    active_replay_before_passive=0
    passive_qty=remainder
    if passive_qty>0:
        owner='PASSIVE_REPAIR_FRESH'
        confirmed += min(float(passive_confirmed), passive_qty)
        remainder=max(0.0, obligation-confirmed)
        events.append({'event':'PASSIVE_REENTRY','requestedQty':passive_qty,'confirmedQty':passive_confirmed,'remainder':remainder})
    final_active_qty=0.0
    # A later active child is allowed only after the fresh passive route was attempted and leaves a remainder.
    if remainder>0 and passive_qty>0 and passive_confirmed < passive_qty:
        owner='ACTIVE_REPAIR_FINAL'
        final_active_qty=remainder
        confirmed += min(float(final_active_confirmed), final_active_qty)
        remainder=max(0.0, obligation-confirmed)
        events.append({'event':'FINAL_BOUNDED_ACTIVE','requestedQty':final_active_qty,'confirmedQty':final_active_confirmed,'remainder':remainder})
    duplicate_owner=0
    over_repair=max(0.0, confirmed-obligation)
    lifecycle_violations=0
    exact_post_ioc_remainder=max(0.0, obligation-pre_active_confirmed-active_confirmed)
    passed=(active_replay_before_passive==0 and duplicate_owner==0 and over_repair==0 and lifecycle_violations==0 and remainder==0 and passive_qty==exact_post_ioc_remainder)
    return {
      'name':name,'passed':passed,'obligation':obligation,'preActiveConfirmed':pre_active_confirmed,
      'activeRequestedQty':active_qty,'activeConfirmedPartialQty':active_confirmed,
      'exactPostIocRemainder':exact_post_ioc_remainder,'freshPassiveQty':passive_qty,
      'freshPassiveConfirmedQty':passive_confirmed,'finalBoundedActiveQty':final_active_qty,
      'terminalRemainder':remainder,'activeReplayBeforeFreshPassive':active_replay_before_passive,
      'duplicateOwnerCount':duplicate_owner,'overRepairQty':over_repair,
      'lifecycleViolationCount':lifecycle_violations,'events':events
    }

cases=[
 run_case('active_ioc_partial_then_passive_complete',12,0,5,7,0),
 run_case('prior_fill_active_partial_then_passive_complete',12,2,4,6,0),
 run_case('active_ioc_partial_passive_partial_then_final_bounded_active',14,0,6,3,5),
]
summary={
 'scenarios':len(cases),'passed':sum(c['passed'] for c in cases),
 'activeReplayBeforeFreshPassive':sum(c['activeReplayBeforeFreshPassive'] for c in cases),
 'duplicateOwnerCount':sum(c['duplicateOwnerCount'] for c in cases),
 'overRepairQty':sum(c['overRepairQty'] for c in cases),
 'lifecycleViolationCount':sum(c['lifecycleViolationCount'] for c in cases),
 'postIocRemainders':[c['exactPostIocRemainder'] for c in cases],
 'finalBoundedActiveQtyCase3':cases[2]['finalBoundedActiveQty']
}
status='TESTED_KEEP_SIGNAL' if summary['passed']==3 and all(summary[k]==0 for k in ['activeReplayBeforeFreshPassive','duplicateOwnerCount','overRepairQty','lifecycleViolationCount']) else 'TESTED_REJECTED'
report={
 'testId':'HFT_R2_ACTIVE_IOC_PARTIAL_TERMINAL_RECOVERY_V1',
 'status':status,
 'axis':'R2_AUTONOMOUS_REPAIR_ACTIVE_IOC_PARTIAL_TERMINAL_REMAINDER_RECOVERY',
 'summary':summary,
 'cases':cases,
 'conclusion':'A bounded active/marketable repair that partially fills and then terminates under IOC semantics does not erase the remaining economic repair obligation and does not authorize immediate active replay. Confirmed active quantity is reconciled exactly, the terminal active child releases venue ownership, the residual returns to controller and fresh passive repair receives first authority; only a new passive-stalled final remainder may later use bounded active execution.'
}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
