"""Independent saved trajectory and executable capacity-difference audit; no replay."""
import json
import math
from audit_btc5m_parallel_payoff_panel_v1 import PACKAGE, STEM, R, RET, read, sha, get, STREAMS


def main():
    manifest = read(PACKAGE / 'manifest.json')
    assert manifest == read(R / 'BTC5M_PARALLEL_PAYOFF_PREREGISTERED_20260913.json')
    report = read(R / (STEM + '_RESULT.json'))
    assert report['status'] == 'COMPLETE' and all(report['control_parity'].values())
    cut, end = manifest['selection']['t'], 1788758400000
    traces = {}
    audited = {}
    for tag in ('control', 'quantity', 'zero'):
        folder = RET / f'parallel-payoff-2026085-{tag}-20260913-v1'
        d, tr = get(folder)
        traces[tag] = tr
        a = report['arms'][tag]
        assert sha(folder / 'result.json') == a['source_sha256']
        assert sha(folder / 'clock_trace.json.gz') == a['trace_sha256']
        assert d['execution_accounting_valid'] and d['unresolved_owners'] == 0
        assert d['clock_smoke']['actual_replay_frames'] == 1462
        # Independent rectangle integration from deduplicated endpoint states.
        points = {cut: tr['money_events'][0]['state']}
        for row in tr['states']:
            if cut < row['t'] <= end: points[row['t']] = row
        times = sorted(points)
        area = math.fsum(max(0., points[t]['cost'] - min(points[t]['inv'].values())) * (nxt-t)/1000.
                         for t,nxt in zip(times, times[1:]+[end]))
        worst = min(min(r['inv'].values())-r['cost'] for r in points.values())
        assert abs(area-a['trajectory']['negative_floor_area_currency_seconds']) < 1e-7
        assert abs(worst-a['trajectory']['minimum_floor']) < 1e-7
        assert all(abs(v)<1e-7 for v in a['post_end_drain_delta'].values())
        assert tr['states'][-1]['inv'] == d['final_inventory']
        assert abs(tr['states'][-1]['cost']-d['final_cost']) < 1e-7
        assert a['parallel_exercise']
        if tag != 'control':
            assert all(r['inv']['UP'] >= r['inv']['DOWN']-1e-7 for r in points.values())
        audited[tag] = dict(actual_replay_frames=1462, all_native_safety_flags=d['safety_gate'],
            independent_loss_area=area, independent_minimum_floor=worst,
            terminal_owner_count=d['unresolved_owners'], post_end_economic_delta_zero=True,
            final_pending_cash_direct=None,
            pending_cash_note='Final cash reservations are not serialized as a separate field; do not copy the pre-end intent snapshot. Native final ledger invariants and receipt reconciliation passed.')
    q, z = traces['quantity'], traces['zero']
    identical = {k:q[k] == z[k] for k in (*STREAMS, 'intent')}
    assert all(identical.values())
    assert len(q['money_rows']) == len(z['money_rows'])
    differences = []
    for a,b in zip(q['money_rows'],z['money_rows']):
        assert all(a[k] == b[k] for k in ('t','side','price','requested','state'))
        if a['capped'] != b['capped']:
            minimum = max(18., 1./a['price'])
            assert a['side'] == 'DOWN' and a['capped'] < minimum and b['capped'] < minimum
            differences.append(dict(t=a['t'], side=a['side'], price=a['price'], quantity_cap=a['capped'],
                                    money_cap=b['capped'], required_minimum=minimum))
    assert len(differences) == 19
    weak = [r for r in z['money_rows'] if r['side']=='DOWN']
    contrast = dict(candidate_visits=len(z['money_rows']), down_visits=len(weak),
        money_need_below_quantity_need=sum(r['money_need'] < r['quantity_need']-1e-9 for r in weak),
        down_visits_with_pending_up_cash=sum(r['state']['pending_cash']['UP'] > 0 for r in weak),
        changed_candidates=len(differences), changed_executable_orders=0,
        all_changed_candidates_below_original_minimum=True, differences=differences,
        identical_full_streams=identical,
        verdict='MONETARY_CONTRAST_NOT_EXERCISED_AT_EXECUTABLE_ORDER_LEVEL')
    c, q = report['arms']['control'], report['arms']['quantity']
    delta = dict(loss_area_change_fraction=q['trajectory']['negative_floor_area_currency_seconds']/c['trajectory']['negative_floor_area_currency_seconds']-1.,
                 final_cost_fraction=q['metrics']['cost']/c['metrics']['cost'],
                 final_up_payoff=q['terminal']['up'], final_down_payoff=q['terminal']['down'])
    target = read(R / 'BTC5M_EXPOSURE_SUPPRESSION_METRIC_V1_20260913.json')
    prior_target = read(R / 'BTC5M_PAYOFF_REPAIR_V1_20260913_TARGET.json')
    for mid, row in target['target_markets'].items():
        for key in ('up', 'down', 'cost'):
            assert abs(row['terminal'][key]-prior_target['markets'][mid]['terminal'][key]) < 1e-7
    output = dict(status='PASS', native_rerun=False, fit=False, trajectory_audits=audited,
        monetary_contrast=contrast, quantity_vs_control=delta, target_matches_prior_accounting=True,
        result_sha256=sha(R / (STEM + '_RESULT.json')),
        conclusion='Parallel activity and fixed-UP retention exercised, but terminal profit ratio hides worse intermediate exposure; monetary rule did not change any executable order. No learned-policy or economic-superiority claim.')
    (R / 'BTC5M_PARALLEL_PAYOFF_VERIFICATION_20260913.json').write_text(json.dumps(output, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(status='PASS', monetary_contrast={k:v for k,v in contrast.items() if k!='differences'}, quantity_vs_control=delta)))


if __name__ == '__main__': main()
