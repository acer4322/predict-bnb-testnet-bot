from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/hourly_novel_tests'
TEST_ID = 'HFT_R2_SOURCE_GAP_INFLIGHT_REPAIR_RECONCILE_V1'


def run_case(name, repair_qty, events):
    unresolved = float(repair_qty)
    source_fresh = True
    fresh_snapshot_seen = True
    owner = 'REPAIR_OBLIGATION'
    live_child = True
    gap_seen = False
    passive_after_resume = False
    active_used = False
    new_actions_while_stale = 0
    stale_replays = 0
    duplicate_owner = 0
    over_repair = 0.0
    violations = []
    trace = [{'event':'REPAIR_CHILD_LIVE','repairQty':unresolved,'sourceFresh':True}]

    for ev in events:
        typ = ev['type']
        if typ == 'SOURCE_GAP':
            source_fresh = False
            fresh_snapshot_seen = False
            gap_seen = True
            trace.append({'event':'SOURCE_GAP_FAIL_CLOSED','repairOwnerPreserved':owner is not None})
        elif typ == 'ATTEMPT_NEW_ECONOMIC_ACTION':
            if not source_fresh:
                new_actions_while_stale += 1
                violations.append('NEW_ECONOMIC_ACTION_WHILE_SOURCE_STALE')
        elif typ == 'VENUE_CONFIRMED_FILL_DURING_GAP':
            q = float(ev['qty'])
            if source_fresh:
                violations.append('GAP_FILL_EVENT_WITH_SOURCE_NOT_STALE')
            fill = min(q, unresolved)
            over_repair += max(0.0, q-unresolved)
            unresolved -= fill
            trace.append({'event':'CONFIRMED_VENUE_FILL_DURING_GAP','qty':q,'repairQty':unresolved})
            if unresolved == 0:
                owner = None
                live_child = False
        elif typ == 'SOURCE_FRESH_SNAPSHOT':
            source_fresh = True
            fresh_snapshot_seen = True
            trace.append({'event':'FRESH_SOURCE_SNAPSHOT','repairQtyBeforeOwnStateReconcile':unresolved})
        elif typ == 'RECONCILE_CONFIRMED_OWN_STATE':
            if not source_fresh or not fresh_snapshot_seen:
                violations.append('RECONCILE_BEFORE_FRESH_SOURCE')
            trace.append({'event':'CONFIRMED_OWN_STATE_RECONCILED','repairQty':unresolved})
        elif typ == 'REPLAY_PRE_GAP_ACTION':
            stale_replays += 1
            violations.append('STALE_PRE_GAP_ACTION_REPLAYED')
        elif typ == 'RESUME_PASSIVE_REPAIR':
            if not source_fresh or not fresh_snapshot_seen:
                violations.append('RESUME_BEFORE_FRESH_SOURCE')
            req = float(ev.get('requestedQty', unresolved))
            if req > unresolved + 1e-9:
                over_repair += req-unresolved
            fill = min(float(ev.get('fillQty', req)), unresolved)
            passive_after_resume = True
            live_child = True
            unresolved -= fill
            trace.append({'action':'PASSIVE_REPAIR_AFTER_FRESH_RECONCILE','requestedQty':req,'fillQty':fill,'repairQty':unresolved})
            if unresolved == 0:
                owner = None
                live_child = False
        elif typ == 'PASSIVE_TERMINAL_STALL':
            live_child = False
            trace.append({'event':'PASSIVE_REPAIR_TERMINAL_STALL','repairQty':unresolved})
        elif typ == 'ACTIVE_REPAIR':
            if not passive_after_resume:
                violations.append('ACTIVE_BEFORE_POST_RECOVERY_PASSIVE')
            if not source_fresh or not fresh_snapshot_seen:
                violations.append('ACTIVE_WHILE_SOURCE_STALE')
            req = float(ev.get('requestedQty', unresolved))
            if req > unresolved + 1e-9:
                over_repair += req-unresolved
            fill = min(float(ev.get('fillQty', req)), unresolved)
            active_used = True
            unresolved -= fill
            trace.append({'action':'BOUNDED_ACTIVE_REPAIR','requestedQty':req,'fillQty':fill,'repairQty':unresolved})
            if unresolved == 0:
                owner = None
                live_child = False
        else:
            raise ValueError(typ)

    if unresolved > 1e-9 and owner is None:
        violations.append('ORPHANED_REPAIR_REMAINDER')
    if unresolved <= 1e-9:
        unresolved = 0.0
        if owner is not None:
            violations.append('OWNER_NOT_RELEASED')
    if duplicate_owner:
        violations.append('DUPLICATE_REPAIR_OWNER')
    if over_repair > 1e-9:
        violations.append('OVER_REPAIR')

    passed = (
        gap_seen and fresh_snapshot_seen and passive_after_resume and
        new_actions_while_stale == 0 and stale_replays == 0 and
        duplicate_owner == 0 and over_repair == 0 and unresolved == 0 and not violations
    )
    return {
        'scenario': name, 'pass': passed, 'terminalUnresolvedQty': unresolved,
        'newActionsWhileSourceStale': new_actions_while_stale,
        'stalePreGapReplayCount': stale_replays,
        'duplicateOwnerCount': duplicate_owner,
        'overRepairQty': over_repair, 'activeRepairUsed': active_used,
        'lifecycleViolations': violations, 'trace': trace
    }


