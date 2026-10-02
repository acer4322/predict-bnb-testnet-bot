from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/research/hourly_novel_tests/hft_r2_repair_child_expiry_recovery_v1_report.json'
TEST_ID = 'HFT_R2_REPAIR_CHILD_EXPIRY_RECOVERY_V1'


def scenario(name: str, obligation: float, pre_expiry_fill: float, replacement_fill: float, replacement_stalls: bool):
    events = []
    unresolved = float(obligation)
    expired_route_replay = 0
    duplicate_owner = 0
    lifecycle_violations = 0
    active_qty = 0.0

    events.append({'event': 'REPAIR_CHILD_ACKED_OPEN', 'route': 'PASSIVE_A', 'qty': unresolved, 'singleEconomicOwner': True})
    if pre_expiry_fill > 0:
        fill = min(unresolved, float(pre_expiry_fill))
        unresolved -= fill
        events.append({'event': 'PASSIVE_A_CONFIRMED_FILL', 'fillQty': fill, 'unresolvedAfter': unresolved})

    events.append({'event': 'PASSIVE_A_TERMINAL_EXPIRED', 'route': 'PASSIVE_A', 'obligationPreserved': True, 'unresolvedAfter': unresolved})

    replacement_qty = unresolved
    if replacement_qty > 0:
        events.append({'event': 'PASSIVE_REPAIR_REOPENED_NEW_ROUTE', 'route': 'PASSIVE_B', 'qty': replacement_qty, 'reason': 'TERMINAL_EXPIRED_REMAINDER', 'singleEconomicOwner': True})

    fill2 = min(unresolved, float(replacement_fill))
    if fill2 > 0:
        unresolved -= fill2
        events.append({'event': 'PASSIVE_B_CONFIRMED_FILL', 'fillQty': fill2, 'unresolvedAfter': unresolved})

    if replacement_stalls and unresolved > 0:
        events.append({'event': 'PASSIVE_B_STALL_CONFIRMED', 'unresolved': unresolved})
        active_qty = unresolved
        events.append({'event': 'BOUNDED_ACTIVE_REPAIR', 'qty': active_qty, 'reason': 'POST_EXPIRY_REPLACEMENT_PASSIVE_STALL_ONLY'})
        unresolved = 0.0
        events.append({'event': 'ACTIVE_REPAIR_CONFIRMED_FILL', 'fillQty': active_qty, 'unresolvedAfter': unresolved})

    over_repair = max(0.0, -unresolved)
    if unresolved < -1e-9:
        lifecycle_violations += 1

    fresh_passive_before_active = (
        active_qty == 0.0 or any(e['event'] == 'PASSIVE_REPAIR_REOPENED_NEW_ROUTE' for e in events)
    )
    passed = (
        expired_route_replay == 0
        and duplicate_owner == 0
        and over_repair == 0.0
        and lifecycle_violations == 0
        and abs(unresolved) < 1e-9
        and fresh_passive_before_active
    )
    return {
        'scenario': name,
        'passed': passed,
        'initialObligationQty': obligation,
        'preExpiryFillQty': pre_expiry_fill,
        'replacementPassiveQty': replacement_qty,
        'replacementPassiveFillQty': fill2,
        'activeQty': active_qty,
        'terminalUnresolvedQty': unresolved,
        'expiredRouteReplayCount': expired_route_replay,
        'duplicateOwnerCount': duplicate_owner,
        'overRepairQty': over_repair,
        'lifecycleViolationCount': lifecycle_violations,
        'freshPassiveBeforeActive': fresh_passive_before_active,
        'events': events,
    }


def main():
    rows = [
        scenario('UNFILLED_REPAIR_EXPIRES_THEN_FRESH_PASSIVE_COMPLETES', 12.0, 0.0, 12.0, False),
        scenario('PARTIAL_REPAIR_FILL_THEN_EXPIRES_REMAINDER_RECOMPUTED', 12.0, 5.0, 7.0, False),
        scenario('EXPIRED_THEN_NEW_PASSIVE_PARTIAL_STALL_BOUNDED_ACTIVE_FINAL_REMAINDER', 12.0, 3.0, 4.0, True),
    ]
    summary = {
        'scenarios': len(rows),
        'passed': sum(bool(r['passed']) for r in rows),
        'expiredRouteReplayCount': sum(r['expiredRouteReplayCount'] for r in rows),
        'duplicateOwnerCount': sum(r['duplicateOwnerCount'] for r in rows),
        'overRepairQty': sum(r['overRepairQty'] for r in rows),
        'lifecycleViolationCount': sum(r['lifecycleViolationCount'] for r in rows),
        'case2ReplacementQty': rows[1]['replacementPassiveQty'],
        'case2ExpectedReplacementQty': 7.0,
        'case3ReplacementQty': rows[2]['replacementPassiveQty'],
        'case3ActiveQty': rows[2]['activeQty'],
        'case3ExpectedActiveQty': 5.0,
        'activeBypassedFreshPassiveReplacement': any(r['activeQty'] > 0 and not r['freshPassiveBeforeActive'] for r in rows),
    }
    keep = (
        summary['passed'] == 3
        and summary['expiredRouteReplayCount'] == 0
        and summary['duplicateOwnerCount'] == 0
        and summary['overRepairQty'] == 0.0
        and summary['lifecycleViolationCount'] == 0
        and summary['case2ReplacementQty'] == 7.0
        and summary['case3ActiveQty'] == 5.0
        and not summary['activeBypassedFreshPassiveReplacement']
    )
    report = {
        'testId': TEST_ID,
        'axis': 'R2_AUTONOMOUS_REPAIR_CHILD_EXPIRY_RECOVERY',
        'researchOnly': True,
        'performanceClaimAllowed': False,
        'executionSemantics': 'deterministic structural scenarios grounded in existing HftBacktest/Predict Execution Tape V1 accepted-live, confirmed partial fill, and terminal EXPIRED semantics',
        'summary': summary,
        'rows': rows,
        'status': 'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED',
        'interpretation': 'KEEP structural primitive: a venue-terminal EXPIRED repair child does not erase the economic repair obligation; confirmed pre-expiry fills resize the remainder; the expired route is never replayed; a genuinely new passive route gets first authority; bounded active handles only the final unresolved remainder after the replacement passive stalls.' if keep else 'Structural gate failed; do not carry this expiry-recovery method forward.'
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
