"""Component-only mixed plan and source-transform checks before native dispatch."""
import ast
from collections import deque
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / 'data/research'
PACKAGE = ROOT / '.lan_worker_v1/fixed15_active_opportunity_2026085_20260913_v1'
FIXTURE = ROOT / '.lan_worker_v1/target_core_cycle_active_v8_fixed15-core-loop-2026085-component-v2'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    # Previously verified private15 component copy, not global module patching.
    sys.path.insert(0, str(FIXTURE))
    from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger
    from tools.minimal_student_open_funding_v1 import OpenFundingProfile
    from tools.pair_core_economic_grant_ledger_v1 import Grant
    from tools.minimal_student_training_world_v2 import TrainingPlanGateway, envelope
    from tools.minimal_student_system_plan_v1 import PlanAction
    from tools.open_funding_native_active_adapter_v1 import validate_mixed_envelope
    from tools.pair_core_asset_route_sizing_v2 import validate_size
    from tools.hft244_pair_route_legality_v1 import crossing_owners
    from btc5m_active_repair_opportunity_v1 import ActiveOpportunity, self_test
    from prepare_btc5m_oracle_repair_panel_v1 import sha
    pins = json.loads((R / 'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json').read_text())['private_file_changes']
    assert all(sha(FIXTURE / 'tools' / n) == v['after'] and sha(ROOT / 'tools' / n) == v['before'] for n, v in pins.items())
    self_test(crossing_owners)
    snapshot = load('mixed_snapshot_component', PACKAGE / 'isolated_gate.py').snapshot
    ledger = FastOpenFundingLedger(OpenFundingProfile(max_live_owners=4096))
    for pid, side in ((1, 'UP'), (2, 'DOWN')):
        ledger.issue(Grant(pid, 'component', side, 0., 0., 0., 'SYNTHETIC_COMPONENT_NOT_TARGET'))
    producer = SimpleNamespace(policy_id='OPPORTUNITY_COMPONENT', continuation_id='COMPONENT', provenance='SYNTHETIC_NO_NATIVE',
                               demand=SimpleNamespace(rows=[dict(t=1000, eligibility=dict(price=.05))]),
                               money_gate=SimpleNamespace(retention=.5))
    gateway = TrainingPlanGateway(ledger, asset='BTC', policy_id=producer.policy_id, capabilities=('PASSIVE', 'ACTIVE'))
    seed_plan = gateway.propose('SYNTHETIC_SEED', [
        PlanAction('NEW', key, parent_id=pid, route='ACTIVE', price=.4, qty=qty)
        for key, pid, qty in (('seed_u', 1, 100.), ('seed_d', 2, 40.))
    ], producer.continuation_id)
    gateway.commit(seed_plan, now_ms=1, market_end_ms=300000)
    for key, qty in (('seed_u', 100.), ('seed_d', 40.)):
        gateway.record_send(key, 'SENT', evidence='COMPONENT_ONLY')
        gateway.receipt(key, filled=qty, payment=qty*.4, terminal=True, evidence='SYNTHETIC_SEED_NO_NATIVE')
    frame = dict(t=1000, start=0, end=300000, index=1, world_profile=asdict(ledger.profile),
                 book=dict(bids={.93: 16.}, asks={.94: 20.}), previous_book=dict(bids={}, asks={}),
                 quotes=dict(DOWN=dict(ask=.07), UP=dict(ask=.94)), snapshots={}, cancellable={},
                 own_view=dict(inv=dict(UP=100., DOWN=40.), cost=56., un=dict(UP=deque(), DOWN=deque()),
                               orders={}, slot_key={}, key_role={}, n=3, max_slots=4096),
                 ledger=deepcopy(gateway.ledger), gateway_state_id=gateway.snapshot_id())
    original = [dict(kind='NEW', key='UP_3', side='UP', parent_id=1, route='PASSIVE', qty=15., price=.92, role='PASSIVE_EXPAND')]
    control = ActiveOpportunity('CONTROL', snapshot)
    assert control.apply(frame, producer, original, validate_size, crossing_owners) == original
    active = ActiveOpportunity('ONE_ACTIVE', snapshot)
    operations = active.apply(frame, producer, original, validate_size, crossing_owners)
    assert control.first == active.first
    assert operations[0] == original[0] and operations[1]['qty'] == 16. and operations[1]['route'] == 'ACTIVE'
    assert active.apply(frame, producer, original, validate_size, crossing_owners) == original
    env = envelope(frame, producer, operations)
    validate_mixed_envelope(frame, env, producer.policy_id, producer.continuation_id)
    gateway.commit(env.plan, now_ms=frame['t'], market_end_ms=frame['end'])
    gateway.record_send('DOWN_4', 'SENT', evidence='COMPONENT_ONLY')
    gateway.receipt('DOWN_4', filled=.53, payment=.0371, evidence='SYNTHETIC_PARTIAL_NO_NATIVE')
    gateway.ledger.request_cancel('DOWN_4')
    next_frame = dict(frame, own_view=dict(frame['own_view'], inv=dict(UP=100., DOWN=40.53), cost=56.0371))
    state = snapshot(next_frame, gateway.ledger)
    assert abs(state['pending_qty']['DOWN']-15.47) < 1e-8
    assert abs(state['pending_cash']['DOWN']-15.47*.07) < 1e-8
    assert gateway.ledger.carriers['DOWN_4'].state == 'CANCEL_PENDING'
    assert state['pending_qty']['UP'] == 15. and state['pending_cash']['UP'] == 13.8

    # Run only source preparation statements, never the worker/native body.
    manifest = json.loads((PACKAGE / 'manifest.json').read_text())
    assert all(sha(PACKAGE / n) == h for n, h in manifest['files'].items())
    tree = ast.parse((PACKAGE / 'money_runner.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    start = next(i for i, n in enumerate(fn.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'gate' for t in n.targets))
    end = next(i for i, n in enumerate(fn.body) if isinstance(n, ast.Assert) and 'socket.gethostname' in ast.unparse(n))
    ns = dict(load=load, package=PACKAGE, manifest=manifest)
    exec(compile(ast.Module(body=fn.body[start:end], type_ignores=[]), 'transform_only', 'exec'), ns)
    transformed = ns['source']
    assert transformed.count('ops=producer.opportunity.apply(') == 1
    assert 'active_matches_opportunity=' in transformed
    assert 'theta[7]=math.log(15.)' in transformed
    out = dict(status='PASS', native_jobs=0, model_fits=0, shared_files_unchanged=True,
               variable_active_qty=16., passive_qty=15.,
               checks=['first-event control parity', 'one Active only', 'existing Passive addition preserved',
                       'mixed-envelope and gateway acceptance', 'partial Active receipt below1 preserved',
                       'both-route pending cash', 'cancel intent retains reservation', 'same-plan cross rejected',
                       'notional1 condition', 'quantity/cash/payoff/depth bounds', 'full source transformation'],
               manifest_sha256=sha(PACKAGE / 'manifest.json'))
    (R / 'BTC5M_ACTIVE_REPAIR_OPPORTUNITY_V1_20260913_COMPONENT.json').write_text(json.dumps(out, indent=2)+'\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
