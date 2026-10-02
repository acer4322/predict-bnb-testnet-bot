"""Freeze one new no-cash-gate Active fork; reuse the completed V18 control."""
import ast
import bisect
import collections
import importlib.util
import json
import math
import shutil
import sys

from prepare_btc5m_oracle_repair_panel_v1 import ROOT, sha, once
from audit_btc5m_target_core_loop_topology_v1 import read

R = ROOT/'data/research'
PARENT = ROOT/'.lan_worker_v1/fixed15_active_opportunity_2026085_20260913_v1'
PACKAGE = ROOT/'.lan_worker_v1/fixed15_unbudgeted_opportunity_2026085_20260913_v1'
STEM = 'BTC5M_UNBUDGETED_OPPORTUNITY_V1_20260913'
BASE = R/'lan_worker_returns/fixed15-core-loop-2026085-control-20260913-v2'
JOB = 'fixed15-core-loop-2026085-unbudgeted-active-20260913-v1'


def load(name, path):
    sys.dont_write_bytecode=True
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def active_source():
    source=(PARENT/'active_opportunity.py').read_text(encoding='utf-8')
    source=once(source, 'room = max(0., (1-retention) * gross - paid[\'DOWN\'] - pending_cash[\'DOWN\'])',
                "assert retention == 0., 'This frozen fork has cash budgeting explicitly OFF'\n    room = None")
    source=once(source,'available_cash=room, pending_qty=', 'available_cash=room, cash_budget_enabled=False, pending_qty=')
    source=once(source,'raw = min(quantity_cap, payoff_cap, room / ask, depth)',
                'raw = min(quantity_cap, payoff_cap, depth)')
    source=once(source,"reason='FIRST_LEGAL_FUNDED_ACTIVE_OPPORTUNITY'", "reason='FIRST_LEGAL_ACTIVE_OPPORTUNITY_NO_CASH_GATE'")
    source=source[:source.index('\ndef self_test(crossing):')]+'''
def self_test(crossing):
    state = dict(inv=dict(UP=100., DOWN=40.), payoff=dict(UP=-10., DOWN=-70.),
                 pending_qty=dict(UP=0., DOWN=0.), pending_cash=dict(UP=0., DOWN=0.), owners=[])
    # Both branches negative, DOWN spending exceeds all UP acquisition gross.
    # An implicit 100% gross cap would wrongly reject this owned repair.
    paid = dict(UP=80., DOWN=30.)
    r = decide(state, paid, .05, .07, 20., [], .01, 0., crossing)
    assert r['eligible'] and r['quantity']==20. and r['available_cash'] is None
    assert not r['cash_budget_enabled'] and r['quoted_cost']>0
    pending = dict(state, owners=[dict(key='old',side='UP',limit=.94)],
                   pending_qty=dict(UP=15.,DOWN=0.),pending_cash=dict(UP=14.1,DOWN=0.))
    blocked = decide(pending,paid,.05,.07,20.,[dict(kind='CANCEL',key='old')],.01,0.,crossing)
    assert not blocked['eligible'] and blocked['conflicts']==['old']
    new=[dict(kind='NEW',key='new',side='UP',price=.94,qty=15.)]
    assert not decide(state,paid,.05,.07,20.,new,.01,0.,crossing)['eligible']
    assert not decide(state,paid,.07,.08,20.,[],.01,0.,crossing)['eligible']
    assert not decide(state,paid,.05,.07,10.,[],.01,0.,crossing)['eligible']
'''
    return source


def budget_source():
    source=(PARENT/'cash_budget.py').read_text(encoding='utf-8')
    a=source.index('    gross=');b=source.index('\n\ndef make_capacity')
    source=source[:a]+'''    assert retention == 0., 'Cash gate OFF, not 100% gross allowance'
    return dict(confirmed_up_gross=inv['UP']-paid['UP'],paid=dict(paid),pending_down_cash=pending_down_cash,
                retention=0.,available_cash=None,quantity_cap=None,cash_budget_enabled=False)
'''+source[b:]
    source=once(source,'assert retention in (0.,.5)','assert retention == 0.')
    source=once(source,"admitted=min(original,info['quantity_cap']) if self.retention else original",'admitted=original')
    source=source[:source.index('\ndef self_test():')]+'''
def self_test():
    row=budget(dict(UP=100.,DOWN=40.),dict(UP=80.,DOWN=30.),50.,.07,0.,.01)
    assert row['quantity_cap'] is None and row['available_cash'] is None and not row['cash_budget_enabled']
'''
    return source


