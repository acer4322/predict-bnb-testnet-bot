"""Freeze a two-arm candidate/maintenance experiment, with no local native run."""
import ast
import json
import shutil
from prepare_btc5m_oracle_repair_panel_v1 import ROOT, sha, once
from btc5m_single_repair_demand_v1 import self_test

R = ROOT / 'data/research'
PARENT = ROOT / '.lan_worker_v1/parallel_payoff_2026085_20260913_v1'
PACKAGE = ROOT / '.lan_worker_v1/single_repair_demand_2026085_20260913_v1'


def main():
    self_test()
    prior = json.loads((PARENT / 'manifest.json').read_text(encoding='utf-8'))
    assert all(sha(PARENT / n) == h for n, h in prior['files'].items())
    checkpoint = R / 'BTC5M_CORE_LOOP_CONTROL_GAP_V1_20260913.json'
    cp = json.loads(checkpoint.read_text())['next_static_checkpoint']['first']
    assert cp['t'] == 1788758120779 and cp['proposed'] == 30 and not cp['conflicting_owners']
    source = (PARENT / 'money_runner.py').read_text(encoding='utf-8')
    source = once(source, "    ap.add_argument('--repair-route'", "    ap.add_argument('--demand-mode', choices=('CONTROL','PULSE30'), required=True)\n    ap.add_argument('--repair-route'")
    source = once(source, "    source=gate.instrument(source,replace)", "    source=gate.instrument(source,replace)\n    demand=load('single_repair_demand',package/'demand_gate.py');demand.self_test()\n    source=demand.instrument(source,replace)")
    source = once(source, "_MONEY_SELECTION=manifest['selection'])", "_MONEY_SELECTION=manifest['selection'], _DemandGate=demand.SingleRepairDemand, _DEMAND_MODE=args.demand_mode, _DEMAND_SELECTION=manifest['demand_selection'], _OWN_SNAPSHOT=gate.snapshot)")
    source = once(source, "money_rows=producer.money_gate.rows)", "money_rows=producer.money_gate.rows, demand_events=producer.demand.events)")
    source = once(source, "money_mode=args.money_mode, actual_replay_frames=actual_frames", "money_mode=args.money_mode, demand_mode=args.demand_mode, demand_visited=producer.demand.visited, actual_replay_frames=actual_frames")
    ast.parse(source)
    assert not PACKAGE.exists(), 'immutable package exists'
    PACKAGE.mkdir()
    for name in prior['files']:
        if name != 'money_runner.py':
            shutil.copy2(PARENT / name, PACKAGE / name)
    shutil.copy2(ROOT / 'tools/btc5m_single_repair_demand_v1.py', PACKAGE / 'demand_gate.py')
    (PACKAGE / 'money_runner.py').write_text(source, encoding='utf-8')
    manifest = {k: prior[k] for k in ('backend', 'native_sha256', 'remote_inputs', 'fixed_train_total_frames', 'selection')}
    manifest.update(version='BTC5M_SINGLE_REPAIR_DEMAND_V1_20260913', oracle=True, runtime_eligible=False,
        market=2026085, max_threads=4, sequential_jobs=1, parent_manifest_sha256=sha(PARENT / 'manifest.json'),
        checkpoint_sha256=sha(checkpoint), demand_selection=cp, modes=['CONTROL','PULSE30'],
        question='Does one independently declared upstream repair request at the frozen OWN checkpoint reach a real native fill and survive unchanged subsequent maintenance?',
        change='PULSE30 changes only DOWN desired at the one selected producer frame to current DOWN inventory + still-reserved DOWN + 30. UP desired, parameters, public price, pending/cancel semantics, gateway and subsequent maintenance remain the frozen algorithms.',
        authorization='One explicit offline diagnostic service request of30, requiring negative DOWN payoff and both uncovered quantity and zero-payoff scenario capacities >=30. Not automatic rounding of a0.53 grant. Existing gateway funding and sizing still apply.',
        minimum_scope='OUR research BTC Passive qty>=18 and notional>=1; applies to new orders only, not partial receipts. Target original request unknown.',
        control_gate='CONTROL must exactly match prior V12 quantity17 economic fields and all four primary trace streams before PULSE30 submit.',
        prefix_gate='All four primary streams strictly before1788758120779 must match; checkpoint inventory/cash/reservations/owners must match. Only one pulse visit.',
        evaluation=['Actual NEW at cut, order id, request/filled/remaining, cancel request, terminal and elapsed time.',
                    'Report original versus separately authorized deficit; preserve max_epoch_residual_overfill against effective declared demand and all original accounting gates.',
                    'Compare both conditional payoffs, worst floor, time-integrated negative floor and ongoing two-side activity.',
                    'One-frame demand intentionally has no persistent-goal or maintenance bypass; early cancellation is a diagnostic result, not grounds to erase the failed arm.'],
        limits=['One selected consumed market and OWN hindsight checkpoint, with offline final-direction bit. Mechanism-only, no policy promotion.',
                'This30 request is below203.57 uncovered shares. It does not test actual net overshoot or establish a Target minimum-size policy.',
                'No parameter fitting, search, Target event/time/size inputs, live changes or fresh data.'],
        files={p.name: sha(p) for p in PACKAGE.iterdir()})
    for p in (PACKAGE / 'manifest.json', R / 'BTC5M_SINGLE_REPAIR_DEMAND_V1_20260913_PREREGISTERED.json'):
        p.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    for tag, mode in (('control','CONTROL'), ('pulse','PULSE30')):
        wave = dict(progress_artifact=f'data/research/SINGLE_REPAIR_DEMAND_{tag.upper()}_PROGRESS_20260913.json', jobs=[dict(
            job_id=f'single-repair-demand-2026085-{tag}-20260913-v1',
            argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
                  '--mode','ORACLE_UP','--money-mode','PARALLEL_QUANTITY','--demand-mode',mode],
            cwd='.', max_threads=4, min_free_ram_gb=6, max_start_cpu_pct=90, required_artifact='result.json')])
        (R / f'single_repair_demand_{tag}_wave_20260913.json').write_text(json.dumps(wave,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PREPARED', package=str(PACKAGE), files=len(manifest['files']), native_runs=0)))


if __name__ == '__main__':
    main()
