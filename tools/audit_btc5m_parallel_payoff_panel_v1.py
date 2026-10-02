"""Saved native continuation and Target geometry evaluation. No native execution."""
import argparse
import collections
import json
import math
from audit_btc5m_target_core_loop_topology_v1 import ROOT, R, RET, read, sha
from aggregate_btc5m_exposure_intent_ablation_v1 import get, STREAMS
from aggregate_btc5m_causal_clock_smoke_v1 import PARITY_FIELDS, compact
from btc5m_exposure_suppression_metrics_v1 import geometry, path_summary, self_test

PACKAGE = ROOT / '.lan_worker_v1/parallel_payoff_2026085_20260913_v1'
STEM = 'BTC5M_PARALLEL_PAYOFF_V1_20260913'


def target_metrics():
    examples = self_test()
    source_manifest = read(ROOT / '.lan_worker_v1/concurrent_flow_probe_20260913_v1/dataset.json')['sources']
    markets = {}
    for item in source_manifest:
        p = ROOT / item['path']
        assert sha(p) == item['sha256']
        source = read(p)
        buckets = collections.defaultdict(list)
        for leg in source['targetActions']:
            assert leg['quote_type'] == 'BID'
            buckets[leg['event_ms']].append(leg)
        inv, cost, curve = {'UP':0., 'DOWN':0.}, 0., []
        for t, legs in sorted(buckets.items()):
            for leg in legs:
                inv[leg['side']] += leg['shares']
                cost += leg['shares'] * leg['price']
            curve.append(dict(t=t, **geometry(inv, cost)))
        markets[str(source['market']['market_id'])] = dict(source_sha256=sha(p), terminal=curve[-1],
            buckets=len(curve), regimes=dict(collections.Counter(x['regime'] for x in curve)), curve=curve)
    old = read(R / 'BTC5M_PAYOFF_REPAIR_V1_20260913_RESULT.json')
    reference = {}
    for tag, arm in old['arms'].items():
        d, tr = get(RET / f'payoff-repair-2026085-{tag}-20260913-v1')
        assert sha(RET / f'payoff-repair-2026085-{tag}-20260913-v1/result.json') == arm['source_sha256']
        reference[tag] = dict(terminal=geometry(d['final_inventory'], d['final_cost']),
            submits=d['submits'], events=arm['metrics']['events'], postcut_new_up=arm['postcut_new_up'],
            last_payoff_change=arm['payoff_curve'][-1]['t'], cost_fraction_of_control=d['final_cost']/old['arms']['control']['metrics']['cost'])
    out = dict(status='COMPLETE', target_markets=markets, old_native_reference=reference,
               counterexamples=examples, limits=['Target observed buys, zero starting inventory and unknown fees.',
               'Terminal gain/loss is a geometry descriptor, not repair execution completeness, private intent or an online feature.',
               'Do not average ratios across undefined/no-profit/both-profit regimes; preserve every regime.'])
    (R / 'BTC5M_EXPOSURE_SUPPRESSION_METRIC_V1_20260913.json').write_text(json.dumps(out, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=out['status'], target_terminal={k:v['terminal'] for k,v in markets.items()}, old_native_reference=reference)))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('stage', choices=('metric','control','finish')); args = ap.parse_args()
    if args.stage == 'metric': target_metrics(); return
    self_test()
    m = read(PACKAGE / 'manifest.json')
    assert all(sha(PACKAGE / n) == h for n,h in m['files'].items())
    old, ot = get(RET / 'payoff-repair-2026085-control-20260913-v1')
    control, ct = get(RET / 'parallel-payoff-2026085-control-20260913-v1')
    parity = {k:old[k] == control[k] for k in PARITY_FIELDS}
    parity.update({k:ot[k] == ct[k] for k in STREAMS})
    assert all(parity.values()), parity
    out = dict(status='COMPLETE', control_parity=parity, oracle=True, runtime_eligible=False, numeric_parameters_frozen=True, arms={})
    for tag in (('control',) if args.stage == 'control' else ('control','quantity','zero')):
        folder = RET / f'parallel-payoff-2026085-{tag}-20260913-v1'
        d, tr = get(folder)
        assert d['clock_smoke']['manifest_sha256'] == sha(PACKAGE / 'manifest.json')
        assert d['oracle'] and not d['runtime_eligible'] and d['active_native_submits'] == 0
        assert d['theta'] == control['theta'] and d['clock_smoke']['actual_replay_frames'] == control['clock_smoke']['actual_replay_frames']
        cut = m['selection']['t']; end = 1788758400000
        start = tr['money_events'][0]
        assert start == ct['money_events'][0] and start['t'] == cut
        prefix = {k:[r for r in tr[k] if r['t'] < cut] == [r for r in ct[k] if r['t'] < cut] for k in STREAMS}
        assert all(prefix.values())
        reduced = 0
        for row in tr['money_rows']:
            st = row['state']
            for side in ('UP','DOWN'):
                owners = [o for o in st['owners'] if o['side'] == side]
                assert abs(math.fsum(o['qty'] for o in owners) - st['pending_qty'][side]) < 1e-7
                assert abs(math.fsum(o['qty']*o['limit'] for o in owners) - st['pending_cash'][side]) < 1e-7
                assert all(o['state'] != 'TERMINAL' for o in owners)
                assert abs(st['payoff'][side] - (st['inv'][side]-st['cost'])) < 1e-7
            if tag == 'control' or row['side'] == 'UP': expected = row['requested']
            else:
                need = max(0., st['inv']['UP']-st['inv']['DOWN']-st['pending_qty']['DOWN'])
                if tag == 'zero':
                    potential = st['payoff']['DOWN']+st['pending_qty']['DOWN']-st['pending_cash']['DOWN']-st['pending_cash']['UP']
                    need = min(need, max(0., -potential/(1.-row['price'])))
                expected = min(row['requested'], round(math.floor((need+1e-10)/.01)*.01, 8))
            assert abs(expected-row['capped']) < 1e-7
            reduced += int(row['capped'] < row['requested']-1e-9)
        new = [(p['t'],o) for p in tr['plans'] if p['t'] >= cut for o in p['operations'] if o['kind'] == 'NEW']
        for t,o in new:
            assert o['qty'] >= 18.-1e-9 and o['qty']*o['price'] >= 1.-1e-9
            assert any(r['t']==t and r['side']==o['side'] and r['price']==o['price'] and abs(r['capped']-o['qty'])<1e-7 for r in tr['money_rows'])
        path = path_summary(start['state'], tr['states'], cut, end)
        final = geometry(d['final_inventory'], d['final_cost'])
        postnew = dict(collections.Counter(o['side'] for t,o in new))
        exercise = all(postnew.get(s,0)>0 and path['acquired'][s]>0 for s in ('UP','DOWN')) and path['changes_first_half']>0 and path['changes_second_half']>0
        out['arms'][tag] = dict(metrics=compact(d), terminal=final, trajectory=path, prefix_parity=prefix,
            postcut_new_by_side=postnew, parallel_exercise=exercise, candidate_visits=len(tr['money_rows']), reduced_visits=reduced,
            post_end_drain_delta={k:final[k]-path['market_end'][k] for k in ('up','down','cost')},
            source_sha256=sha(folder/'result.json'), trace_sha256=sha(folder/'clock_trace.json.gz'))
    out['limits'] = m['limits']
    out['verdict'] = 'DIAGNOSTIC_ONLY_NO_LEARNED_POLICY_CLAIM'
    (R / (STEM + ('_CONTROL.json' if args.stage=='control' else '_RESULT.json'))).write_text(json.dumps(out, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(dict(status='COMPLETE', control_parity=True, arms={k:{n:v[n] for n in ('metrics','terminal','parallel_exercise','postcut_new_by_side')} for k,v in out['arms'].items()})))


if __name__ == '__main__': main()
