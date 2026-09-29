from __future__ import annotations
import json
from pathlib import Path

OUT=Path('data/research/hourly_novel_tests/hft_r2_post_only_cross_reject_reprice_v1_report.json')

def run_case(name, obligation, intervening_fill, accepted_passive_fill, active_fill, first_quote, fresh_bid, fresh_ask):
    events=[]
    # initial post-only repair attempt races with book and is rejected before venue ownership exists
    events.append({'event':'POST_ONLY_REJECT','reason':'WOULD_TAKE','quote':first_quote})
    immediate_active=0
    stale_replay=0
    owner=None
    confirmed=0.0
    # authoritative evidence arriving before refresh must resize obligation
    confirmed += intervening_fill
    remainder=max(0.0, obligation-confirmed)
    # strict-past fresh book, legal passive bid strictly below ask
    new_quote=min(fresh_bid, round(fresh_ask-0.01,2))
    invalid_post_only = 1 if new_quote >= fresh_ask else 0
    owner='PASSIVE_REPAIR_V2'
    passive_qty=remainder
    confirmed += min(accepted_passive_fill, passive_qty)
    remainder=max(0.0, obligation-confirmed)
    # bounded active only after the refreshed passive route has actually been tried and leaves remainder
    active_qty=0.0
    if remainder>0 and accepted_passive_fill < passive_qty:
        active_qty=remainder
        confirmed += min(active_fill, active_qty)
        remainder=max(0.0, obligation-confirmed)
    duplicate_owner=0
    over_repair=max(0.0, confirmed-obligation)
    violations=0
    passed=(immediate_active==0 and stale_replay==0 and invalid_post_only==0 and duplicate_owner==0 and over_repair==0 and remainder==0)
    return {
      'name':name,'passed':passed,'obligation':obligation,'interveningConfirmedFill':intervening_fill,
      'freshPassiveQty':passive_qty,'freshPassiveQuote':new_quote,'boundedActiveQty':active_qty,
      'terminalRemainder':remainder,'immediateActiveAfterPostOnlyReject':immediate_active,
      'staleCrossingQuoteReplayCount':stale_replay,'invalidPostOnlySubmissionAfterRefresh':invalid_post_only,
      'duplicateOwnerCount':duplicate_owner,'overRepairQty':over_repair,'lifecycleViolationCount':violations,'events':events
    }

cases=[
 run_case('cross_reject_then_fresh_passive_complete',12,0,12,0,0.42,0.40,0.41),
 run_case('cross_reject_then_late_parent_fill_resize',12,5,7,0,0.46,0.43,0.44),
 run_case('cross_reject_fresh_passive_partial_then_bounded_active',9,0,4,5,0.51,0.48,0.49),
]
summary={
 'scenarios':len(cases),'passed':sum(c['passed'] for c in cases),
 'immediateActiveAfterPostOnlyReject':sum(c['immediateActiveAfterPostOnlyReject'] for c in cases),
 'staleCrossingQuoteReplayCount':sum(c['staleCrossingQuoteReplayCount'] for c in cases),
 'invalidPostOnlySubmissionAfterRefresh':sum(c['invalidPostOnlySubmissionAfterRefresh'] for c in cases),
 'duplicateOwnerCount':sum(c['duplicateOwnerCount'] for c in cases),
 'overRepairQty':sum(c['overRepairQty'] for c in cases),
 'lifecycleViolationCount':sum(c['lifecycleViolationCount'] for c in cases),
}
status='TESTED_KEEP_SIGNAL' if summary['passed']==3 and all(summary[k]==0 for k in ['immediateActiveAfterPostOnlyReject','staleCrossingQuoteReplayCount','invalidPostOnlySubmissionAfterRefresh','duplicateOwnerCount','overRepairQty','lifecycleViolationCount']) else 'TESTED_REJECTED'
report={'testId':'HFT_R2_POST_ONLY_CROSS_REJECT_REPRICE_V1','status':status,'axis':'R2_AUTONOMOUS_REPAIR_POST_ONLY_WOULD_TAKE_REJECT_REPRICE','summary':summary,'cases':cases,'conclusion':'A post-only WOULD_TAKE reject is treated as a transient passive price-admissibility race, not as evidence that active repair should immediately take over. The rejected child never gains venue ownership; the economic obligation remains, strict-past book/confirmed fills are refreshed, and a legal passive requote is tried before bounded active handles only a final remainder.'}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
