"""Offline observed-fill topology comparison; no policy, HFT, fitting or dispatch.

Reuse the frozen atomic accounting implementation for both cohorts. Inferred
FIFO allocation is a measurement convention, never proof of Target internals.
"""
from __future__ import annotations
import ast
import bisect
import collections
import gzip
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / 'data/research'
RET = R / 'lan_worker_returns'
MIDS = (2022527, 2022538, 2022602, 2023438, 2026085, 2026817, 2028352, 2029246)
SIDES = ('UP', 'DOWN')
EPS = 1e-9
STEM = 'BTC5M_TARGET_CORE_LOOP_TOPOLOGY_AUDIT_V1_20260912'
FROZEN = ROOT / '.lan_worker_v1/causal_clock_smoke_2026085_20260912_v1/frozen_runner.py'


def read(p):
    if p.suffix == '.gz':
        with gzip.open(p, 'rb') as f:
            raw = f.read(16 * 1024 * 1024 + 1)
        assert len(raw) <= 16 * 1024 * 1024
        return json.loads(raw)
    return json.loads(p.read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load_ledger():
    assert sha(FROZEN) == '1cbcce2da93c5cb140816c4200434dfc4fa9d83a871a808b8ebedd855253f893'
    tree = ast.parse(FROZEN.read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AtomicResponsibilityLedger')
    env = {'EPS_ATOMIC': EPS}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(FROZEN), 'exec'), env)
    return env['AtomicResponsibilityLedger']


Ledger = load_ledger()


def batches(rows, resolution=1):
    grouped = collections.defaultdict(lambda: {'UP': 0., 'DOWN': 0.})
    for r in rows:
        t = int(r['t']) // resolution * resolution
        for s in SIDES:
            grouped[t][s] += r[s]
    return [dict(t=t, **v) for t, v in sorted(grouped.items())]


def side_of(q):
    return 'UP' if q['UP'] - q['DOWN'] > EPS else 'DOWN' if q['DOWN'] - q['UP'] > EPS else 'FLAT'


def topology(rows):
    ledger = Ledger()
    labels = []
    metrics = collections.Counter()
    simplified = []
    direct_inventory = {'UP': 0., 'DOWN': 0.}
    for r in rows:
        direct_before = side_of(direct_inventory)
        for s in SIDES:
            direct_inventory[s] += r[s]
        ev = ledger.process_batch(r['t'], r['UP'], r['DOWN'])
        if ev is None:
            continue
        before, after = side_of(ev['outstanding_before']), side_of(ev['outstanding_after'])
        # Independent cumulative-fill signs do not use FIFO identities.
        assert before == direct_before and after == side_of(direct_inventory)
        pay, birth, direct = bool(ev['payments']), bool(ev['births']), ev['direct_pair_qty'] > EPS
        label = '+'.join(k for k, v in [('P', pay), ('B', birth), ('D', direct)] if v)
        labels.append(label)
        metrics['fill_batches'] += 1
        metrics['two_sided_batches'] += int(r['UP'] > EPS and r['DOWN'] > EPS)
        metrics['payment_batches'] += int(pay)
        metrics['birth_batches'] += int(birth)
        metrics['payment_keeps_surplus_side'] += int(pay and before == after and before != 'FLAT')
        metrics['payment_flips_surplus_side'] += int(pay and before != after and after != 'FLAT')
        metrics['payment_ends_flat'] += int(pay and after == 'FLAT')
        metrics['birth_while_old_residual_remains'] += int(any(x['stacked'] for x in ev['births']))
        simplified.append(dict(t=r['t'], before=before, after=after, label=label,
            outstanding_before=ev['outstanding_before'], outstanding_after=ev['outstanding_after'],
            fill_up=r['UP'], fill_down=r['DOWN']))
    summary = ledger.summary()
    assert summary['pass']
    assert abs(sum(summary['outstanding'].values()) - abs(summary['total_fill']['UP'] - summary['total_fill']['DOWN'])) < 1e-7
    # P-only -> B-only -> P-only on compressed materialization states, with
    # the same nonzero surplus side throughout: an observable partial-service loop.
    runs = []
    for e in simplified:
        if not runs or runs[-1][-1]['label'] != e['label']:
            runs.append([])
        runs[-1].append(e)
    witnesses = []
    for a, b, c in zip(runs, runs[1:], runs[2:]):
        if [a[0]['label'], b[0]['label'], c[0]['label']] != ['P', 'B', 'P']:
            continue
        events = a + b + c
        sides = {s for e in events for s in (e['before'], e['after'])}
        if len(sides) == 1 and 'FLAT' not in sides:
            witnesses.append(dict(start=a[0]['t'], birth_start=b[0]['t'], end=c[-1]['t'],
                                  surplus_side=next(iter(sides)), events=events))
    metrics['same_surplus_P_B_P_motifs'] = len(witnesses)
    metrics['payment_keep_fraction'] = metrics['payment_keeps_surplus_side'] / max(1, metrics['payment_batches'])
    metrics['birth_stack_fraction'] = metrics['birth_while_old_residual_remains'] / max(1, metrics['birth_batches'])
    return dict(metrics=dict(metrics), atomic_summary=summary,
                label_counts=dict(collections.Counter(labels)),
                transition_counts=dict(collections.Counter(a+' -> '+b for a,b in zip(labels,labels[1:]))),
                same_surplus_loop_witnesses=witnesses, events=simplified)


