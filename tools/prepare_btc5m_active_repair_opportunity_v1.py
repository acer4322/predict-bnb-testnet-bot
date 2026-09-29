"""Observe the first causal opportunity, then freeze control/one-Active arms."""
import ast
import bisect
from collections import Counter
import json
import math
import shutil
import sys

from prepare_btc5m_oracle_repair_panel_v1 import ROOT, sha, once
from audit_btc5m_target_core_loop_topology_v1 import read
from btc5m_active_repair_opportunity_v1 import decide, self_test

sys.path.insert(0, str(ROOT))
from tools.hft244_pair_route_legality_v1 import crossing_owners

R = ROOT / 'data/research'
PARENT = ROOT / '.lan_worker_v1/fixed15_core_loop_2026085_20260913_v2'
PACKAGE = ROOT / '.lan_worker_v1/fixed15_active_opportunity_2026085_20260913_v1'
STEM = 'BTC5M_ACTIVE_REPAIR_OPPORTUNITY_V1_20260913'
OLD = R / 'lan_worker_returns/fixed15-core-loop-2026085-half-20260913-v2'


def observations():
    source_path = ROOT / '.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    assert sha(source_path) == 'd2ce3d621241f0de94aeb888cb2d059345e3dc0afd677d0d1b70547f798c578b'
    source = read(source_path)
    trace = read(OLD / 'clock_trace.json.gz')
    result = read(OLD / 'result.json')
    books = {b['received_ms']: b for b in source['books']}
    assert len(books) == len(source['books'])
    plans = {p['t']: p['operations'] for p in trace['plans']}
    carriers = {c['key']: c for c in trace['demand_final']['all_final_carriers']}
    paid = dict(UP=0., DOWN=0.)
    paid_rows = [(0, dict(paid))]
    for event in result['atomic_responsibility_events']:
        for fill in event['fill_rows']:
            paid[fill['side']] += fill['fill_increment'] * carriers[fill['key']]['limit']
        paid_rows.append((event['t'], dict(paid)))
    paid_times = [p[0] for p in paid_rows]
    rows = []
    for demand in trace['demand_rows']:
        t = demand['t']
        state = demand['state']
        booked = paid_rows[bisect.bisect_right(paid_times, t)-1][1]
        assert abs(sum(booked.values()) - state['cost']) < 1e-7
        book = books[t]
        assert book['source_ms'] <= t
        ask = round(1-book['best_bid'], 10)
        row = decide(state, booked, demand['eligibility']['price'], ask, book['bids'][0][1],
                     plans[t], .01, .5, crossing_owners)
        row.update(t=t, seconds=(t-source['market']['window_start_ms']) / 1000.,
                   state=state, original_operations=plans[t], book_source_ms=book['source_ms'], book_received_ms=t)
        rows.append(row)
    eligible = [r for r in rows if r['eligible']]
    assert eligible
    out = dict(status='COMPLETE', source_sha256=sha(source_path), trace_sha256=sha(OLD / 'clock_trace.json.gz'),
               result_sha256=sha(OLD / 'result.json'), selection='Earliest eligible event, no later-outcome filter',
               reasons=dict(Counter(r['reason'] for r in rows)), first=eligible[0], candidates=eligible,
               target_actions_used=False, native_jobs=0, model_fits=0,
               active_min_notional='One dollar NEW condition retained for this diagnostic; not Passive fixed sizing',
               limits=['Displayed depth is not a fill guarantee.', 'No Target timestamps or quantities select this event.',
                       'Whole-episode candidate count is descriptive, not independent trials or a policy replay.'])
    (R / (STEM + '_OBSERVATION.json')).write_text(json.dumps(out, indent=2) + '\n', encoding='utf-8')
    return out


