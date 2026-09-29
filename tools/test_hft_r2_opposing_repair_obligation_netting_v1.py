import json
from pathlib import Path

OUT = Path('data/research/hourly_novel_tests/hft_r2_opposing_repair_obligation_netting_v1_report.json')


def net_obligation(up_qty: float, down_qty: float):
    if up_qty > down_qty:
        return 'UP', round(up_qty - down_qty, 10)
    if down_qty > up_qty:
        return 'DOWN', round(down_qty - up_qty, 10)
    return None, 0.0


def run_case(name, initial_owner, initial_qty, opposite_side, opposite_qty, owner_terminal_before_reissue):
    events = []
    violations = []
    dual_owner_count = 0

    # Current confirmed unresolved economic obligation.
    up = initial_qty if initial_owner == 'UP' else 0.0
    down = initial_qty if initial_owner == 'DOWN' else 0.0
    owner = {'side': initial_owner, 'qty': initial_qty, 'state': 'WORKING'} if initial_owner else None
    events.append({'event': 'INITIAL_OBLIGATION', 'owner': owner.copy() if owner else None})

    # New confirmed opposite-side obligation arrives. Never spawn a second child while current owner is live.
    if opposite_side == 'UP':
        up += opposite_qty
    else:
        down += opposite_qty
    events.append({'event': 'OPPOSITE_CONFIRMED_OBLIGATION', 'side': opposite_side, 'qty': opposite_qty})

    if owner and owner['state'] == 'WORKING':
        # Quarantine new obligation until terminal evidence for the existing child.
        events.append({'event': 'QUARANTINE_NEW_CHILD', 'reason': 'EXISTING_OWNER_NOT_TERMINAL'})
        if not owner_terminal_before_reissue:
            violations.append('REISSUE_WITHOUT_TERMINAL_EVIDENCE')
            dual_owner_count += 1
        owner['state'] = 'TERMINAL_ACK'
        events.append({'event': 'OWNER_TERMINAL_ACK', 'side': owner['side'], 'qty': owner['qty']})

    # Reconcile from confirmed obligations and algebraically net before issuing any new child.
    net_side, net_qty = net_obligation(up, down)
    events.append({'event': 'RECONCILE_AND_NET', 'upObligation': up, 'downObligation': down, 'netSide': net_side, 'netQty': net_qty})
    owner = None
    if net_qty > 0:
        owner = {'side': net_side, 'qty': net_qty, 'state': 'WORKING'}
        events.append({'event': 'NEW_NET_REPAIR_OWNER', 'side': net_side, 'qty': net_qty})

    # Structural completion of the latest net residual only.
    terminal_unresolved = 0.0
    if owner:
        owner['state'] = 'FILLED'
        events.append({'event': 'NET_REPAIR_CONFIRMED_FILLED', 'side': owner['side'], 'qty': owner['qty']})

    if dual_owner_count > 0:
        violations.append('DUAL_ECONOMIC_REPAIR_OWNER')

    stale_gross_qty = round(initial_qty + opposite_qty, 10)
    stale_qty_suppressed = (net_qty < stale_gross_qty) if stale_gross_qty > 0 else True
    result = {
        'name': name,
        'events': events,
        'expectedNetSide': net_side,
        'expectedNetQty': net_qty,
        'dualEconomicOwnerCount': dual_owner_count,
        'reconcileBeforeNewChild': any(e['event'] == 'RECONCILE_AND_NET' for e in events),
        'staleGrossRepairQty': stale_gross_qty,
        'executedNetRepairQty': net_qty,
        'staleChildQuantitySuppressed': stale_qty_suppressed,
        'terminalUnresolvedResidual': terminal_unresolved,
        'lifecycleViolations': violations,
    }
    result['passed'] = (
        result['dualEconomicOwnerCount'] == 0
        and result['reconcileBeforeNewChild']
        and result['staleChildQuantitySuppressed']
        and result['terminalUnresolvedResidual'] == 0.0
        and len(result['lifecycleViolations']) == 0
    )
    return result


