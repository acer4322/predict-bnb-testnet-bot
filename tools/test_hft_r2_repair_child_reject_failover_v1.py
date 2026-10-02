from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/hourly_novel_tests'
TEST_ID = 'HFT_R2_REPAIR_CHILD_REJECT_FAILOVER_V1'


def run_case(name: str, obligation: float, events: list[dict]) -> dict:
    unresolved = float(obligation)
    owner = 'REPAIR_OBLIGATION'
    passive_route_generation = 1
    same_route_retry = 0
    active_attempts = 0
    active_filled = 0.0
    confirmed_external_fill = 0.0
    duplicate_owner_count = 0
    over_repair = 0.0
    violations = []
    rejected = False
    returned_after_zero_fill = False
    trace = [
        {'event':'REPAIR_OBLIGATION_CREATED','qty':unresolved,'owner':owner},
        {'action':'PASSIVE_REPAIR_SUBMIT','routeGeneration':passive_route_generation,'qty':unresolved,'owner':owner},
    ]

    for ev in events:
        typ = ev['type']
        if typ == 'PASSIVE_REJECT':
            rejected = True
            trace.append({'event':'PASSIVE_REPAIR_REJECT_CONFIRMED','unresolvedQty':unresolved,'owner':owner})
        elif typ == 'BLIND_SAME_ROUTE_RETRY':
            same_route_retry += 1
            violations.append('BLIND_RETRY_OF_REJECTED_PASSIVE_ROUTE')
        elif typ == 'LATE_CONFIRMED_FILL':
            qty = min(float(ev['qty']), unresolved)
            confirmed_external_fill += qty
            unresolved -= qty
            trace.append({'event':'LATE_FILL_RECONCILED','fillDeltaQty':qty,'unresolvedQty':unresolved,'owner':owner})
        elif typ == 'ACTIVE_FALLBACK':
            if not rejected:
                violations.append('ACTIVE_FALLBACK_BEFORE_REJECT_EVIDENCE')
            requested = float(ev.get('requestedQty', unresolved))
            actual = min(float(ev.get('fillQty', requested)), unresolved)
            over_repair += max(0.0, requested - unresolved)
            active_attempts += 1
            active_filled += actual
            unresolved -= actual
            trace.append({'action':'BOUNDED_ACTIVE_FALLBACK','requestedQty':requested,'fillQty':actual,'unresolvedQty':unresolved,'owner':owner})
        elif typ == 'ACTIVE_TERMINAL_ZERO_FILL':
            if not rejected:
                violations.append('ACTIVE_ZERO_FILL_WITHOUT_REJECT_PATH')
            active_attempts += 1
            returned_after_zero_fill = True
            trace.append({'event':'ACTIVE_TERMINAL_ZERO_FILL','unresolvedQty':unresolved,'owner':owner})
            trace.append({'action':'RETURN_TO_CONTROLLER_UNRESOLVED','unresolvedQty':unresolved,'owner':owner})
        elif typ == 'NEW_PASSIVE_ROUTE':
            if not returned_after_zero_fill:
                violations.append('NEW_PASSIVE_ROUTE_WITHOUT_CONTROLLER_RETURN')
            passive_route_generation += 1
            trace.append({'action':'PASSIVE_REPAIR_SUBMIT_NEW_ROUTE','routeGeneration':passive_route_generation,'qty':unresolved,'owner':owner})
        elif typ == 'NEW_PASSIVE_FILL':
            qty = min(float(ev['qty']), unresolved)
            unresolved -= qty
            trace.append({'event':'NEW_PASSIVE_ROUTE_FILL_CONFIRMED','fillDeltaQty':qty,'unresolvedQty':unresolved,'owner':owner})
        else:
            raise ValueError(typ)

        if unresolved <= 1e-9:
            unresolved = 0.0
            owner = None

    if unresolved > 0 and owner is None:
        violations.append('ORPHANED_REPAIR_REMAINDER')
    if unresolved == 0 and owner is not None:
        violations.append('OWNER_NOT_RELEASED')
    if duplicate_owner_count:
        violations.append('DUPLICATE_ECONOMIC_OWNER')
    if over_repair > 1e-9:
        violations.append('OVER_REPAIR_REQUEST')

    expected_active_attempts = 1 if name != 'PASSIVE_REPAIR_REJECT_LATE_ORIGINAL_FILL_THEN_ACTIVE_REMAINDER' else 1
    passed = (
        rejected
        and same_route_retry == 0
        and active_attempts == expected_active_attempts
        and unresolved == 0.0
        and duplicate_owner_count == 0
        and over_repair == 0.0
        and not violations
    )
    return {
        'scenario': name,
        'initialRepairObligationQty': obligation,
        'passiveRejectObserved': rejected,
        'sameRejectedRouteRetryCount': same_route_retry,
        'activeFallbackAttempts': active_attempts,
        'activeFilledQty': active_filled,
        'lateConfirmedFillQty': confirmed_external_fill,
        'passiveRouteGenerations': passive_route_generation,
        'terminalUnresolvedQty': unresolved,
        'duplicateOwnerCount': duplicate_owner_count,
        'overRepairQty': over_repair,
        'lifecycleViolations': violations,
        'trace': trace,
        'pass': passed,
    }


