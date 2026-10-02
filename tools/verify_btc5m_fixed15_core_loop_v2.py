"""Reuse V17 receipt/budget checks with explicit package-local ticket15 bindings."""
import argparse
import ast
import inspect
import json
import math
import types

import verify_btc5m_repair_profit_budget_v1 as old
from btc5m_fixed15_research_condition_v1 import make_validator
from btc5m_fixed15_execution_condition_v2 import CONTRACT
from pair_core_asset_route_sizing_v2 import validate_size
from aggregate_btc5m_exposure_intent_ablation_v1 import get, ROOT, R, RET, STREAMS
from audit_btc5m_target_core_loop_topology_v1 import read, sha
from prepare_btc5m_oracle_repair_panel_v1 import once

PACKAGE = ROOT / '.lan_worker_v1/fixed15_core_loop_2026085_20260913_v2'
STEM = 'BTC5M_FIXED15_CORE_LOOP_V2_20260913'


def auditor():
    # Separate function globals; the historical auditor and shared files remain
    # unchanged. Only the explicit size validator,15 goal and package pins differ.
    f = old.audit_receipts
    receipts = types.FunctionType(f.__code__, dict(f.__globals__, validate_size=make_validator(validate_size)),
                                  f.__name__, f.__defaults__, f.__closure__)
    source = inspect.getsource(old.audit)
    source = once(source, "+initial['pending_qty']['DOWN']+30.", "+initial['pending_qty']['DOWN']+15.")
    source = once(source, "last_minimum=max(18.,1./last_budget['price'])", "last_minimum=max(15.,1./last_budget['price'])")
    ns = dict(old.__dict__, PACKAGE=PACKAGE, audit_receipts=receipts)
    exec(compile(ast.parse(source), 'explicit_fixed15_audit_bindings', 'exec'), ns)
    return ns['audit']


def inspect_arm(tag):
    result, trace = get(RET / f'fixed15-core-loop-2026085-{tag}-20260913-v2')
    assert result['status'] == 'COMPLETE' or result['status'] == 'PASS', result['status']
    assert result['clock_smoke']['passive_ticket'] == 15.
    assert result['clock_smoke']['sizing_condition'] == CONTRACT
    condition = read(R / 'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json')
    assert result['sizing_installation'] == condition['private_file_changes']
    new = [o for p in trace['plans'] for o in p['operations'] if o['kind'] == 'NEW']
    assert len(new) == result['submits'] and all(o['route'] == 'PASSIVE' and o['qty'] == 15. for o in new)
    assert all(o['qty'] * o['price'] >= 1 - 1e-8 for o in new)
    parent, _ = get(RET / 'repair-profit-budget-2026085-control-20260913-v1')
    assert len(parent['theta']) == len(result['theta'])
    assert all(a == b for i, (a, b) in enumerate(zip(parent['theta'], result['theta'])) if i != 7)
    assert abs(result['theta'][7] - math.log(15.)) < 1e-12
    report = auditor()(tag, result, trace)
    report.update(all_new_exactly15=True, theta_except_ticket_equal_v17=True,
                  new_orders_by_side={side: sum(o['side'] == side for o in new) for side in ('UP', 'DOWN')},
                  result_sha256=sha(RET / f'fixed15-core-loop-2026085-{tag}-20260913-v2/result.json'))
    return result, trace, report


def brief(report):
    peak = report['post_exposure']['peak']
    after = peak['windows']['30']
    return dict(terminal=report['terminal'], cost_by_side=report['paid_by_side'], retention=report['up_profit_retention'],
                area=report['trajectory']['negative_floor_area_currency_seconds'],
                worst=report['trajectory']['minimum_floor'], peak_seconds=peak['seconds'],
                peak_next30=dict(weak_payoff_change=after['weak_payoff_change'], weak_payoff_after=after['weak_payoff_after'],
                                 net_retained_fraction=after['net_retained_fraction']),
                submits=report['submits'], new_orders_by_side=report['new_orders_by_side'],
                finite_work_status=report['work_status_counts'], unfinished=report['unfinished_work_ids'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--control-only', action='store_true')
    args = parser.parse_args()
    manifest = read(PACKAGE / 'manifest.json')
    assert all(sha(PACKAGE / n) == h for n, h in manifest['files'].items())
    control, ct, ca = inspect_arm('control')
    if args.control_only:
        (R / (STEM + '_CONTROL.json')).write_text(json.dumps(dict(status='PASS', arm=ca), indent=2) + '\n')
        print(json.dumps(brief(ca), indent=2))
        return
    half, ht, ha = inspect_arm('half')
    assert control['theta'] == half['theta']
    differing = [a['t'] for a, b in zip(ct['plans'], ht['plans']) if a != b]
    first = differing[0] if differing else None
    assert first is not None, 'No physical budget contrast; record separately before interpretation'
    prefix = {k: [x for x in ct[k] if x['t'] < first] == [x for x in ht[k] if x['t'] < first]
              for k in (*STREAMS, 'intent', 'money_rows')}
    assert all(prefix.values()), prefix
    out = dict(status='COMPLETE', verification='PASS', arms=dict(control=ca, half=ha),
               prefix=prefix, first_plan_difference_t=first, manifest_sha256=sha(PACKAGE / 'manifest.json'),
               successful_native_jobs=2, earlier_v1_failed_before_first_send=1, local_native_jobs=0, model_fits=0,
               scope='Same15 Passive condition, existing budget on/off; no size or parameter sweep; Active held off in both arms.',
               reused_verifier_sha256=sha(ROOT / 'tools/verify_btc5m_repair_profit_budget_v1.py'),
               ticket_condition='User-selected historical15; not a learned optimal quantity or deployment rule')
    (R / (STEM + '_RESULT.json')).write_text(json.dumps(out, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: brief(a) for k, a in out['arms'].items()}, indent=2))


if __name__ == '__main__':
    main()