def main():
    self_test(crossing_owners)
    observed = observations()
    parent = read(PARENT / 'manifest.json')
    assert all(sha(PARENT / n) == h for n, h in parent['files'].items())
    runner = (PARENT / 'money_runner.py').read_text(encoding='utf-8')
    runner = once(runner, "ap.add_argument('--repair-route'", "ap.add_argument('--opportunity-mode',choices=('CONTROL','ONE_ACTIVE'),required=True)\n    ap.add_argument('--repair-route'")
    runner = once(runner, "source=ticket.instrument(source,replace)", "source=ticket.instrument(source,replace)\n    opportunity=load('active_opportunity',package/'active_opportunity.py')\n    source=opportunity.instrument(source,replace)")
    runner = once(runner, "_MODE=args.mode, _install_ticket_condition=", "_MODE=args.mode, _OPPORTUNITY_MODE=args.opportunity_mode, _ActiveOpportunity=opportunity.ActiveOpportunity, _install_ticket_condition=")
    runner = once(runner, "profit_budget_rows=producer.money_gate.budget_rows)", "profit_budget_rows=producer.money_gate.budget_rows,opportunity_rows=producer.opportunity.rows,opportunity_first=producer.opportunity.first,opportunity_submissions=producer.opportunity.submissions)")
    runner = once(runner, "profit_retention=args.retention, demand_visited=", "profit_retention=args.retention, opportunity_mode=args.opportunity_mode, demand_visited=")
    runner = once(runner, "assert args.mode=='ORACLE_UP' and args.repair_route=='NONE'", "assert args.mode=='ORACLE_UP' and args.repair_route=='NONE' and args.retention==.5 and args.demand_mode=='AUTO_REPAIR'")
    isolated = (PARENT / 'isolated_gate.py').read_text(encoding='utf-8')
    isolated = once(isolated, "assert c.route=='PASSIVE' and c.fee_cap==0., 'panel requires the frozen zero-fee passive reservation contract'",
                    "assert c.route in ('PASSIVE','ACTIVE') and c.fee_cap==0., 'panel requires the frozen zero-fee mixed-route reservation contract'")
    ast.parse(runner)
    ast.parse(isolated)
    assert not PACKAGE.exists(), 'immutable package already exists'
    PACKAGE.mkdir()
    for name in parent['files']:
        if name not in ('money_runner.py', 'isolated_gate.py'):
            shutil.copy2(PARENT / name, PACKAGE / name)
    (PACKAGE / 'money_runner.py').write_text(runner, encoding='utf-8')
    (PACKAGE / 'isolated_gate.py').write_text(isolated, encoding='utf-8')
    shutil.copy2(ROOT / 'tools/btc5m_active_repair_opportunity_v1.py', PACKAGE / 'active_opportunity.py')
    manifest = {k: parent[k] for k in ('backend', 'native_sha256', 'remote_inputs', 'fixed_train_total_frames',
                                      'oracle_source_sha256', 'selection', 'demand_selection', 'condition')}
    manifest.update(version=STEM, parent_manifest_sha256=sha(PARENT / 'manifest.json'), market=2026085,
        oracle_direction='UP', runtime_eligible=False, max_threads=4, sequential_jobs=1, maximum_native_jobs=2,
        model_fits=0, parameter_search=False, observation_sha256=sha(R / (STEM + '_OBSERVATION.json')),
        runtime_selector='First current own-state unmet DOWN repair where unchanged Passive15 quote costs<1, variable Active amount costs>=1, within existing half-profit budget, uncovered qty, zero-payoff capacity, observed top-level depth, and existing plus same-plan self-cross/owner limits.',
        active_quantity='floor_to_step(min(uncovered_down_qty, projected_zero_payoff_capacity, available_cash/active_ask, visible_top_depth))',
        modes=dict(control='Observe only; complete V18 half parity required before Active arm.', active='Exactly one first-eligible Active order; remaining episode follows unchanged controller.'),
        paired_condition='Both arms Passive15, existing half-profit budget, oracle UP; no fixed Active size.',
        frozen=['All theta including15, quote calculation, passive maintenance, existing work/rearm, public source, native zero-fee model.',
                'No Target action time/size, terminal-source fallback, parameter sweep, or forced completion.',
                'Current and same-plan pending claims include CANCEL_PENDING, never infer release from cancel intent.'],
        evaluation=['Full disabled parity with V18 half, first-eligible witness matches observation without being input as trigger.',
                    'Identical state/intent/native prefix before the single Active operation.',
                    'Native maker=0 receipt, variable requested qty, partial/zero-fill/terminal, complete owner/payment audit.',
                    'Budget includes both routes and all draft reservations; Passive NEW remains exactly15.',
                    'Direct Active payoff contribution separately from subsequent policy trajectory effects.',
                    'This one-opportunity experiment cannot establish a recurrent route policy.'],
        files={p.name: sha(p) for p in PACKAGE.iterdir()})
    for path in (PACKAGE / 'manifest.json', R / (STEM + '_PREREGISTERED.json')):
        path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    for tag, mode in (('control', 'CONTROL'), ('active', 'ONE_ACTIVE')):
        wave = dict(progress_artifact=f'data/research/ACTIVE_REPAIR_OPPORTUNITY_{tag.upper()}_PROGRESS_20260913.json', jobs=[dict(
            job_id=f'fixed15-core-loop-2026085-opportunity-{tag}-20260913-v1',
            argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
                  '--mode', 'ORACLE_UP', '--money-mode', 'PARALLEL_QUANTITY', '--demand-mode', 'AUTO_REPAIR',
                  '--retention', '0.5', '--opportunity-mode', mode], cwd='.', max_threads=4,
            min_free_ram_gb=6, max_start_cpu_pct=90, required_artifact='result.json')])
        (R / f'active_repair_opportunity_{tag}_wave_20260913.json').write_text(json.dumps(wave, indent=2) + '\n')
    print(json.dumps(dict(status='FROZEN', files=len(manifest['files']), manifest_sha256=sha(PACKAGE / 'manifest.json'),
                         first={k: observed['first'][k] for k in ('t', 'seconds', 'active_ask', 'quantity', 'available_cash', 'quoted_cost')})))


if __name__ == '__main__':
    main()