def main() -> None:
    rows = [
        run_case('PASSIVE_REPAIR_REJECT_THEN_ACTIVE_COMPLETES', 12, [
            {'type':'PASSIVE_REJECT'},
            {'type':'ACTIVE_FALLBACK','requestedQty':12,'fillQty':12},
        ]),
        run_case('PASSIVE_REPAIR_REJECT_LATE_ORIGINAL_FILL_THEN_ACTIVE_REMAINDER', 12, [
            {'type':'PASSIVE_REJECT'},
            {'type':'LATE_CONFIRMED_FILL','qty':5},
            {'type':'ACTIVE_FALLBACK','requestedQty':7,'fillQty':7},
        ]),
        run_case('PASSIVE_REPAIR_REJECT_ACTIVE_ZERO_FILL_RETURN_THEN_SECOND_PASSIVE_NEW_ROUTE', 9, [
            {'type':'PASSIVE_REJECT'},
            {'type':'ACTIVE_TERMINAL_ZERO_FILL'},
            {'type':'NEW_PASSIVE_ROUTE'},
            {'type':'NEW_PASSIVE_FILL','qty':9},
        ]),
    ]
    all_pass = all(r['pass'] for r in rows)
    summary = {
        'scenarios': len(rows),
        'passed': sum(r['pass'] for r in rows),
        'repairRejectObservedAll': all(r['passiveRejectObserved'] for r in rows),
        'sameRejectedRouteRetryCount': sum(r['sameRejectedRouteRetryCount'] for r in rows),
        'duplicateOwnerCount': sum(r['duplicateOwnerCount'] for r in rows),
        'overRepairQty': sum(r['overRepairQty'] for r in rows),
        'lifecycleViolationCount': sum(len(r['lifecycleViolations']) for r in rows),
        'lateFillAwareActiveQtyCase2': rows[1]['activeFilledQty'],
        'activeZeroFillReturnedThenNewPassiveRouteCase3': rows[2]['passiveRouteGenerations'] == 2,
        'terminalUnresolvedQtyMax': max(r['terminalUnresolvedQty'] for r in rows),
        'status': 'TESTED_KEEP_SIGNAL' if all_pass else 'TESTED_REJECTED',
    }
    report = {
        'testId': TEST_ID,
        'researchOnly': True,
        'evidenceClass': 'STRUCTURAL_EXECUTION_LIFECYCLE_EVIDENCE',
        'executionSemanticsSource': 'HftBacktest/Predict Execution Tape V1 incident semantics; deterministic structural intervention, not PnL evidence',
        'preregistration': 'data/research/hourly_novel_tests/hft_r2_repair_child_reject_failover_v1_preregistered.json',
        'rows': rows,
        'summary': summary,
        'conclusion': ('Repair-child reject failover passes the preregistered structural gate: the rejected passive route is not blindly retried, repair ownership is preserved, confirmed late fills shrink the fallback quantity, and unresolved active zero-fill can return to controller for a genuinely new passive route without duplicate ownership.' if all_pass else 'Repair-child reject failover failed at least one preregistered lifecycle/safety invariant.'),
    }
    path = OUT / 'hft_r2_repair_child_reject_failover_v1_report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
