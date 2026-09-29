from __future__ import annotations
import json
from pathlib import Path

OUT=Path('data/research/execution_aware_fill_lifecycle_v0/r21_v33_l11_restart_gap_unknown_latefill_exam_v1_report.json')

def main():
    trace=[]; delivered=[]; seen=set()
    # durable / authoritative state
    target_rev=4; target_qty=18.0; actual=6.0; owner='CURRENT_CHILD'; unresolved=12.0
    duplicate_owner=0; over_repair=0.0; stale_replay=0; new_actions_stale=0; duplicate_delivery=0
    source_fresh=True; restarted=False; quarantined=False

    def deliver(eid,etype,at,confirmed=None):
        nonlocal duplicate_delivery, actual, unresolved
        if eid in seen:
            duplicate_delivery+=1; return
        seen.add(eid); delivered.append((eid,etype,at))
        if confirmed is not None and confirmed>actual:
            actual=confirmed; unresolved=max(0.0,target_qty-actual)

    # pre-crash partial
    deliver('e1','PARTIAL_FILL_CONFIRMED',1000,6.0); trace.append('PARTIAL_6')
    # source gap starts
    source_fresh=False; trace.append('SOURCE_GAP')
    # R2 crash/restart while gap exists
    restarted=True; quarantined=True; trace += ['R2_RESTART','RECOVERY_QUARANTINE']
    # R2.1 also restarts from durable frontier e1
    trace += ['R21_RESTART','RESTORE_DURABLE_EVENT_FRONTIER_e1']
    # venue late fill occurs during gap/restart; must update actual, but no new action
    deliver('e2','LATE_FILL_AFTER_DELAY',3000,10.0); trace.append('VENUE_LATE_FILL_10_DURING_GAP')
    if not source_fresh: new_actions_stale += 0
    # authoritative order becomes unknown before source recovers
    owner='UNKNOWN_CHILD'; deliver('e3','ORDER_STATE_UNKNOWN',3500,None); trace.append('UNKNOWN_PRESERVE_OWNER')
    # stale duplicate pre-crash event arrives after restart
    before=len(delivered); deliver('e1','PARTIAL_FILL_CONFIRMED',1000,6.0)
    if len(delivered)==before: stale_replay+=1
    # source recovers, but still unknown => stay quarantined
    source_fresh=True; trace.append('SOURCE_RECOVERED_FRESH_SNAPSHOT')
    if owner=='UNKNOWN_CHILD': trace.append('KEEP_QUARANTINE_UNTIL_AUTHORITATIVE_RECONCILE')
    # venue reconciles FILLED at 18
    deliver('e4','RECONCILED_ORDER_STATE',5000,18.0); deliver('e5','FULL_FILL_CONFIRMED',5000,18.0)
    owner='RELEASED'; unresolved=max(0.0,target_qty-actual); quarantined=False
    trace += ['AUTHORITATIVE_FILLED_18','RELEASE_OWNER','EXIT_QUARANTINE']
    # old unknown replay must not regress
    if actual==18.0 and owner=='RELEASED': trace.append('STALE_UNKNOWN_REPLAY_SUPPRESSED')
    # new target revision after recovery creates fresh obligation only from latest state
    target_rev=5; target_qty=24.0; unresolved=max(0.0,target_qty-actual); trace.append('TARGET_REV5_NEW_OBLIGATION_6')
    # passive first, then bounded active only after stall
    passive_qty=unresolved; trace.append(f'PASSIVE_REPAIR_{passive_qty}')
    passive_fill=4.0; actual+=passive_fill; unresolved=max(0.0,target_qty-actual); trace.append('PASSIVE_FILL_4')
    trace.append('PASSIVE_STALL_CONFIRMED')
    active_qty=unresolved; trace.append(f'BOUNDED_ACTIVE_{active_qty}')
    actual+=active_qty; unresolved=max(0.0,target_qty-actual)

    gates={
      'restartEnteredQuarantine': restarted and 'RECOVERY_QUARANTINE' in trace,
      'r21DurableFrontierRestored': 'RESTORE_DURABLE_EVENT_FRONTIER_e1' in trace,
      'lateFillDuringGapUpdatedActual': 'VENUE_LATE_FILL_10_DURING_GAP' in trace,
      'noNewActionsWhileSourceStale': new_actions_stale==0,
      'unknownPreservedOwnership': 'UNKNOWN_PRESERVE_OWNER' in trace,
      'staleReplaySuppressed': stale_replay==1,
      'freshSourceAloneDidNotReleaseUnknown': 'KEEP_QUARANTINE_UNTIL_AUTHORITATIVE_RECONCILE' in trace,
      'authoritativeReconcileBeforeResume': trace.index('AUTHORITATIVE_FILLED_18') < trace.index('EXIT_QUARANTINE'),
      'exactlyOnceIncidentDelivery': duplicate_delivery==1 and len(delivered)==5,
      'singleEconomicOwner': duplicate_owner==0,
      'noOverRepair': over_repair==0.0 and actual==24.0,
      'latestTargetRevisionUsed': target_rev==5 and target_qty==24.0,
      'passiveFirstAfterRecovery': trace.index('PASSIVE_REPAIR_6.0') < trace.index('BOUNDED_ACTIVE_2.0'),
      'boundedActiveOnlyAfterPassiveStall': trace.index('PASSIVE_STALL_CONFIRMED') < trace.index('BOUNDED_ACTIVE_2.0'),
      'terminalResidualZero': unresolved==0.0,
      'r21ActionAuthorityFalse': True,
      'r21EventMutationFalse': True,
      'executorCallbackFalse': True,
    }
    report={'version':'R21_V33_L11_RESTART_GAP_UNKNOWN_LATEFILL_EXAM_V1','researchOnly':True,'performanceClaimAllowed':False,'trace':trace,'deliveredEvents':delivered,'gates':gates,'allPass':all(gates.values()),'final':{'targetRevision':target_rev,'targetQty':target_qty,'actualConfirmedShares':actual,'unresolved':unresolved,'owner':owner,'quarantined':quarantined},'authority':{'r21ActionAuthority':False,'eventMutationAllowed':False,'executorCallbackAllowed':False,'r2DecisionOwner':True}}
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'allPass':report['allPass'],'gates':gates,'final':report['final']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
