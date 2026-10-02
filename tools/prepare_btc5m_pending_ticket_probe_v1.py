"""Pin one causal ticket-completion experiment after diagnosing V20 traces.

Local execution only reads collected data and prepares Python sources. Never HFT.
"""
import ast
import collections
import inspect
import json
import shutil
import sys
from types import SimpleNamespace

from prepare_btc5m_unbudgeted_opportunity_v1 import ROOT, R, load, sha, once
from aggregate_btc5m_exposure_intent_ablation_v1 import get, RET
from audit_btc5m_target_core_loop_topology_v1 import read
from hft244_pair_route_legality_v1 import crossing_owners
import btc5m_pending_ticket_probe_v1 as probe

PARENT = ROOT / '.lan_worker_v1/fixed15_unbudgeted_opportunity_2026085_20260913_v1'
PACKAGE = ROOT / '.lan_worker_v1/fixed15_pending_ticket_2026085_20260913_v1'
BASE = RET / 'fixed15-core-loop-2026085-unbudgeted-active-20260913-v1'
CONTROL = RET / 'fixed15-core-loop-2026085-control-20260913-v2'
JOB = 'fixed15-core-loop-2026085-pending-ticket-20260913-v1'
STEM = 'BTC5M_PENDING_TICKET_V1_20260913'
START = 1788758100000


def dump(suffix, obj):
    (R / (STEM + '_' + suffix + '.json')).write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def diagnose(c, ct, a, at):
    frames = []
    for sec in (224.616, 229.791):
        t = START + round(sec * 1000)
        item = dict(seconds=sec, t=t)
        for tag, tr in [('control', ct), ('active', at)]:
            d = next(x for x in tr['demand_rows'] if x['t'] == t)
            s = d['state']; q = s['inv']['UP'] - s['inv']['DOWN'] - s['pending_qty']['DOWN']
            assert abs(q - d['eligibility']['quantity_capacity']) < 1e-7
            money = [{k: v for k, v in m.items() if k != 'state'} for m in tr['money_rows'] if m['t'] == t]
            plan = next(x for x in tr['plans'] if x['t'] == t)
            item[tag] = dict(state={k: v for k, v in s.items() if k != 'owners'},
                             quantity_gap=q, demand_work=d['work_id'], money=money, original_plan=plan)
        assert item['control']['quantity_gap'] >= 15 > item['active']['quantity_gap'] > 0
        assert abs(item['control']['quantity_gap'] - item['active']['quantity_gap'] - 14.21) < 1e-7
        old_ops = item['control']['original_plan']['operations']
        lost = [o for o in old_ops if o['kind'] == 'NEW' and o['side'] == 'DOWN']
        assert len(lost) == 1 and lost[0]['qty'] == 15
        assert not any(o['kind'] == 'NEW' and o['side'] == 'DOWN' for o in item['active']['original_plan']['operations'])
        owner = next(o for o in ct['demand_final']['all_final_carriers'] if o['key'] == lost[0]['key'])
        assert owner['filled'] == 15 and owner['state'] == 'TERMINAL'
        item['omitted_baseline_order'] = owner
        assert item['control']['demand_work'] is None and item['active']['demand_work'] is None
        frames.append(item)
    t = START + 179773
    ds = {tag: next(x['state'] for x in tr['demand_rows'] if x['t'] == t) for tag, tr in [('control', ct), ('active', at)]}
    hits = {tag: crossing_owners('UP', .91, [dict(key=o['key'], side=o['side'], price=o['limit']) for o in s['owners']]) for tag, s in ds.items()}
    assert hits == dict(control=[], active=[at['opportunity_submissions'][0]['key']])
    out = dict(status='PASS', native_jobs_added=0,
        source_hashes={str(p.relative_to(ROOT)): sha(p) for p in (CONTROL/'result.json', CONTROL/'clock_trace.json.gz', BASE/'result.json', BASE/'clock_trace.json.gz')},
        earliest_non_active_plan_difference=dict(t=t, seconds=179.773, unchanged_confirmed_inventory=ds['control']['inv'] == ds['active']['inv'],
            candidate_side='UP', candidate_price=.91, crossing_owners=hits,
            finding='Current Active pending owner legally blocks UP .91; do not remove own-cross legality.'),
        omitted_passive_orders=frames,
        omitted_total_qty=30., omitted_total_cash=sum(f['omitted_baseline_order']['payment'] for f in frames),
        interpretation='Both missing DOWN births fail the full15 quantity condition. No automatic repair work is active at either frame. Earlier Active pending own-cross delays UP; later OWN/desired/atomic frontier changes remain causal feedback, not a cash gate.',
        proposed_scope='One full15 residual completion with excess backed by current uncancelled UP pending quantity; no general removal of quantity limits.')
    assert abs(out['omitted_total_cash'] - 2.25) < 1e-7
    dump('DIAGNOSIS', out)
    return out