def target_rows(source):
    out = []
    for a in source['targetActions']:
        assert a['quote_type'] == 'BID' and a['side'] in SIDES
        assert 0 < a['price'] < 1 and a['shares'] > 0
        out.append(dict(t=a['event_ms'], UP=a['shares'] if a['side'] == 'UP' else 0.,
                        DOWN=a['shares'] if a['side'] == 'DOWN' else 0.))
    return batches(out)


def own_rows(d):
    assert d['status'] == 'COMPLETE' and d['safety_gate']['pass']
    return batches([dict(t=e['t'], UP=e['fill_up'], DOWN=e['fill_down']) for e in d['atomic_responsibility_events']])


def orientation(target, own):
    def path(rows):
        q = {'UP': 0., 'DOWN': 0.}
        result = []
        for r in rows:
            for s in SIDES:
                q[s] += r[s]
            result.append(dict(t=r['t'], side=side_of(q), net=q['UP']-q['DOWN']))
        return result
    tp, op = path(target), path(own)
    times = [x['t'] for x in op]
    matches, eligible, unknown = 0, 0, 0
    for t in tp:
        j = bisect.bisect_right(times,t['t'])-1
        o = op[j]['side'] if j >= 0 else 'FLAT'
        if t['side'] == 'FLAT' or o == 'FLAT':
            unknown += 1
        else:
            eligible += 1
            matches += int(t['side'] == o)
    return dict(agree=matches, eligible=eligible, flat_or_unstarted=unknown,
                fraction=matches/max(1,eligible), target_terminal_net=tp[-1]['net'],
                own_terminal_net=op[-1]['net'])


def check_own_parity(d, own):
    got = own['atomic_summary']
    expected = d['atomic_responsibility_summary']
    # Existing result can contain extra lineage statistics; all core fields match.
    for k,v in got.items():
        if k in ('responsibility_conservation_error','fill_allocation_error'):
            assert abs(v-expected[k]) < 1e-7
        else:
            assert v == expected[k], (k,v,expected[k])


def manager_feedback_audit(d):
    rows = []
    for ev in d['atomic_responsibility_events']:
        state = ev.get('manager_state')
        if state is None:
            continue
        inv, desired = state['inv'], state['desired']
        own_net = (inv['UP']-inv['DOWN']) / (1.+inv['UP']+inv['DOWN'])
        predicted = math.tanh(d['theta'][3]*own_net)
        actual = (desired['UP']-desired['DOWN']) / sum(desired.values())
        assert abs(predicted-actual) < 1e-12
        rows.append(dict(t=ev['t'], inventory_side=side_of(inv), desired_side=side_of(desired),
                         own_net=own_net, implied_desired_exposure=actual))
    eligible = [x for x in rows if x['inventory_side'] != 'FLAT']
    return dict(formula='desired_net_fraction = tanh(theta[3] * (UP-DOWN)/(1+UP+DOWN))',
        frozen_feedback_coefficient=d['theta'][3], checked_events=len(rows),
        nonflat_events=len(eligible),
        desired_opposes_inventory=sum(x['inventory_side'] != x['desired_side'] for x in eligible),
        first_events=rows[:6],
        interpretation='Desired exposure opposes own surplus; this is verified controller behavior, not a causal proof that it alone causes all realized crossings.')