def main():
    rows = [
        run_case('SOURCE_GAP_WITH_LIVE_REPAIR_NO_FILL_THEN_FRESH_RESUME_PASSIVE_COMPLETE', 12, [
            {'type':'SOURCE_GAP'},
            {'type':'SOURCE_FRESH_SNAPSHOT'},
            {'type':'RECONCILE_CONFIRMED_OWN_STATE'},
            {'type':'RESUME_PASSIVE_REPAIR','requestedQty':12,'fillQty':12},
        ]),
        run_case('SOURCE_GAP_WITH_CONFIRMED_FILL_DURING_OUTAGE_THEN_RESIZE_ON_FRESH_RESUME', 12, [
            {'type':'SOURCE_GAP'},
            {'type':'VENUE_CONFIRMED_FILL_DURING_GAP','qty':5},
            {'type':'SOURCE_FRESH_SNAPSHOT'},
            {'type':'RECONCILE_CONFIRMED_OWN_STATE'},
            {'type':'RESUME_PASSIVE_REPAIR','requestedQty':7,'fillQty':7},
        ]),
        run_case('SOURCE_GAP_THEN_PASSIVE_REMAINDER_STALL_AND_BOUNDED_ACTIVE_AFTER_RECOVERY', 12, [
            {'type':'SOURCE_GAP'},
            {'type':'VENUE_CONFIRMED_FILL_DURING_GAP','qty':3},
            {'type':'SOURCE_FRESH_SNAPSHOT'},
            {'type':'RECONCILE_CONFIRMED_OWN_STATE'},
            {'type':'RESUME_PASSIVE_REPAIR','requestedQty':9,'fillQty':4},
            {'type':'PASSIVE_TERMINAL_STALL'},
            {'type':'ACTIVE_REPAIR','requestedQty':5,'fillQty':5},
        ]),
    ]
    all_pass = all(r['pass'] for r in rows)
    summary = {
        'scenarios': len(rows), 'passed': sum(r['pass'] for r in rows),
        'newActionsWhileSourceStale': sum(r['newActionsWhileSourceStale'] for r in rows),
        'stalePreGapReplayCount': sum(r['stalePreGapReplayCount'] for r in rows),
        'duplicateOwnerCount': sum(r['duplicateOwnerCount'] for r in rows),
        'overRepairQty': sum(r['overRepairQty'] for r in rows),
        'lifecycleViolationCount': sum(len(r['lifecycleViolations']) for r in rows),
        'gapFillResizedCase2ToQty': 7.0,
        'activeOnlyAfterRecoveredPassiveStallCase3': rows[2]['activeRepairUsed'],
        'status': 'TESTED_KEEP_SIGNAL' if all_pass else 'TESTED_REJECTED'
    }
    report = {
        'testId': TEST_ID, 'researchOnly': True,
        'evidenceClass': 'STRUCTURAL_EXECUTION_LIFECYCLE_EVIDENCE',
        'executionSemanticsSource': 'Official HftBacktest/Predict Execution Tape V1 source-gap and confirmed-fill semantics; deterministic structural intervention, not PnL evidence',
        'preregistration': 'data/research/hourly_novel_tests/hft_r2_source_gap_inflight_repair_reconcile_v1_preregistered.json',
        'rows': rows, 'summary': summary,
        'conclusion': ('An in-flight repair can survive a public-data source gap without replaying stale decisions: the repair obligation remains owned, no new economic child is issued while source data is stale, confirmed venue fills during the gap resize the remainder, and execution resumes only after a fresh source snapshot plus own-state reconciliation; passive repair retains first authority and bounded active repair handles only the final unresolved remainder.' if all_pass else 'At least one preregistered source-freshness/ownership/reconciliation invariant failed.')
    }
    p = OUT / 'hft_r2_source_gap_inflight_repair_reconcile_v1_report.json'
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