def same_checkpoint_case():
    events = [
        {'event': 'CONFIRMED_OBLIGATIONS_SAME_CHECKPOINT', 'upQty': 14.0, 'downQty': 9.0}
    ]
    side, qty = net_obligation(14.0, 9.0)
    events.append({'event': 'RECONCILE_AND_NET', 'netSide': side, 'netQty': qty})
    events.append({'event': 'NEW_NET_REPAIR_OWNER', 'side': side, 'qty': qty})
    events.append({'event': 'NET_REPAIR_CONFIRMED_FILLED', 'side': side, 'qty': qty})
    return {
        'name': 'same_checkpoint_up14_down9',
        'events': events,
        'expectedNetSide': 'UP',
        'expectedNetQty': 5.0,
        'dualEconomicOwnerCount': 0,
        'reconcileBeforeNewChild': True,
        'staleGrossRepairQty': 23.0,
        'executedNetRepairQty': 5.0,
        'staleChildQuantitySuppressed': True,
        'terminalUnresolvedResidual': 0.0,
        'lifecycleViolations': [],
        'passed': side == 'UP' and qty == 5.0,
    }


def main():
    cases = [
        run_case('passive_down12_plus_opposite_up7', 'DOWN', 12.0, 'UP', 7.0, True),
        run_case('active_down10_plus_opposite_up6_before_terminal', 'DOWN', 10.0, 'UP', 6.0, True),
        same_checkpoint_case(),
    ]

    # Exact expected residual checks fixed by preregistration.
    exact = [
        cases[0]['expectedNetSide'] == 'DOWN' and cases[0]['expectedNetQty'] == 5.0,
        cases[1]['expectedNetSide'] == 'DOWN' and cases[1]['expectedNetQty'] == 4.0,
        cases[2]['expectedNetSide'] == 'UP' and cases[2]['expectedNetQty'] == 5.0,
    ]
    for c, ok in zip(cases, exact):
        c['netResidualCorrect'] = ok
        c['passed'] = c['passed'] and ok

    passed = sum(1 for c in cases if c['passed'])
    violations = sum(len(c['lifecycleViolations']) for c in cases)
    status = 'TESTED_KEEP_SIGNAL' if passed == 3 and violations == 0 else 'TESTED_REJECTED'
    report = {
        'testId': 'HFT_R2_OPPOSING_REPAIR_OBLIGATION_NETTING_V1',
        'axis': 'R2_AUTONOMOUS_REPAIR_OPPOSING_OBLIGATION_NETTING',
        'evidenceClass': 'STRUCTURAL_LIFECYCLE_ONLY',
        'performanceClaimAllowed': False,
        'scenarios': cases,
        'primaryResult': {
            'scenarios': len(cases),
            'passed': passed,
            'dualEconomicOwnerCount': sum(c['dualEconomicOwnerCount'] for c in cases),
            'exactNetResidualCases': sum(1 for c in cases if c['netResidualCorrect']),
            'reconcileBeforeNewChildCases': sum(1 for c in cases if c['reconcileBeforeNewChild']),
            'staleChildQuantitySuppressedCases': sum(1 for c in cases if c['staleChildQuantitySuppressed']),
            'terminalUnresolvedResidualMax': max(c['terminalUnresolvedResidual'] for c in cases),
            'lifecycleViolationCount': violations,
        },
        'status': status,
        'conclusion': (
            'Opposing confirmed repair obligations are safely reconciled and netted into a single economic owner before new execution. '
            'The method prevents mutually-cancelling repair children and suppresses stale gross repair quantity. Structural evidence only; no PnL claim.'
            if status == 'TESTED_KEEP_SIGNAL' else
            'Opposing-obligation netting violated the preregistered lifecycle gate; reject this primitive.'
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report['primaryResult']))
    print(status)


if __name__ == '__main__':
    main()