def main():
    provenance = [dict(path=str(FROZEN.relative_to(ROOT)), sha256=sha(FROZEN))]
    out = dict(version=STEM, purpose='TARGET_OBSERVED_CORE_LOOP_COMPARISON_NOT_ECONOMIC_OPTIMIZATION',
        runtime_changes=False, training=False, HFT=False, fresh_or_sealed_used=False,
        definitions={'P':'confirmed payment to preexisting residual', 'B':'confirmed new residual birth',
            'D':'same-clock residual direct pair',
            'coarse_clock':'1000 ms floor buckets only for offline observation-resolution sensitivity; not a runtime timer',
            'FIFO':'imposed allocation convention; Target FIFO identity and controller intent are not observed'},
        historical8={}, current_factorial={}, provenance=provenance)
    for mid in MIDS:
        bundle = 'open_funding_recovery_train_20260911_v3' if mid in MIDS[:3] else 'v20_consumed_btc5_transfer5_20260912_v1'
        p = ROOT / '.lan_worker_v1' / bundle / f'input_{mid}.json.gz'
        source = read(p)
        assert source['market']['market_id'] == mid
        tr = target_rows(source)
        # Verify resolution before using common buckets.
        assert all(x['t'] % 1000 == 0 for x in tr)
        result_path = RET / f'v34-repair-lineage-{mid}-20260912-v1/result.json'
        d = read(result_path)
        own = own_rows(d)
        exact = topology(own)
        check_own_parity(d, exact)
        row = dict(target=topology(tr), own_native=exact, own_common_resolution=topology(batches(own,1000)),
            orientation_common_resolution=orientation(tr,batches(own,1000)),
            legacy_similarity=d['core_similarity'], legacy_clock_has_future_count_dependency=True)
        out['historical8'][str(mid)] = row
        provenance.extend(dict(path=str(x.relative_to(ROOT)),sha256=sha(x)) for x in (p,result_path))
        if mid == 2026085:
            selected_target = tr
    for arm in ('pa','aa','pd','ad'):
        p = RET / f'core-mech-2026085-{arm}-20260912-v1/result.json'
        d = read(p); own = own_rows(d); exact = topology(own)
        check_own_parity(d,exact)
        out['current_factorial'][arm] = dict(own_native=exact, own_common_resolution=topology(batches(own,1000)),
            orientation_common_resolution=orientation(selected_target,batches(own,1000)),
            legacy_similarity=d['core_similarity'], causal_clock=True,
            manager_feedback=manager_feedback_audit(d))
        if arm == 'pa':
            fork = d['clock_smoke']['fresh_hits'][0]
            earlier = [e for e in out['historical8']['2026085']['target']['events'] if e['t'] < fork['t']]
            out['previous_factorial_target_role_context'] = dict(checkpoint=fork['t'],
                target_observed_prior_event=earlier[-1], own_inventory=fork['inventory'],
                own_surplus_side=side_of(fork['inventory']),
                tested_own_repair_side=d['frontier_exact_hits'][0]['side'],
                tested_own_fresh_side=fork['side'],
                note='Offline observed role context only. The latest Target event is not claimed to be available to OUR at this clock.')
        provenance.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
    out['pooled_counts'] = {c: {k: sum(v[c]['metrics'].get(k,0) for v in out['historical8'].values())
        for k in ('fill_batches','payment_batches','payment_keeps_surplus_side','payment_flips_surplus_side',
                  'birth_batches','birth_while_old_residual_remains','same_surplus_P_B_P_motifs')}
        for c in ('target','own_common_resolution')}
    # Pure unit and side-label invariance checks, not candidate policy retuning.
    base = topology(selected_target)['metrics']
    assert topology([dict(t=x['t'],UP=x['DOWN'],DOWN=x['UP']) for x in selected_target])['metrics'] == base
    assert topology([dict(t=x['t'],UP=x['UP']*2,DOWN=x['DOWN']*2) for x in selected_target])['metrics'] == base
    out['verification'] = dict(native_atomic_summary_parity_12=True, cumulative_sign_independent_of_FIFO=True,
                               unit_scale_invariance=True, side_label_invariance=True,
                               target_whole_second_resolution_all8=True)
    out['limitations'] = [
        'Observed acquisitions only; Target original orders, pending capacity, zero fills and cancels are unknown.',
        'Native receipt milliseconds and Target whole-second event clocks differ; raw topology must not be compared without common-resolution sensitivity.',
        'Common buckets hide within-second ordering; cannot prove causal wake or concurrent active orders.',
        'Accounting identities alone do not identify Target strategy. Same-surplus motifs are observable structure, not hidden intent.',
        'Target inventory is reconstructed from observed window acquisitions assuming zero starting inventory; official hidden positions are not available.',
        'The retained-surplus pattern does not identify independent thesis, alpha, direction selection or a reason to ban crossings. Relative tranche/gap sizes and execution can contribute.',
        'Eight historical controls retain the known future-count clock dependency; only the four current arms use fixed TRAIN clock.',
        'No new significance test or held-out promotion; all markets already consumed.']
    (R/(STEM+'.json')).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    compact = {}
    for mid,row in out['historical8'].items():
        compact[mid] = {k: row[k]['metrics'] for k in ('target','own_native','own_common_resolution')}
    compact['current_factorial'] = {k: dict(metrics=v['own_common_resolution']['metrics'], orientation=v['orientation_common_resolution']) for k,v in out['current_factorial'].items()}
    print(json.dumps(compact,ensure_ascii=False))


if __name__ == '__main__':
    main()
