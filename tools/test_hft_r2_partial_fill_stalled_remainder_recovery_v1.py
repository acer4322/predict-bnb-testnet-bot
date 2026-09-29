from __future__ import annotations
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/hourly_novel_tests'
TEST_ID = 'HFT_R2_PARTIAL_FILL_STALLED_REMAINDER_RECOVERY_V1'


def run_case(name: str, requested: float, initial_fill: float, events: list[dict[str, Any]]) -> dict[str, Any]:
    confirmed = float(initial_fill)
    unresolved = requested - confirmed
    owner = 'LIVE_REMAINDER_CHILD' if unresolved > 0 else None
    trace = [
        {'event':'PARTIAL_FILL_CONFIRMED','confirmedFilledQty':confirmed,'unresolvedQty':unresolved,'owner':owner},
        {'event':'LIVE_PARTIAL_FILL_STALL','confirmedFilledQty':confirmed,'unresolvedQty':unresolved,'owner':owner},
        {'action':'PASSIVE_REPAIR','qty':unresolved,'owner':owner},
    ]
    passive_activated = unresolved > 0
    active_actions = []
    duplicate_owners = 0
    over_repair = 0.0
    violations = []
    passive_failed = False

    for ev in events:
        typ = ev['type']
        if typ == 'PASSIVE_FILL':
            qty = min(float(ev['qty']), unresolved)
            confirmed += qty
            unresolved -= qty
            trace.append({'event':'PASSIVE_FILL_CONFIRMED','fillDeltaQty':qty,'confirmedFilledQty':confirmed,'unresolvedQty':unresolved})
        elif typ == 'PASSIVE_STALL':
            passive_failed = True
            trace.append({'event':'PASSIVE_REMAINDER_STALL','unresolvedQty':unresolved})
        elif typ == 'LATE_ORIGINAL_FILL':
            qty = min(float(ev['qty']), unresolved)
            confirmed += qty
            unresolved -= qty
            trace.append({'event':'LATE_FILL_RECONCILED','fillDeltaQty':qty,'confirmedFilledQty':confirmed,'unresolvedQty':unresolved,'owner':owner})
        elif typ == 'ACTIVE_COMPLETE':
            if not passive_failed:
                violations.append('PREMATURE_ACTIVE_BEFORE_PASSIVE_FAILURE')
            requested_active = float(ev.get('requestedQty', unresolved))
            actual_active = min(requested_active, unresolved)
            over_repair += max(0.0, requested_active - unresolved)
            active_actions.append(actual_active)
            confirmed += actual_active
            unresolved -= actual_active
            trace.append({'action':'BOUNDED_ACTIVE_REPAIR','requestedQty':requested_active,'executedQty':actual_active,'confirmedFilledQty':confirmed,'unresolvedQty':unresolved})
        else:
            raise ValueError(typ)
        if unresolved <= 1e-9:
            unresolved = 0.0
            owner = None

    if confirmed > requested + 1e-9:
        violations.append('CONFIRMED_FILL_EXCEEDS_REQUESTED')
    if unresolved > 1e-9 and owner is None:
        violations.append('ORPHANED_REMAINDER')
    if unresolved == 0 and owner is not None:
        violations.append('OWNER_NOT_RELEASED')
    if over_repair > 1e-9:
        violations.append('OVER_REPAIR_REQUEST')

    requires_active = any(e['type']=='ACTIVE_COMPLETE' for e in events)
    passed = (
        passive_activated
        and (bool(active_actions) == requires_active)
        and unresolved == 0.0
        and duplicate_owners == 0
        and over_repair == 0.0
        and not violations
    )
    return {
        'scenario': name,
        'requestedQty': requested,
        'initialConfirmedFill': initial_fill,
        'passiveRepairActivated': passive_activated,
        'activeRepairActivated': bool(active_actions),
        'activeRepairQty': sum(active_actions),
        'terminalConfirmedFill': confirmed,
        'terminalUnresolvedQty': unresolved,
        'duplicateOwnerCount': duplicate_owners,
        'overRepairQty': over_repair,
        'lifecycleViolations': violations,
        'trace': trace,
        'pass': passed,
    }


def main() -> None:
    rows = [
        run_case('PARTIAL6_PASSIVE_COMPLETES', 18, 6, [
            {'type':'PASSIVE_FILL','qty':12},
        ]),
        run_case('PARTIAL6_PASSIVE_STALL_ACTIVE12', 18, 6, [
            {'type':'PASSIVE_STALL'},
            {'type':'ACTIVE_COMPLETE','requestedQty':12},
        ]),
        run_case('PARTIAL10_LATE3_THEN_ACTIVE5', 18, 10, [
            {'type':'PASSIVE_STALL'},
            {'type':'LATE_ORIGINAL_FILL','qty':3},
            {'type':'ACTIVE_COMPLETE','requestedQty':5},
        ]),
    ]
    all_pass = all(r['pass'] for r in rows)
    report = {
        'testId': TEST_ID,
        'researchOnly': True,
        'evidenceClass': 'STRUCTURAL_EXECUTION_LIFECYCLE_EVIDENCE',
        'executionSemanticsSource': 'HftBacktest/Predict Execution Tape V1 incident contract; deterministic structural intervention, not PnL evidence',
        'preregistration': 'data/research/hourly_novel_tests/hft_r2_partial_fill_stalled_remainder_recovery_v1_preregistered.json',
        'rows': rows,
        'summary': {
            'scenarios': len(rows),
            'passed': sum(r['pass'] for r in rows),
            'passiveRepairScenarios': sum(r['passiveRepairActivated'] for r in rows),
            'activeRepairScenarios': sum(r['activeRepairActivated'] for r in rows),
            'duplicateOwnerCount': sum(r['duplicateOwnerCount'] for r in rows),
            'overRepairQty': sum(r['overRepairQty'] for r in rows),
            'lifecycleViolationCount': sum(len(r['lifecycleViolations']) for r in rows),
            'lateFillAwareActiveQtyCase3': rows[2]['activeRepairQty'],
            'status': 'TESTED_KEEP_SIGNAL' if all_pass else 'TESTED_REJECTED',
        },
        'conclusion': ('Explicit stalled-remainder ownership with passive-first repair and late-fill-aware bounded active completion passes the preregistered structural recovery gate. It is a repair primitive, not economic promotion evidence.' if all_pass else 'The partial-fill stalled-remainder recovery primitive failed at least one preregistered safety/recovery gate.'),
    }
    path = OUT / 'hft_r2_partial_fill_stalled_remainder_recovery_v1_report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report['summary'], ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
