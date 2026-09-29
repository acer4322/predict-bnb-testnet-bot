"""Freeze two existing mechanisms at the user-confirmed historical ticket15.

Not a ticket or retention search. Both arms share exactly15 Passive NEW orders;
the only between-arm variable is the existing confirmed-profit budget on/off.
"""
import ast
import json
import shutil
import sys
from prepare_btc5m_oracle_repair_panel_v1 import ROOT, sha, once
from btc5m_fixed15_research_condition_v1 import self_test

sys.path.insert(0, str(ROOT))
from tools.pair_core_asset_route_sizing_v2 import validate_size

R = ROOT / 'data/research'
PARENT = ROOT / '.lan_worker_v1/repair_profit_budget_2026085_20260913_v1'
PACKAGE = ROOT / '.lan_worker_v1/fixed15_core_loop_2026085_20260913_v1'
STEM = 'BTC5M_FIXED15_CORE_LOOP_V1_20260913'


def main():
    self_test(validate_size)
    prior = json.loads((PARENT / 'manifest.json').read_text(encoding='utf-8'))
    assert all(sha(PARENT / name) == digest for name, digest in prior['files'].items())
    source = (PARENT / 'money_runner.py').read_text(encoding='utf-8')
    marker = 'source=demand.instrument(source,replace)'
    source = once(source, marker, marker + "\n    ticket=load('fixed15_condition',package/'ticket_condition.py')\n    source=ticket.instrument(source,replace)")
    source = once(source, "_MODE=args.mode, _REPAIR_ROUTE=args.repair_route,", "_MODE=args.mode, _ticket_validator_factory=ticket.make_validator, _REPAIR_ROUTE=args.repair_route,")
    source = once(source, "numeric_parameters_frozen=True, repair_route=", "numeric_parameters_frozen=True, passive_ticket=15., sizing_condition=ticket.CONTRACT, repair_route=")
    demand = (PARENT / 'demand_gate.py').read_text(encoding='utf-8')
    demand = once(demand, 'minimum=max(18.,1./price)', 'minimum=max(15.,1./price)')
    ast.parse(source)
    ast.parse(demand)
    assert not PACKAGE.exists(), 'immutable package already exists'
    PACKAGE.mkdir()
    for name in prior['files']:
        if name not in ('money_runner.py', 'demand_gate.py'):
            shutil.copy2(PARENT / name, PACKAGE / name)
    (PACKAGE / 'money_runner.py').write_text(source, encoding='utf-8')
    (PACKAGE / 'demand_gate.py').write_text(demand, encoding='utf-8')
    shutil.copy2(ROOT / 'tools/btc5m_fixed15_research_condition_v1.py', PACKAGE / 'ticket_condition.py')
    manifest = {k: prior[k] for k in ('backend', 'native_sha256', 'remote_inputs', 'fixed_train_total_frames', 'oracle_source_sha256')}
    manifest.update(
        version=STEM, market=2026085, oracle_direction='UP', runtime_eligible=False,
        max_threads=4, sequential_jobs=1, maximum_native_jobs=2, model_fits=0, parameter_search=False,
        # AUTO_REPAIR never consumes historical V15/V17 diagnostic checkpoints.
        selection=dict(t=0, old_id=None, initial_remaining=None, repair_side='DOWN'),
        demand_selection=dict(t=0, state=dict(owners=[])),
        parent_manifest_sha256=sha(PARENT / 'manifest.json'),
        target_size_audit_sha256=sha(R / 'BTC5M_TARGET_FILL_SIZE_AUDIT_V1_20260913.json'),
        user_authorization='Use confirmed15 for this market to learn the core loop; postpone ticket/parameter optimization.',
        condition=dict(passive_new_qty=15., min_notional=1., quantity_step=.01,
                       active='UNCHANGED_NOT_FIXED15', shared_sizing_file_changed=False,
                       partial_receipts='NOT_SUBJECT_TO_NEW_ORDER_TICKET', historical_size_rule='USER_RESEARCH_CONDITION_NOT_EXCHANGE_SPEC'),
        modes=dict(control=dict(demand='AUTO_REPAIR', retention=0.), half=dict(demand='AUTO_REPAIR', retention=.5)),
        frozen=['Oracle UP, public tape, manager state and all other theta values.',
                'Price calculation, event/terminal rearm, owner maintenance, pending reservations, self-cross checks.',
                'Both arms are Passive-only as in V17; this does not test Active trigger/quantity rules.'],
        changed_from_parent=['Passive ticket30 becomes exactly15, rejecting clipped NEW sizes.',
                             'Package-local Passive minimum18 becomes ticket15; notional1 stays.',
                             'Automatic finite repair work uses one15 ticket; no historical checkpoint state.'],
        evaluation=['Verify every actual Passive NEW is15 and price*qty>=1; allow partial receipts below minima.',
                    'Receipt/accounting/owner/transport and event consumption gates; preserve incomplete work.',
                    'Compare same15 budget on/off, both payoffs, retained exposure and post-peak repair.',
                    'Earlier30 results are historical context, not a one-factor causal comparison.',
                    'No extra sweep, Target action timing/quantity replay, native rerun or forced completion.'],
        files={p.name: sha(p) for p in PACKAGE.iterdir()},
    )
    for path in (PACKAGE / 'manifest.json', R / (STEM + '_PREREGISTERED.json')):
        path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    for tag, retention in (('control', 0.), ('half', .5)):
        wave = dict(progress_artifact=f'data/research/FIXED15_CORE_LOOP_{tag.upper()}_PROGRESS_20260913.json', jobs=[dict(
            job_id=f'fixed15-core-loop-2026085-{tag}-20260913-v1',
            argv=['.venv/Scripts/python.exe', f'.lan_worker_v1/staging/{PACKAGE.name}/money_runner.py',
                  '--mode', 'ORACLE_UP', '--money-mode', 'PARALLEL_QUANTITY', '--demand-mode', 'AUTO_REPAIR', '--retention', str(retention)],
            cwd='.', max_threads=4, min_free_ram_gb=6, max_start_cpu_pct=90, required_artifact='result.json')])
        (R / f'fixed15_core_loop_{tag}_wave_20260913.json').write_text(json.dumps(wave, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(status='FROZEN', package=PACKAGE.name, files=len(manifest['files']), manifest_sha256=sha(PACKAGE / 'manifest.json'))))


if __name__ == '__main__':
    main()
