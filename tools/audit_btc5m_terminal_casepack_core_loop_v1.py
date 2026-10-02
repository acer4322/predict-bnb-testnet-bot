"""Offline case witnesses using existing payoff and prefix-response measurements.

Consumes the supplied casepack, never imports its writer or opens its database.
Realized bucket prices and outcome-selected witnesses are descriptive only.
No fitting, policy mutation, worker dispatch, or native execution.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

from audit_btc5m_post_exposure_response_v1 import analyze, reconstruct, sum_flow
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/research/target_casepacks_v1/TARGET_TERMINAL_SMALL_LOSS_AND_LOCK_CASEPACK_V1_20260913.json'
OUT = SOURCE.parent / 'TARGET_CASEPACK_CORE_LOOP_ANALYSIS_V1_20260913.json'
EPS = 1e-7
SIDES = ('UP', 'DOWN')
ROUTES = ('MAKER', 'TAKER')
# Post-hoc witnesses, explicitly separate from the existing prefix selector.
PHASES = {
    1871599: [('terminal_weak_branch_repair', 283, 290)],
    2202440: [('late_repair', 211, 260), ('after_cheap_repair', 257, 260)],
    1962121: [('late_lock_creation', 290, 297), ('after_final_lock_entry', 292, 297)],
    2210304: [('renewed_exposure', 120, 121), ('repair_after_exposure', 121, 124),
              ('continued_fills_after_final_lock_entry', 124, 268)],
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a, b):
    assert math.isfinite(a) and math.isfinite(b) and abs(a - b) < EPS, (a, b)


def compact_state(row, start):
    return dict(seconds=(row['t'] - start) / 1000, **row['geometry'])


def verify_reconstruction(case):
    summary = case['summary']
    assert summary['quoteTypes'] == ['BID'] and summary['sellProceedsUsdt'] == 0
    source_rows = case['timelineBySecond']
    assert len({r['eventMs'] for r in source_rows}) == len(source_rows)
    assert all(a['eventMs'] < b['eventMs'] for a, b in zip(source_rows, source_rows[1:]))
    start = summary['windowEndMs'] - 300000
    legs = []
    for row in source_rows:
        assert row['eventMs'] % 1000 == 0
        close(row['secondsFromWindowStart'], (row['eventMs'] - start) / 1000)
        close(row['secondsLeft'], (summary['windowEndMs'] - row['eventMs']) / 1000)
        for key, bucket in row['fillsByRoleSide'].items():
            route, side = key.split(':')
            assert route in ROUTES and side in SIDES
            assert bucket['fills'] > 0 and bucket['shares'] > 0
            assert 0 < bucket['notional'] < bucket['shares']
            legs.append(dict(t=row['eventMs'], side=side, route=route,
                             qty=bucket['shares'], cash=bucket['notional']))
    rows = reconstruct(legs)
    assert rows == reconstruct(list(reversed(legs)))
    assert len(rows) == len(source_rows)
    counts = 0
    role_qty = {route: dict(UP=0., DOWN=0.) for route in ROUTES}
    role_cost = dict.fromkeys(ROUTES, 0.)
    for row, src in zip(rows, source_rows):
        assert row['t'] == src['eventMs']
        close(row['cost'], src['cumulativeBuyNotional'])
        close(row['inv']['UP'], src['cumulativeUpShares'])
        close(row['inv']['DOWN'], src['cumulativeDownShares'])
        for field, original in [('up', 'pnlIfUp'), ('down', 'pnlIfDown'),
                                ('floor', 'worstCasePnl'), ('best', 'bestCasePnl')]:
            close(row['geometry'][field], src[original])
        assert (row['geometry']['floor'] > 0) == src['bothPositive']
        n = sum(b['fills'] for b in src['fillsByRoleSide'].values())
        assert n == src['fillsThisSecond']
        counts += n
        assert counts == src['cumulativeFills']
        # Reconstruct receives aggregated route buckets; restore recorded leg counts.
        for side in SIDES:
            for route in ROUTES:
                f = row['flow'][side][route]
                f['legs'] = src['fillsByRoleSide'].get(f'{route}:{side}', {}).get('fills', 0)
                role_qty[route][side] += f['qty']
                role_cost[route] += f['cash']
        for route, state in src['roleState'].items():
            close(role_cost[route], state['buyNotional'])
            for side, field in [('UP', 'upShares'), ('DOWN', 'downShares')]:
                close(role_qty[route][side], state[field])
                close(role_qty[route][side] - role_cost[route], state['pnlIf' + side.title()])
    last = rows[-1]
    assert counts == summary['fillCount']
    for field, original in [('cost', 'buyNotionalUsdt'), ('inventory_up', 'upPositionShares'),
                            ('inventory_down', 'downPositionShares'), ('up', 'pnlIfUp'),
                            ('down', 'pnlIfDown'), ('floor', 'worstCasePnl'), ('best', 'bestCasePnl')]:
        close(last['geometry'][field], summary[original])
    # Winner is consulted only to validate the supplied terminal accounting label.
    winner = summary['winner']
    other = 'DOWN' if winner == 'UP' else 'UP'
    close(last['geometry'][winner.lower()], summary['actualWinnerPnl'])
    close(last['geometry'][other.lower()], summary['counterfactualOppositePnl'])
    for route, field in [('MAKER', 'makerNetPnlUsdt'), ('TAKER', 'takerNetPnlUsdt')]:
        close(role_qty[route][winner] - role_cost[route], summary[field])
    for cp in case['checkpoints']:
        match = next(r for r in source_rows if r['eventMs'] == cp['eventMs'])
        for k, v in cp.items():
            assert match[k] == v
    return rows, start, summary['windowEndMs']


def bucket_geometry(row, start):
    """Frozen pre-bucket weak side; no intrasecond order-intent assumptions."""
    before = row['before']['geometry']
    net = before['up_net']
    if abs(net) < EPS:
        return None
    strong, weak = ('UP', 'DOWN') if net > 0 else ('DOWN', 'UP')
    f, b = before[weak.lower()], before[strong.lower()]
    qty = {s: sum(row['flow'][s][r]['qty'] for r in ROUTES) for s in SIDES}
    cash = {s: sum(row['flow'][s][r]['cash'] for r in ROUTES) for s in SIDES}
    lift = qty[weak] - cash[weak]
    drag = cash[strong]
    close(row['geometry'][weak.lower()] - f, lift - drag)
    price = cash[weak] / qty[weak] if qty[weak] else None
    q_zero = -f / (1 - price) if f < 0 and price is not None else None
    q_other_zero = b / price if b >= 0 and price is not None else None
    threshold = b / (b - f) if f < 0 < b else None
    return dict(seconds=(row['t'] - start) / 1000, before_weak_side=weak,
                before_weak_payoff=f, before_strong_payoff=b, before_gap=abs(net),
                weak_qty=qty[weak], strong_qty=qty[strong], weak_cash=cash[weak], strong_cash=drag,
                weak_lift=lift, net_weak_payoff_change=lift - drag,
                realized_weak_average_price=price,
                no_addition_constant_price_q_to_weak_zero=q_zero,
                no_addition_constant_price_q_to_strong_zero=q_other_zero,
                no_addition_constant_price_feasibility_threshold=threshold,
                pure_weak_acquisition=qty[weak] > 0 and qty[strong] == 0,
                both_sides_filled=all(qty[s] > 0 for s in SIDES),
                after=compact_state(row, start), flow=row['flow'])


def phase(rows, start, name, left, right):
    begin = next(r for r in rows if r['t'] == start + left * 1000)
    final = next(r for r in rows if r['t'] == start + right * 1000)
    within = [r for r in rows if begin['t'] < r['t'] <= final['t']]
    flow = sum_flow(within)
    qty = {s: sum(flow[s][r]['qty'] for r in ROUTES) for s in SIDES}
    cash = {s: sum(flow[s][r]['cash'] for r in ROUTES) for s in SIDES}
    total = sum(cash.values())
    delta = {s: qty[s] - total for s in SIDES}
    for s in SIDES:
        close(final['geometry'][s.lower()] - begin['geometry'][s.lower()], delta[s])
    close(final['cost'] - begin['cost'], total)
    return dict(name=name, selection='POST_HOC_DESCRIPTIVE_ONLY',
                interval='left state excluded, right state included',
                before=compact_state(begin, start), after=compact_state(final, start),
                delta_qty=qty, delta_cash=cash, total_added_cost=total, delta_payoff=delta,
                added_inventory_standalone_floor=min(qty.values()) - total,
                average_acquisition_price={s: cash[s] / qty[s] if qty[s] else None for s in SIDES},
                added_fill_legs=sum(flow[s][r]['legs'] for s in SIDES for r in ROUTES), flow=flow)


def case_analysis(case):
    rows, start, end = verify_reconstruction(case)
    summary = case['summary']
    mid = summary['marketId']
    # This existing selector depends on historical fills and elapsed separation,
    # not winner, terminal case label, global peak, or future recovery.
    measured = analyze(rows, start, end)
    intervals = []
    opened = None
    for row in rows:
        positive = row['geometry']['floor'] > 0
        if positive and opened is None:
            opened = (row['t'] - start) / 1000
        elif not positive and opened is not None:
            intervals.append([opened, (row['t'] - start) / 1000])
            opened = None
    if opened is not None:
        intervals.append([opened, (end - start) / 1000])
    initial = dict(t=start, inv=dict(UP=0., DOWN=0.), cost=0.)
    path = path_summary(initial, rows, start, end)
    close(sum(b - a for a, b in intervals), path['both_positive_seconds'])
    first = intervals[0][0] if intervals else None
    stable = intervals[-1][0] if intervals and intervals[-1][1] == 300 else None
    for actual, field in [(first, 'firstBothPositive'), (stable, 'stableBothPositiveFrom')]:
        expected = summary[field]
        assert (actual is None) == (expected is None)
        if actual is not None:
            close(actual, 300 - expected['secondsLeft'])
    for field, choose in [('minWorstCaseFloor', min), ('maxWorstCaseFloor', max)]:
        observed = choose(rows, key=lambda r: r['geometry']['floor'])
        assert observed['t'] == summary[field]['eventMs']
        close(observed['geometry']['floor'], summary[field]['worstCasePnl'])
    both = [r for r in rows if r['before']['geometry']['floor'] > 0]
    return dict(market_id=mid, validation='PASS', supplied_terminal_summary=summary,
                terminal=measured['terminal'], first_both_positive_second=first,
                final_unbroken_both_positive_second=stable, both_positive_intervals=intervals,
                both_positive_seconds=path['both_positive_seconds'],
                minimum_floor=path['minimum_floor'],
                negative_floor_area=path['negative_floor_area_currency_seconds'],
                total_seconds_with_fills=len(rows), recorded_fill_legs=summary['fillCount'],
                event_buckets_after_positive_prebucket_state=len(both),
                positive_to_nonpositive_transitions=sum(r['geometry']['floor'] <= 0 for r in both),
                geometry_by_bucket=[b for r in rows if (b := bucket_geometry(r, start)) is not None],
                phases=[phase(rows, start, *p) for p in PHASES[mid]],
                prefix_selection_future_invariant=measured['prefix_selection_future_invariant'],
                prefix_anchors=measured['prefix_anchors'], descriptive_global_peak=measured['peak'])


def main():
    begin = time.perf_counter()
    source_hash = sha(SOURCE)
    supplied = json.loads(SOURCE.read_text(encoding='utf-8-sig'))
    cases = [case_analysis(c) for c in supplied['cases']]
    for c in cases:
        responses = [a['windows']['30'] for a in c['prefix_anchors'] if a['windows']['30']['complete_window']]
        c['prefix_30s_summary'] = dict(windows=len(responses),
            ongoing_strong=sum(r['ongoing_strong_acquisition'] for r in responses),
            weak_acquisition=sum(r['weak_acquisition'] for r in responses),
            both_routes_on_weak=sum(r['both_routes_on_weak'] for r in responses),
            weak_payoff_improved=sum(r['weak_payoff_change'] > EPS for r in responses),
            weak_payoff_worsened=sum(r['weak_payoff_change'] < -EPS for r in responses),
            direction_retained=sum(r['direction_retained_entire_window'] for r in responses))
    dependencies = ['audit_btc5m_post_exposure_response_v1.py', 'btc5m_exposure_suppression_metrics_v1.py']
    out = dict(version='TARGET_CASEPACK_CORE_LOOP_ANALYSIS_V1', status='COMPLETE', runtime_eligible=False,
        source=str(SOURCE.relative_to(ROOT)), source_sha256=source_hash,
        source_generated_utc=supplied['generatedUtc'], source_population=supplied['population'],
        population_status='SUPPLIED_SNAPSHOT_NOT_REQUERIED',
        script_sha256=sha(Path(__file__)), dependency_sha256={p: sha(ROOT / 'tools' / p) for p in dependencies},
        validation=dict(all_case_accounting='PASS', cases=len(cases),
            event_second_buckets=sum(c['total_seconds_with_fills'] for c in cases),
            recorded_fill_legs=sum(c['recorded_fill_legs'] for c in cases),
            source_hash_unchanged=sha(SOURCE) == source_hash,
            native_runs=0, model_fits=0, parameter_search=0),
        measurement_contract=dict(
            timing='Event-second aggregates; carry state to next event then window end. Intrasecond path unknown.',
            direction='Frozen pre-bucket or prefix-anchor inventory sign; no winner or final direction for selectors.',
            quantity='Recorded aggregated fills, not NEW order sizes or inferred private intentions.',
            prices='Realized bucket averages, not observed contemporaneous public executable quotes.',
            feasibility='Algebraic diagnostic for a single weak-side buy at constant price with no other fills; no hard veto or cash budget.',
            fees='NO_EXPLICIT_FEE; sub-dollar endpoint signs are fee-sensitive.',
            selection='Four outcome-selected mechanism witnesses; prefix windows are not independent trials.',
            lifecycle='Target outstanding/cancel/receipt state and missed opportunities unavailable.'),
        elapsed_seconds=time.perf_counter() - begin, cases=cases)
    assert out['validation']['source_hash_unchanged']
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(dict(output=str(OUT.relative_to(ROOT)), validation=out['validation'],
        elapsed_seconds=out['elapsed_seconds'], cases=[dict(market_id=c['market_id'],
        both_positive_intervals=c['both_positive_intervals'], prefix_30s=c['prefix_30s_summary']) for c in cases])))


if __name__ == '__main__':
    main()
