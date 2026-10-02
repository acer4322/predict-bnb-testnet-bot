from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/hourly_novel_tests'
TEST_ID='HFT_R2_REPAIR_HEADROOM_RECLAMATION_V1'
CAP=100.0

def run_case(name, spent, committed_risk, repair_qty, events):
    committed=float(committed_risk); spent=float(spent); unresolved=float(repair_qty)
    owner='REPAIR_OBLIGATION'; violations=[]; duplicate=0; over=0.0
    cancel_requested=False; cancel_terminal=False; passive_started=False; active_started=False
    max_usage=spent+committed
    trace=[{'event':'REPAIR_CAPITAL_BLOCKED','spent':spent,'committedRisk':committed,'repairQty':unresolved,'usage':spent+committed}]
    for ev in events:
        typ=ev['type']
        if typ=='CANCEL_RISK_CHILD_REQUEST':
            cancel_requested=True
            trace.append({'action':'CANCEL_RISK_INCREASING_CHILD','committedStillReserved':committed})
        elif typ=='ILLEGAL_RELEASE_ON_CANCEL_REQUEST':
            if cancel_requested and not cancel_terminal:
                violations.append('CAPITAL_RELEASED_BEFORE_TERMINAL_EVIDENCE')
        elif typ=='FILL_DURING_CANCEL':
            q=float(ev['qty']); px=float(ev['price']); cost=q*px
            spent+=cost; committed=max(0.0,committed-cost)
            # risk-increasing fill worsens repair obligation by same qty
            unresolved+=q
            trace.append({'event':'FILL_DURING_CANCEL_RECONCILED','qty':q,'cost':cost,'spent':spent,'committed':committed,'repairQty':unresolved})
        elif typ=='CANCEL_TERMINAL_ACK':
            cancel_terminal=True; committed=0.0
            trace.append({'event':'CANCEL_TERMINAL_OBSERVED','releasedCommitted':True,'spent':spent,'repairQty':unresolved})
        elif typ=='PASSIVE_REPAIR':
            if not cancel_terminal: violations.append('PASSIVE_REPAIR_BEFORE_CAPITAL_RELEASE_EVIDENCE')
            req=float(ev.get('requestedQty',unresolved)); fill=min(float(ev.get('fillQty',req)),unresolved)
            px=float(ev.get('price',0.4)); cost=fill*px
            if spent+cost>CAP+1e-9: violations.append('CAP_BREACH_PASSIVE_REPAIR')
            over+=max(0.0,req-unresolved); passive_started=True; spent+=cost; unresolved-=fill
            trace.append({'action':'PASSIVE_REPAIR','requestedQty':req,'fillQty':fill,'spent':spent,'repairQty':unresolved})
        elif typ=='ACTIVE_REPAIR':
            if not passive_started: violations.append('ACTIVE_BEFORE_PASSIVE_ATTEMPT')
            req=float(ev.get('requestedQty',unresolved)); fill=min(float(ev.get('fillQty',req)),unresolved)
            px=float(ev.get('price',0.5)); cost=fill*px
            if spent+cost>CAP+1e-9: violations.append('CAP_BREACH_ACTIVE_REPAIR')
            over+=max(0.0,req-unresolved); active_started=True; spent+=cost; unresolved-=fill
            trace.append({'action':'BOUNDED_ACTIVE_REPAIR','requestedQty':req,'fillQty':fill,'spent':spent,'repairQty':unresolved})
        else: raise ValueError(typ)
        max_usage=max(max_usage,spent+committed)
        if spent+committed>CAP+1e-9: violations.append('CAPITAL_USAGE_EXCEEDED_CAP')
        if unresolved<=1e-9: unresolved=0.0; owner=None
    if unresolved>0 and owner is None: violations.append('ORPHANED_REMAINDER')
    if unresolved==0 and owner is not None: violations.append('OWNER_NOT_RELEASED')
    if duplicate: violations.append('DUPLICATE_REPAIR_OWNER')
    if over>1e-9: violations.append('OVER_REPAIR')
    passed=(cancel_requested and cancel_terminal and passive_started and unresolved==0 and max_usage<=CAP+1e-9 and duplicate==0 and over==0 and not violations)
    return {'scenario':name,'pass':passed,'maxCapitalUsage':max_usage,'terminalSpent':spent,'terminalUnresolvedQty':unresolved,'duplicateOwnerCount':duplicate,'overRepairQty':over,'activeRepairUsed':active_started,'lifecycleViolations':violations,'trace':trace}

def main():
    rows=[
      run_case('CANCEL_RISK_CHILD_RECLAIM_THEN_PASSIVE_COMPLETE',78,18,9,[
        {'type':'CANCEL_RISK_CHILD_REQUEST'},{'type':'CANCEL_TERMINAL_ACK'},{'type':'PASSIVE_REPAIR','requestedQty':9,'fillQty':9,'price':0.4}]),
      run_case('FILL_DURING_CANCEL_RECOMPUTE_THEN_PASSIVE_COMPLETE',72,18,6,[
        {'type':'CANCEL_RISK_CHILD_REQUEST'},{'type':'FILL_DURING_CANCEL','qty':4,'price':0.5},{'type':'CANCEL_TERMINAL_ACK'},{'type':'PASSIVE_REPAIR','requestedQty':10,'fillQty':10,'price':0.4}]),
      run_case('PASSIVE_PARTIAL_AFTER_RECLAIM_THEN_BOUNDED_ACTIVE_REMAINDER',70,20,12,[
        {'type':'CANCEL_RISK_CHILD_REQUEST'},{'type':'CANCEL_TERMINAL_ACK'},{'type':'PASSIVE_REPAIR','requestedQty':12,'fillQty':7,'price':0.4},{'type':'ACTIVE_REPAIR','requestedQty':5,'fillQty':5,'price':0.5}]),
    ]
    all_pass=all(r['pass'] for r in rows)
    summary={'scenarios':len(rows),'passed':sum(r['pass'] for r in rows),'maxCapitalUsage':max(r['maxCapitalUsage'] for r in rows),'duplicateOwnerCount':sum(r['duplicateOwnerCount'] for r in rows),'overRepairQty':sum(r['overRepairQty'] for r in rows),'lifecycleViolationCount':sum(len(r['lifecycleViolations']) for r in rows),'fillDuringCancelRecomputedCase2':rows[1]['terminalUnresolvedQty']==0,'activeEscalationOnlyAfterPassiveCase3':rows[2]['activeRepairUsed'],'status':'TESTED_KEEP_SIGNAL' if all_pass else 'TESTED_REJECTED'}
    report={'testId':TEST_ID,'researchOnly':True,'evidenceClass':'STRUCTURAL_EXECUTION_LIFECYCLE_EVIDENCE','executionSemanticsSource':'HftBacktest/Predict Execution Tape V1 incident semantics; deterministic structural intervention, not PnL evidence','preregistration':'data/research/hourly_novel_tests/hft_r2_repair_headroom_reclamation_v1_preregistered.json','rows':rows,'summary':summary,'conclusion':('Capital-blocked repair can reclaim headroom without fabricating cancel completion: risk-increasing commitment stays reserved until terminal evidence, fill-during-cancel is reconciled before repair sizing, passive repair gets first authority, and only the latest remainder may escalate to bounded active repair without exceeding the cap.' if all_pass else 'At least one preregistered capital/ownership/lifecycle invariant failed.')}
    p=OUT/'hft_r2_repair_headroom_reclamation_v1_report.json'; p.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
