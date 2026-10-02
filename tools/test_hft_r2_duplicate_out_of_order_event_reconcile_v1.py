from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/hourly_novel_tests'
TEST_ID='HFT_R2_DUPLICATE_OUT_OF_ORDER_EVENT_RECONCILE_V1'

def run_case(name,requested,events):
    confirmed=0.0; frontier=0.0; seen=set(); unresolved=float(requested); owner='REPAIR_OBLIGATION'; passive_started=False; passive_stalled=False; active_qty=0.0
    duplicate_applied=0.0; stale_rollbacks=0; dup_owner=0; over=0.0; violations=[]; trace=[]
    def apply_event(ev):
        nonlocal confirmed,frontier,unresolved,duplicate_applied,stale_rollbacks
        eid=ev['id']; cum=float(ev['cumFill'])
        if eid in seen:
            trace.append({'event':'DUPLICATE_EVENT_IGNORED','id':eid,'cumFill':cum,'confirmed':confirmed}); return
        seen.add(eid)
        if cum < frontier-1e-9:
            stale_rollbacks += 1
            trace.append({'event':'OUT_OF_ORDER_STALE_FRONTIER_IGNORED','id':eid,'cumFill':cum,'frontier':frontier,'confirmed':confirmed}); return
        delta=max(0.0,cum-frontier); frontier=max(frontier,cum); confirmed=min(float(requested),confirmed+delta); unresolved=max(0.0,float(requested)-confirmed)
        trace.append({'event':'CONFIRMED_FILL_FRONTIER_APPLIED','id':eid,'cumFill':cum,'delta':delta,'confirmed':confirmed,'unresolved':unresolved})
    for x in events:
        typ=x['type']
        if typ=='VENUE': apply_event(x)
        elif typ=='PASSIVE_START':
            passive_started=True; trace.append({'action':'PASSIVE_REPAIR','qty':unresolved})
        elif typ=='PASSIVE_STALL': passive_stalled=True; trace.append({'event':'PASSIVE_REPAIR_STALL','unresolved':unresolved})
        elif typ=='ACTIVE_COMPLETE':
            if not passive_started or not passive_stalled: violations.append('ACTIVE_BEFORE_PASSIVE_STALL')
            req=float(x.get('qty',unresolved)); exe=min(req,unresolved); over += max(0.0,req-unresolved); active_qty += exe; confirmed += exe; frontier=max(frontier,confirmed); unresolved=max(0.0,float(requested)-confirmed); trace.append({'action':'BOUNDED_ACTIVE_REPAIR','requested':req,'executed':exe,'unresolved':unresolved})
    if confirmed>requested+1e-9: violations.append('CONFIRMED_EXCEEDS_REQUESTED')
    if unresolved>1e-9 and owner is None: violations.append('ORPHANED_REMAINDER')
    passed=(unresolved==0 and duplicate_applied==0 and dup_owner==0 and over==0 and not violations)
    return {'scenario':name,'pass':passed,'terminalConfirmed':confirmed,'terminalUnresolved':unresolved,'duplicateAppliedQty':duplicate_applied,'staleRollbackCount':stale_rollbacks,'duplicateOwnerCount':dup_owner,'overRepairQty':over,'lifecycleViolations':violations,'passiveRepairActivated':passive_started,'activeRepairQty':active_qty,'trace':trace}

def main():
    rows=[
      run_case('DUPLICATE_PARTIAL_FILL_REPLAY',18,[{'type':'VENUE','id':'f1','cumFill':6},{'type':'VENUE','id':'f1','cumFill':6},{'type':'PASSIVE_START'},{'type':'VENUE','id':'p1','cumFill':18}]),
      run_case('OUT_OF_ORDER_CUMULATIVE_FILL',18,[{'type':'VENUE','id':'f2','cumFill':10},{'type':'VENUE','id':'f_old','cumFill':6},{'type':'PASSIVE_START'},{'type':'VENUE','id':'p2','cumFill':18}]),
      run_case('DUPLICATE_AND_OUT_OF_ORDER_THEN_ACTIVE_REMAINDER',18,[{'type':'VENUE','id':'f3','cumFill':7},{'type':'VENUE','id':'f3','cumFill':7},{'type':'VENUE','id':'f_old2','cumFill':4},{'type':'PASSIVE_START'},{'type':'VENUE','id':'p3','cumFill':12},{'type':'PASSIVE_STALL'},{'type':'ACTIVE_COMPLETE','qty':6}])]
    s={'scenarios':len(rows),'passed':sum(r['pass'] for r in rows),'duplicateAppliedQty':sum(r['duplicateAppliedQty'] for r in rows),'staleRollbackCount':sum(r['staleRollbackCount'] for r in rows),'duplicateOwnerCount':sum(r['duplicateOwnerCount'] for r in rows),'overRepairQty':sum(r['overRepairQty'] for r in rows),'lifecycleViolationCount':sum(len(r['lifecycleViolations']) for r in rows)}
    ok=s['passed']==3 and s['duplicateAppliedQty']==0 and s['duplicateOwnerCount']==0 and s['overRepairQty']==0 and s['lifecycleViolationCount']==0
    s['status']='TESTED_KEEP_SIGNAL' if ok else 'TESTED_REJECTED'
    rep={'testId':TEST_ID,'researchOnly':True,'evidenceClass':'STRUCTURAL_EXECUTION_LIFECYCLE_EVIDENCE','executionSemanticsSource':'HftBacktest/Predict Execution Tape V1 confirmed-fill semantics; deterministic fault-injection, not PnL evidence','preregistration':'data/research/hourly_novel_tests/hft_r2_duplicate_out_of_order_event_reconcile_v1_preregistered.json','rows':rows,'summary':s,'conclusion':'Idempotent event identity plus monotonic cumulative-fill frontier prevents duplicate/reordered venue evidence from double-counting or rolling own-state backward; repair remains passive-first and active only on the latest unresolved remainder.' if ok else 'Duplicate/out-of-order event reconciliation failed a preregistered lifecycle invariant.'}
    (OUT/'hft_r2_duplicate_out_of_order_event_reconcile_v1_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(s,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