def observation(at, source):
    books = {b['received_ms']: b for b in source['books']}
    plans = {p['t']: p['operations'] for p in at['plans']}
    active_key = at['opportunity_submissions'][0]['key']
    confirmed = min(r['t'] for r in at['demand_owner_rows'] if r['key'] == active_key and r['state'] == 'TERMINAL' and r['filled'] > 0)
    rows = []
    for d in at['demand_rows']:
        t = d['t']; book = books[t]
        assert book['source_ms'] <= t
        r = probe.decide(d['state'], plans[t], d['eligibility']['price'], round(1-book['best_bid'], 10), t >= confirmed, crossing_owners)
        r.update(t=t, seconds=(t - START) / 1000, state=d['state'], original_operations=plans[t], book_source_ms=book['source_ms'])
        rows.append(r)
        if r['eligible']:
            break
    assert rows[-1]['eligible'], 'No causal candidate; do not stage or run unchanged replay'
    out = dict(status='PASS', first=rows[-1], visited_rows=len(rows),
        reason_counts=dict(collections.Counter(r['reason'] for r in rows)),
        prefix_selection='First eligible current OWN/public event after canonical Active terminal fill; no Target events or future OUR outcomes.',
        active_terminal_t=confirmed, raw_prefix=rows)
    dump('OBSERVATION', out)
    return out


def component(first):
    checked = probe.self_test(crossing_owners)
    state = first['state']
    obj = probe.PendingTicketProbe(lambda f, ledger: state)
    active = dict(key='old_active')
    ledger = SimpleNamespace(carriers={'old_active': SimpleNamespace(state='TERMINAL', filled=14.21)})
    producer = SimpleNamespace(opportunity=SimpleNamespace(submissions=[active]),
        demand=SimpleNamespace(rows=[dict(t=first['t'], eligibility=dict(price=first['price']))]), passive_births=0)
    frame = dict(start=START, end=START+300000, t=first['t'], ledger=ledger, gateway_state_id='COMPONENT_ONLY',
        own_view=dict(n=10000), quotes=dict(DOWN=dict(ask=first['ask'])),
        world_profile=dict(asset='BTC', quantity_step=.01, tick=.01, max_live_owners=10000))
    calls = []
    def validate(asset, route, price, qty, quantity_step):
        assert asset == 'BTC' and route == 'PASSIVE' and qty == 15 and price*qty >= 1
        calls.append((price, qty))
    operations = first['original_operations']
    before = json.dumps(operations, sort_keys=True)
    result = obj.apply(frame, producer, operations, validate, crossing_owners)
    assert result[:-1] == operations and result[-1]['qty'] == 15
    assert json.dumps(operations, sort_keys=True) == before
    assert obj.apply(frame, producer, operations, validate, crossing_owners) == operations
    assert len(obj.submissions) == producer.passive_births == len(calls) == 1
    checked.update(one_shot=True, original_operations_unchanged=True, current_candidate_validation=True, local_native=0)
    dump('COMPONENT', checked)


def compile_source(package):
    runner = load('pending_exact_transform_check', package/'money_runner.py')
    source = inspect.getsource(runner.main)
    stop = "    assert socket.gethostname().upper() == 'DESKTOP-JIERAGF', 'worker only'"
    assert source.count(stop) == 1
    # Execute exactly the actual runner's preparation prefix, ending before all
    # worker, native import, process, and main-replay operations.
    prefix = source[:source.index(stop)] + '    return source\n'
    assert 'import hftbacktest' not in prefix and "namespace['main']()" not in prefix
    namespace = dict(runner.__dict__)
    exec(compile(prefix, 'ACTUAL_TRANSFORM_PREFIX_NO_NATIVE', 'exec'), namespace)
    previous = sys.argv
    try:
        sys.argv = ['component', '--mode', 'ORACLE_UP', '--money-mode', 'PARALLEL_QUANTITY',
                    '--demand-mode', 'AUTO_REPAIR', '--retention', '0', '--opportunity-mode', 'ONE_ACTIVE']
        transformed = namespace['main']()
    finally:
        sys.argv = previous
    compile(transformed, 'ACTUAL_TRANSFORM_COMPILE_ONLY', 'exec')
    return transformed


