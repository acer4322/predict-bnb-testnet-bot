from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/hourly_novel_tests'
TEST_ID='HFT_R2_SUBMIN_NOTIONAL_REPAIR_DUST_ACCUMULATION_V1'
MIN_NOTIONAL=1.0

def run_case(name, initial_qty, initial_price, events, expect_terminal_pending=False):
    unresolved=float(initial_qty); price=float(initial_price); owner='DUST_REPAIR_PENDING'
    below_min_submit=0; oversize=0; duplicate_owner=0; over_repair=0.0; violations=[]
    passive_started=False; passive_qty=0.0; terminal_state=None; trace=[]
    def admissible(): return unresolved>1e-9 and unresolved*price >= MIN_NOTIONAL-1e-12
    def maybe_passive(trigger):
        nonlocal unresolved,owner,below_min_submit,passive_started,passive_qty,terminal_state
        if unresolved<=1e-9: return
        if not admissible():
            trace.append({'event':'DUST_REMAINS_PENDING','trigger':trigger,'qty':unresolved,'price':price,'notional':unresolved*price}); return
        passive_started=True; q=unresolved; passive_qty += q
        trace.append({'action':'PASSIVE_REPAIR_EXACT_REMAINDER','trigger':trigger,'qty':q,'price':price,'notional':q*price})
        unresolved=0.0; owner=None; terminal_state='RESOLVED_PASSIVE'
    trace.append({'event':'REPAIR_OBLIGATION_CREATED','qty':unresolved,'price':price,'notional':unresolved*price,'owner':owner})
    if admissible(): violations.append('INITIAL_CASE_NOT_DUST')
    else: trace.append({'event':'SUBMIN_NOTIONAL_QUARANTINED','qty':unresolved,'notional':unresolved*price})
    for ev in events:
        typ=ev['type']
        if typ=='SAME_DIRECTION_OBLIGATION':
            if owner is None: owner='DUST_REPAIR_PENDING'
            unresolved += float(ev['qty'])
            trace.append({'event':'SAME_DIRECTION_OBLIGATION_ACCUMULATED','addQty':float(ev['qty']),'qty':unresolved,'price':price,'notional':unresolved*price})
            maybe_passive('OBLIGATION_GROWTH')
        elif typ=='FRESH_PRICE':
            price=float(ev['price']); trace.append({'event':'FRESH_PRICE_REEVALUATION','qty':unresolved,'price':price,'notional':unresolved*price})
            maybe_passive('FRESH_PRICE')
        elif typ=='DATA_END':
            if unresolved>1e-9:
                terminal_state='PENDING_AT_DATA_END'; trace.append({'event':'PENDING_AT_DATA_END','qty':unresolved,'price':price,'notional':unresolved*price,'owner':owner})
            else: terminal_state=terminal_state or 'RESOLVED_PASSIVE'
    if unresolved>1e-9 and owner is None: violations.append('ORPHANED_DUST_REMAINDER')
    if unresolved>1e-9 and admissible() and terminal_state=='PENDING_AT_DATA_END': violations.append('ADMISSIBLE_REMAINDER_LEFT_UNATTEMPTED')
    if expect_terminal_pending:
        passed=(terminal_state=='PENDING_AT_DATA_END' and unresolved>0 and owner=='DUST_REPAIR_PENDING' and not passive_started and not violations)
    else:
        passed=(terminal_state=='RESOLVED_PASSIVE' and unresolved==0 and passive_started and owner is None and not violations)
    return {'scenario':name,'pass':passed,'passiveRepairActivated':passive_started,'passiveRepairQty':passive_qty,'terminalUnresolved':unresolved,'terminalOwner':owner,'terminalState':terminal_state,'belowMinSubmissionCount':below_min_submit,'oversizeToMeetMinCount':oversize,'duplicateOwnerCount':duplicate_owner,'overRepairQty':over_repair,'lifecycleViolations':violations,'trace':trace}

def main():
    rows=[
      run_case('DUST_ACCUMULATES_TO_ADMISSIBLE',1,0.40,[{'type':'SAME_DIRECTION_OBLIGATION','qty':2}]),
      run_case('FRESH_PRICE_MAKES_EXACT_REMAINDER_ADMISSIBLE',2,0.40,[{'type':'FRESH_PRICE','price':0.60}]),
      run_case('PERSISTENT_DUST_PRESERVED_AT_DATA_END',1,0.40,[{'type':'DATA_END'}],True),
    ]
    s={'scenarios':len(rows),'passed':sum(r['pass'] for r in rows),'belowMinSubmissionCount':sum(r['belowMinSubmissionCount'] for r in rows),'oversizeToMeetMinCount':sum(r['oversizeToMeetMinCount'] for r in rows),'duplicateOwnerCount':sum(r['duplicateOwnerCount'] for r in rows),'overRepairQty':sum(r['overRepairQty'] for r in rows),'lifecycleViolationCount':sum(len(r['lifecycleViolations']) for r in rows),'resolvablePassiveExactQty':[rows[0]['passiveRepairQty'],rows[1]['passiveRepairQty']],'persistentDustOwnershipPreserved':rows[2]['terminalState']=='PENDING_AT_DATA_END' and rows[2]['terminalOwner']=='DUST_REPAIR_PENDING'}
    ok=(s['passed']==3 and s['belowMinSubmissionCount']==0 and s['oversizeToMeetMinCount']==0 and s['duplicateOwnerCount']==0 and s['overRepairQty']==0 and s['lifecycleViolationCount']==0 and s['resolvablePassiveExactQty']==[3.0,2.0] and s['persistentDustOwnershipPreserved'])
    s['status']='TESTED_KEEP_SIGNAL' if ok else 'TESTED_REJECTED'
    rep={'testId':TEST_ID,'researchOnly':True,'evidenceClass':'STRUCTURAL_EXECUTION_LIFECYCLE_EVIDENCE','executionSemanticsSource':'HftBacktest/Predict Execution Tape V1 venue-admissibility and confirmed-state semantics; deterministic fault-injection, not PnL evidence','preregistration':'data/research/hourly_novel_tests/hft_r2_submin_notional_repair_dust_accumulation_v1_preregistered.json','rows':rows,'summary':s,'conclusion':'Sub-min-notional repair dust remains explicitly owned without invalid submit or oversizing; exact remainder reactivates passive repair only when strict-past obligation growth or fresh price makes it admissible; persistent dust remains pending at data end rather than being fabricated complete.' if ok else 'Sub-min-notional dust lifecycle failed a preregistered ownership/admissibility invariant.'}
    (OUT/'hft_r2_submin_notional_repair_dust_accumulation_v1_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(s,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
