"""Freeze one pulse-reproduction control and one persistent-goal native arm."""
import ast
import json
import shutil
from prepare_btc5m_oracle_repair_panel_v1 import ROOT, sha, once
from btc5m_persistent_repair_demand_v1 import self_test

R = ROOT / 'data/research'
PARENT = ROOT / '.lan_worker_v1/single_repair_demand_2026085_20260913_v1'
PACKAGE = ROOT / '.lan_worker_v1/persistent_repair_demand_2026085_20260913_v1'
STEM = 'BTC5M_PERSISTENT_REPAIR_DEMAND_V1_20260913'


def main():
    self_test()
    prior = json.loads((PARENT / 'manifest.json').read_text(encoding='utf-8'))
    assert all(sha(PARENT / n)==h for n,h in prior['files'].items())
    source = (PARENT/'money_runner.py').read_text(encoding='utf-8')
    source = once(source, "choices=('CONTROL','PULSE30')", "choices=('PULSE_CONTROL','PERSIST30')")
    source = once(source, 'demand_events=producer.demand.events)',
                  'demand_events=producer.demand.events, demand_rows=producer.demand.rows, '
                  'demand_owner_rows=producer.demand.owner_rows, demand_plan_rows=producer.demand.plan_rows, '
                  'demand_maintenance_rows=producer.demand.maintenance_rows, demand_final=producer.demand.final)')
    source = once(source, 'first_direction_birth=producer.intent.birth, controller_rows=',
                  "final_pending_cash_direct={s:a['reserved_cash'] for s,a in producer.demand.final['final_accounts'].items()}, "
                  'first_direction_birth=producer.intent.birth, controller_rows=')
    ast.parse(source)
    assert not PACKAGE.exists(), 'immutable package exists'
    PACKAGE.mkdir()
    for name in prior['files']:
        if name not in ('money_runner.py','demand_gate.py'):
            shutil.copy2(PARENT/name,PACKAGE/name)
    shutil.copy2(PARENT/'demand_gate.py',PACKAGE/'pulse_gate.py')
    shutil.copy2(ROOT/'tools/btc5m_persistent_repair_demand_v1.py',PACKAGE/'demand_gate.py')
    (PACKAGE/'money_runner.py').write_text(source,encoding='utf-8')
    m = {k:prior[k] for k in ('backend','native_sha256','remote_inputs','fixed_train_total_frames','selection','demand_selection','checkpoint_sha256')}
    initial = m['demand_selection']['state']
    goal = initial['inv']['DOWN']+initial['pending_qty']['DOWN']+30.
    m.update(version=STEM, market=2026085, oracle=True, runtime_eligible=False, max_threads=4,
        sequential_jobs=1, maximum_native_jobs=2, parent_manifest_sha256=sha(PARENT/'manifest.json'),
        modes=['PULSE_CONTROL','PERSIST30'], no_parameter_search=True, model_fits=0,
        question='Does a finite inventory goal maintained consistently in desired and maintenance prevent V14 immediate surplus cancellation and achieve received repair progress?',
        change='Same one30 upstream request at the fixed OWN checkpoint. Persist an absolute DOWN inventory floor427.58 while the finite goal remains active; effective desired.DOWN=max(original desired.DOWN,floor). No goal ratcheting or blanket KEEP. Original UP demand, public quotes, stale-price maintenance, sizing, capacity cap and gateway remain.',
        work=dict(initial_down=initial['inv']['DOWN'],initial_committed_down=initial['pending_qty']['DOWN'],
                  additional_request=30.,absolute_goal=goal,remaining_confirmed_at_birth=goal-initial['inv']['DOWN'],
                  accounting='84.71 shares includes already-pending54.71 plus new30, not84.71 new authorization. All confirmed DOWN acquisitions can fulfill the fungible inventory goal; pending occupies demand but is not success.',
                  stops=['Confirmed DOWN inventory reaches the absolute goal.', 'Actual market end.',
                         'Current UP inventory is no longer greater than DOWN.', 'Current DOWN conditional payoff is nonnegative.'],
                  stop_scope='Stop only the experimental floor; original controller and original market-end cancellation remain. Nonnegative is a reused diagnostic withdrawal, not a learned Target threshold.'),
        control_gate='PULSE_CONTROL must reproduce V14 pulse17 economic fields,4 primary streams,intent,money_rows and demand_events exactly before PERSIST30 submit.',
        prefix_gate='Match both arms through the20.779s cut, including the same native NEW DOWN_77 qty30 @.31 and the same initial OWN state; later divergence must be attributable to persistent desired.',
        evaluation=['Original selected order and existing two orders: actual native submit, received partial/full/zero fills, cancellation predicates, final carriers and canonical receipts.',
                    'Goal state versus actual inventory, all pending including CANCEL_PENDING, released capacity, no upward dust rounding, no changing absolute goal.',
                    'Whole remaining path: both conditional payoffs, loss area, worst floor, direction and activity; do not interpret one completed order as a learned full loop.',
                    'Preserve full failures, exact-job uniqueness, native accounting flags, terminal ownership and directly serialized final pending accounts.'],
        minimum_scope='OUR BTC Passive research contract qty>=18 AND notional>=1 for NEW only; partial receipts/leaves may be smaller. Active remains unused in this panel.',
        limits=['One consumed market, own hindsight diagnostic checkpoint and offline final-direction bit; not deployable or generalization evidence.',
                'Existing baseline demand may exceed the floor, so total subsequent DOWN volume can exceed84.71. Distinguish baseline allocation from this finite goal.',
                'This is not an actual net-overshoot test or new Active allocator. Fixed quantity cap remains.',
                'A remaining unreserved need below the existing minimum can still be untradeable. Do not silently enlarge the experimental goal.'],
        files={p.name:sha(p) for p in PACKAGE.iterdir()})
    for p in (PACKAGE/'manifest.json',R/(STEM+'_PREREGISTERED.json')):
        p.write_text(json.dumps(m,indent=2)+'\n',encoding='utf-8')
    for tag,mode in (('control','PULSE_CONTROL'),('persist','PERSIST30')):
        wave = dict(progress_artifact=f'data/research/PERSISTENT_REPAIR_DEMAND_{tag.upper()}_PROGRESS_20260913.json',jobs=[dict(
            job_id=f'persistent-repair-demand-2026085-{tag}-20260913-v1',
            argv=['.venv/Scripts/python.exe',f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
                  '--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY','--demand-mode',mode],
            cwd='.',max_threads=4,min_free_ram_gb=6,max_start_cpu_pct=90,required_artifact='result.json')])
        (R/f'persistent_repair_demand_{tag}_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PREPARED',files=len(m['files']),goal=goal,modes=m['modes'],native_runs=0)))


if __name__ == '__main__':
    main()