def observations():
    active=load('unbudgeted_observation',PACKAGE/'active_opportunity.py')
    sys.path.insert(0,str(ROOT))
    from tools.hft244_pair_route_legality_v1 import crossing_owners
    active.self_test(crossing_owners)
    source_path=ROOT/'.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz'
    source=read(source_path);result=read(BASE/'result.json');trace=read(BASE/'clock_trace.json.gz')
    assert source_path.is_file() and sha(source_path)==read(PARENT/'manifest.json')['oracle_source_sha256']
    books={b['received_ms']:b for b in source['books']};plans={p['t']:p['operations'] for p in trace['plans']}
    carriers={c['key']:c for c in trace['demand_final']['all_final_carriers']}
    paid=dict(UP=0.,DOWN=0.);history=[(0,dict(paid))]
    for event in result['atomic_responsibility_events']:
        for fill in event['fill_rows']: paid[fill['side']]+=fill['fill_increment']*carriers[fill['key']]['limit']
        history.append((event['t'],dict(paid)))
    times=[x[0] for x in history];rows=[]
    for demand in trace['demand_rows']:
        t=demand['t'];book=books[t];s=demand['state'];p=history[bisect.bisect_right(times,t)-1][1]
        assert book['source_ms']<=t and abs(sum(p.values())-s['cost'])<1e-7
        row=active.decide(s,p,demand['eligibility']['price'],round(1-book['best_bid'],10),
                          dict(book['bids'])[book['best_bid']],plans[t],.01,0.,crossing_owners)
        row.update(t=t,seconds=(t-source['market']['window_start_ms'])/1000,state=s,
                   original_operations=plans[t],book_source_ms=book['source_ms'])
        rows.append(row)
    eligible=[r for r in rows if r['eligible']]
    assert eligible, 'No opportunity: do not dispatch an unchanged redundant replay'
    out=dict(status='PASS',source_sha256=sha(source_path),baseline_result_sha256=sha(BASE/'result.json'),
             baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'),first=eligible[0],
             reason_counts=dict(collections.Counter(r['reason'] for r in rows)),candidate_frames=len(eligible),
             target_actions_used=False,native_jobs=0,selection='First current OWN/public eligible frame; no later outcome selection')
    (R/(STEM+'_OBSERVATION.json')).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    return out


def main():
    parent=read(PARENT/'manifest.json')
    assert all(sha(PARENT/n)==h for n,h in parent['files'].items())
    assert not (PACKAGE/'manifest.json').exists(), 'Frozen package already exists; inspect, never rebuild'
    runner=(PARENT/'money_runner.py').read_text(encoding='utf-8')
    runner=once(runner,'choices=(0.,.5),required=True','choices=(0.,),required=True')
    runner=once(runner,'and args.retention==.5','and args.retention==0.')
    runner=once(runner,'profit_retention=args.retention, opportunity_mode=',
                'profit_retention=args.retention, cash_budget_enabled=False, opportunity_mode=')
    runner=once(runner,"oracle=True,target_direction_input=True,runtime_eligible=False,lookahead_condition=",
                "cash_budget_enabled=False,oracle=True,target_direction_input=True,runtime_eligible=False,lookahead_condition=")
    overrides=dict(money_runner=runner,active_opportunity=active_source(),cash_budget=budget_source())
    for source in overrides.values():ast.parse(source)
    PACKAGE.mkdir(exist_ok=True)
    for name in parent['files']:
        if (PACKAGE/name).exists():
            if name[:-3] in overrides:assert (PACKAGE/name).read_text(encoding='utf-8')==overrides[name[:-3]]
            else:assert sha(PACKAGE/name)==parent['files'][name]
        elif name[:-3] in overrides:(PACKAGE/name).write_text(overrides[name[:-3]],encoding='utf-8')
        else:shutil.copy2(PARENT/name,PACKAGE/name)
    observed=observations()
    manifest={k:parent[k] for k in ('backend','native_sha256','remote_inputs','fixed_train_total_frames',
                                   'oracle_source_sha256','selection','demand_selection','condition')}
    manifest.update(version=STEM,parent_manifest_sha256=sha(PARENT/'manifest.json'),market=2026085,
        cash_budget_enabled=False,active_cash_cap=None,passive_cash_cap=None,capital_cap=None,
        oracle_direction='UP',runtime_eligible=False,max_threads=4,maximum_native_jobs=1,model_fits=0,
        existing_control=str(BASE.relative_to(ROOT)),baseline_result_sha256=sha(BASE/'result.json'),
        baseline_trace_sha256=sha(BASE/'clock_trace.json.gz'),observation_sha256=sha(R/(STEM+'_OBSERVATION.json')),
        selection='First causal opportunity; only ONE_ACTIVE new native run. Reuse completed V18 no-budget control.',
        active_quantity='floor_to_step(min(uncovered_DOWN_qty, projected_zero_payoff_capacity, current_visible_top_depth))',
        unchanged='Passive15; quote/scheduler/maintenance/theta; Active minimum1; one Active; current source and same-plan ownership.',
        bounded_scope='Single opportunity capacity retains prior zero-payoff bound; it is not an identified Target stopping target. Later Passive can make both branches positive.',
        user_hypothesis='Directional additions deliberately assume exposure while repairs run concurrently. Temporary loss is allowed; small losses and occasional both-positive outcomes are hypotheses, not required success labels.',
        evaluation=['Exact original policy/source difference audit; component no-op and no hidden100% cash gate.',
                    'All pre-opportunity native/state/intent/plans match existing no-budget control; no replay of future OUR actions.',
                    'Actual receipt accounting, terminal owners, both-route reservations and native hashes.',
                    'Direct repair vs later add/repair changes; peak subsequent response and entire-path both-positive duration.',
                    'No classification threshold for small/big losses; no cross-market success frequency from one consumed case.'],
        files={p.name:sha(p) for p in PACKAGE.glob('*.py')})
    for p in (PACKAGE/'manifest.json',R/(STEM+'_PREREGISTERED.json')):p.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    wave=dict(progress_artifact='data/research/UNBUDGETED_OPPORTUNITY_PROGRESS_20260913.json',jobs=[dict(
        job_id=JOB,argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
                        '--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY','--demand-mode','AUTO_REPAIR',
                        '--retention','0','--opportunity-mode','ONE_ACTIVE'],cwd='.',max_threads=4,min_free_ram_gb=6,
        max_start_cpu_pct=90,required_artifact='result.json')])
    (R/'unbudgeted_opportunity_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='FROZEN',files=len(manifest['files']),first={k:observed['first'][k] for k in ('seconds','quantity','active_ask','quoted_cost','available_cash')},manifest_sha256=sha(PACKAGE/'manifest.json'))))


if __name__=='__main__':main()