def write_wave():
    wave = dict(progress_artifact='data/research/PENDING_TICKET_PROGRESS_20260913.json', jobs=[dict(
        job_id=JOB, argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
        '--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR',
        '--retention','0','--opportunity-mode','ONE_ACTIVE'], cwd='.',max_threads=4,min_free_ram_gb=6,
        max_start_cpu_pct=90,required_artifact='result.json')])
    (R/'pending_ticket_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')


def main():
    assert not (PACKAGE/'manifest.json').exists(), 'Frozen experiment exists; inspect status, never rebuild or resubmit'
    parent = read(PARENT/'manifest.json')
    assert all(sha(PARENT/n) == h for n, h in parent['files'].items())
    a, at = get(BASE); c, ct = get(CONTROL)
    assert a['cash_budget_enabled'] is False
    diagnosis = diagnose(c, ct, a, at)
    source_path = ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(source_path) == parent['oracle_source_sha256']
    obs = observation(at, read(source_path)); component(obs['first'])
    runner = (PARENT/'money_runner.py').read_text(encoding='utf-8')
    runner = once(runner, '    source=opportunity.instrument(source,replace)',
                  "    source=opportunity.instrument(source,replace)\n    pending_ticket=load('pending_ticket_probe',package/'pending_ticket.py')\n    source=pending_ticket.instrument(source,replace)")
    runner = once(runner, '_ActiveOpportunity=opportunity.ActiveOpportunity,',
                  '_ActiveOpportunity=opportunity.ActiveOpportunity, _PendingTicketProbe=pending_ticket.PendingTicketProbe,')
    runner = once(runner, 'opportunity_rows=producer.opportunity.rows,',
                  'pending_ticket_rows=producer.pending_ticket.rows,pending_ticket_first=producer.pending_ticket.first,pending_ticket_submissions=producer.pending_ticket.submissions,opportunity_rows=producer.opportunity.rows,')
    ast.parse(runner)
    PACKAGE.mkdir(exist_ok=True)
    for name in parent['files']:
        target = PACKAGE/name
        assert not target.exists(), name
        if name == 'money_runner.py':
            target.write_text(runner, encoding='utf-8')
        else:
            shutil.copy2(PARENT/name, target)
    shutil.copy2(ROOT/'tools/btc5m_pending_ticket_probe_v1.py', PACKAGE/'pending_ticket.py')
    manifest = dict(parent)
    manifest.update(version=STEM, parent_manifest_sha256=sha(PARENT/'manifest.json'),
        existing_control=str(BASE.relative_to(ROOT)), baseline_result_sha256=sha(BASE/'result.json'),
        baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'), maximum_native_jobs=1, model_fits=0,
        observation_sha256=sha(R/(STEM+'_OBSERVATION.json')), diagnostic_sha256=sha(R/(STEM+'_DIAGNOSIS.json')),
        selection='First causal post-Active terminal event with negative DOWN, sub15 positive residual, legal15 Passive, no existing DOWN NEW, and current uncancelled UP pending covers excess; append once.',
        unchanged='Same V20 Active; cash gates OFF; theta/quotes/scheduler/maintenance/native/sizing unchanged. Existing operations keep priority; cancellations remain reserved.',
        bounded_scope='One new Passive15 authority may exceed filled-only residual by <15; conditional coverage uses current pending UP, not future filled shares. This is not a claim that pending will fill.',
        evaluation=['Full pre-bridge prefix equality vs existing V20', 'One appended legal15 and same Active14.21',
                    'Actual receipts and lifecycle, direct vs later replacement, conditional payoff both branches',
                    'No blanket repair-to-zero, no success labels based on winner, no new fixed cash gate'],
        files={p.name:sha(p) for p in PACKAGE.glob('*.py')})
    (PACKAGE/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    dump('PREREGISTERED', manifest)
    compile_source(PACKAGE)
    write_wave()
    print(json.dumps(dict(status='FROZEN', files=len(manifest['files']), job=JOB,
        first={k:obs['first'][k] for k in ('seconds','price','quantity','uncovered_filled_gap','excess_over_filled_gap','usable_pending_up')},
        manifest_sha256=sha(PACKAGE/'manifest.json'), local_native=0)))


if __name__ == '__main__':
    main()
