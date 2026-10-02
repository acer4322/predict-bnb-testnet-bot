"""Saved native trace comparison; no native execution, training or refitting."""
import argparse
import json
import math
from aggregate_btc5m_exposure_intent_ablation_v1 import get, ROOT, R, RET, STREAMS
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS, compact
from audit_btc5m_target_core_loop_topology_v1 import read, sha
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary

STEM = 'BTC5M_SINGLE_REPAIR_DEMAND_V1_20260913'
PACKAGE = ROOT / '.lan_worker_v1/single_repair_demand_2026085_20260913_v1'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--control-only', action='store_true')
    args = ap.parse_args()
    m = read(PACKAGE / 'manifest.json')
    assert m == read(R / (STEM + '_PREREGISTERED.json'))
    assert all(sha(PACKAGE / p) == h for p, h in m['files'].items())
    base, bt = get(RET / 'parallel-payoff-2026085-quantity-20260913-v1')
    control, ct = get(RET / 'single-repair-demand-2026085-control-20260913-v1')
    parity = {k: base[k] == control[k] for k in PARITY_FIELDS}
    parity.update({k: bt[k] == ct[k] for k in (*STREAMS, 'intent', 'money_rows')})
    assert all(parity.values()), parity
    assert len(ct['demand_events']) == 1 and not ct['demand_events'][0]['applied']
    output = dict(status='PASS', control_parity=parity, control_parity_pass=True, native_runs_here=0)
    if args.control_only:
        (R / (STEM + '_CONTROL.json')).write_text(json.dumps(output,indent=2)+'\n', encoding='utf-8')
        print(json.dumps(output))
        return
    pulse, pt = get(RET / 'single-repair-demand-2026085-pulse-20260913-v1')
    cut = m['demand_selection']['t']
    end = 1788758400000
    prefix = {k: [r for r in ct[k] if r['t'] < cut] == [r for r in pt[k] if r['t'] < cut]
              for k in (*STREAMS, 'intent', 'money_rows')}
    assert all(prefix.values()), prefix
    assert len(pt['demand_events']) == 1 and pt['demand_events'][0]['applied']
    assert pt['demand_events'][0]['state'] == ct['demand_events'][0]['state']
    assert pt['demand_events'][0]['original_desired'] == ct['demand_events'][0]['original_desired']
    new = [o for p in pt['plans'] if p['t'] == cut for o in p['operations'] if o['kind'] == 'NEW']
    cp_new = [o for p in ct['plans'] if p['t'] == cut for o in p['operations'] if o['kind'] == 'NEW']
    extra = [o for o in new if o not in cp_new]
    assert len(extra) == 1 and extra[0]['side'] == 'DOWN' and extra[0]['qty'] == 30. and extra[0]['price'] == .31, extra
    key = extra[0]['key']
    native_new = [r for r in pt['native_actions'] if r['t'] == cut and r['kind'] == 'NEW'
                  and r['side'] == 'DOWN' and r['qty'] == 30. and r['price'] == .31]
    assert len(native_new) == 1, 'require actual native submit, not only a plan'
    fill_rows = [dict(t=e['t'], **r) for e in pulse['atomic_responsibility_events']
                 for r in e['fill_rows'] if r['key'] == key]
    filled = math.fsum(r['fill_increment'] for r in fill_rows)
    assert filled <= 30. + 1e-7
    cancels = [dict(t=p['t'], **o) for p in pt['plans'] for o in p['operations']
               if o['kind'] == 'CANCEL' and o['key'] == key]
    terminal_candidates = [r for r in pulse['candidate_checkpoints'] if r['source_key'] == key]
    owners = [dict(t=r['t'], **o) for r in pt['money_rows'] for o in r['state']['owners'] if o['key'] == key]
    first_seen = min((r['t'] for r in owners), default=None)
    absent = [r['t'] for r in pt['money_rows'] if first_seen is not None and r['t'] > first_seen
              and not any(o['key'] == key for o in r['state']['owners'])]
    terminal_upper = min(absent, default=None)
    maintenance = None
    if cancels:
        t = cancels[0]['t']
        intent = next(r for r in pt['intent'] if r['t'] == t)
        control_intent = next(r for r in ct['intent'] if r['t'] == t)
        threshold = math.exp(pulse['theta'][7]) / (1 + math.exp(-pulse['theta'][11]))
        owned = intent['inv']['DOWN'] + intent['reserved_qty']['DOWN']
        control_owned = control_intent['inv']['DOWN'] + control_intent['reserved_qty']['DOWN']
        maintenance = dict(t=t, delay_ms=t-cut, desired_down=intent['desired']['DOWN'],
                           owned_plus_reserved_down=owned, signed_deficit=intent['desired']['DOWN']-owned,
                           negative_surplus_threshold=-threshold,
                           surplus_predicate=intent['desired']['DOWN']-owned < -threshold,
                           control_signed_deficit=control_intent['desired']['DOWN']-control_owned,
                           control_surplus_predicate=control_intent['desired']['DOWN']-control_owned < -threshold,
                           pulse_plan=next(r['operations'] for r in pt['plans'] if r['t']==t),
                           control_plan=next(r['operations'] for r in ct['plans'] if r['t']==t),
                           attribution='The frozen surplus predicate alone suffices to cancel. The raw frame quote/stale predicate is not serialized here.')
        assert maintenance['surplus_predicate']
    lifecycle = dict(order=extra[0], requested=30., notional=9.3, filled=filled,
                     filled_fraction=filled / 30., fill_rows=fill_rows,
                     first_fill_t=fill_rows[0]['t'] if fill_rows else None,
                     fill_complete_t=fill_rows[-1]['t'] if abs(filled - 30.) < 1e-7 else None,
                     cancels=cancels, observed_owners=owners, native_new=native_new, maintenance=maintenance,
                     terminal_candidate_checkpoints=terminal_candidates,
                     terminal_absence_upper_bound=terminal_upper, exact_terminal_receipt_t=None,
                     note='Cumulative fills come from canonical ledger observations. Absence from a later snapshot bounds terminal/release time; a raw terminal receipt timestamp is not serialized.')
    arms = {}
    for tag, result, tr in (('control',control,ct), ('pulse',pulse,pt)):
        assert result['worker'] == base['worker'] and result['theta'] == base['theta']
        assert result['runtime_eligible'] is False and result['active_native_submits'] == 0
        assert result['clock_smoke']['manifest_sha256'] == sha(PACKAGE / 'manifest.json')
        assert result['clock_smoke']['actual_replay_frames'] == 1462
        assert result['max_epoch_residual_overfill'] <= 1e-9
        initial = tr['demand_events'][0]['state']
        path = path_summary(initial, tr['states'], cut, end)
        points = {cut: initial}
        points.update({r['t']: r for r in tr['states'] if cut < r['t'] <= end})
        times = sorted(points)
        area = math.fsum(max(0.,points[t]['cost']-min(points[t]['inv'].values()))*(n-t)/1000.
                         for t,n in zip(times,times[1:]+[end]))
        assert abs(area-path['negative_floor_area_currency_seconds']) < 1e-7
        assert abs(min(min(r['inv'].values())-r['cost'] for r in points.values())-path['minimum_floor']) < 1e-7
        assert result['final_inventory'] == tr['states'][-1]['inv']
        assert abs(result['final_cost'] - tr['states'][-1]['cost']) < 1e-7
        assert all(abs(path['market_end']['inventory_'+s.lower()] - result['final_inventory'][s]) < 1e-7 for s in ('UP','DOWN'))
        assert abs(path['market_end']['cost'] - result['final_cost']) < 1e-7
        for r in tr['money_rows']:
            for owner in r['state']['owners']:
                assert owner['qty'] >= 0 and owner['state'] != 'TERMINAL'
            for s in ('UP','DOWN'):
                assert abs(sum(o['qty'] for o in r['state']['owners'] if o['side'] == s)-r['state']['pending_qty'][s]) < 1e-7
        folder = RET / f'single-repair-demand-2026085-{tag}-20260913-v1'
        arms[tag] = dict(metrics=compact(result), terminal=geometry(result['final_inventory'],result['final_cost']),
                         trajectory=path, native_safety=result['safety_gate'], unresolved_owners=result['unresolved_owners'],
                         post_end_economic_delta_zero=True, final_pending_cash_direct=None,
                         source_sha256=sha(folder/'result.json'), trace_sha256=sha(folder/'clock_trace.json.gz'),
                         new_by_side={s:sum(o['kind']=='NEW' and o['side']==s for p in tr['plans'] if cut<=p['t']<end for o in p['operations']) for s in ('UP','DOWN')})
    output.update(status='COMPLETE', verification='PASS', prefix=prefix, lifecycle=lifecycle, arms=arms,
                  request_event=pt['demand_events'][0], local_fits=0, policy_weights_changed=False,
                  scope='One-frame demand/unchanged-maintenance native diagnostic, not a minimum-size overshoot strategy or learned Target policy.')
    (R / (STEM + '_RESULT.json')).write_text(json.dumps(output,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(status=output['status'],verification='PASS',
        lifecycle={k:v for k,v in lifecycle.items() if k not in ('observed_owners','terminal_candidate_checkpoints')},
        arms={tag:dict(metrics=a['metrics'],terminal=a['terminal'],trajectory={k:v for k,v in a['trajectory'].items() if k!='changed_rows'},new_by_side=a['new_by_side']) for tag,a in arms.items()}),indent=2))


if __name__ == '__main__':
    main()
