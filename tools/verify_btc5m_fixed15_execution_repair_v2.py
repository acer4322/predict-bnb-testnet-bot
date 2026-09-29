"""No-native reproduction of the captured first failed15 plan and repaired gates."""
import argparse
from collections import deque
from copy import deepcopy
from dataclasses import asdict
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / 'data/research'
CASE = ROOT / '.lan_worker_v1/target_core_cycle_active_v8_fixed15-core-loop-2026085-component-v2'


def child(condition):
    if condition == 'repaired':
        sys.path.insert(0, str(CASE))
    else:
        sys.path.insert(0, str(ROOT))
    from tools.pair_core_economic_grant_ledger_v1 import Grant, EconomicGrantLedger
    from tools.open_funding_recovery_runtime_v3 import FastOpenFundingLedger
    from tools.minimal_student_open_funding_v1 import OpenFundingProfile
    from tools.minimal_student_training_world_v2 import TrainingPlanGateway, envelope
    from tools.open_funding_native_active_adapter_v1 import validate_mixed_envelope
    from tools.minimal_student_quantity_seam_v1 import QuantityIntent, VenueGrid, prepare_exact
    from tools.pair_core_asset_route_sizing_v2 import validate_size
    failed = R / 'lan_worker_returns/fixed15-core-loop-2026085-control-20260913-v1/failure_trace.json.gz'
    trace = json.loads(gzip.decompress(failed.read_bytes()))
    captured = trace['demand_plan_rows'][0]
    source = json.loads(gzip.decompress((ROOT / '.lan_worker_v1/v20_consumed_btc5_transfer5_20260912_v1/input_2026085.json.gz').read_bytes()))
    book = next(b for b in source['books'] if b['received_ms'] == captured['t'])

    class Producer:
        policy_id = 'CAPTURED_FIRST_PLAN_COMPONENT'
        continuation_id = 'COMPONENT_ONLY'
        provenance = 'CAPTURED_OPERATION_EMPTY_OWN_STATE_SYNTHETIC_FRAME_NO_NATIVE'

    def fixture():
        ledger = FastOpenFundingLedger(OpenFundingProfile(max_live_owners=4096))
        for pid, side in ((1, 'UP'), (2, 'DOWN')):
            ledger.issue(Grant(pid, 'component', side, 0., 0., 0., 'EXPLICIT_COMPONENT'))
        gateway = TrainingPlanGateway(ledger, asset='BTC', policy_id=Producer.policy_id,
                                      capabilities=('PASSIVE', 'ACTIVE'))
        frame = dict(t=captured['t'], start=source['market']['window_start_ms'], end=source['market']['window_end_ms'],
                     index=1, world_profile=asdict(ledger.profile), book=dict(bids=dict(book['bids']), asks=dict(book['asks'])),
                     previous_book=dict(bids={}, asks={}), quotes={}, snapshots={}, cancellable={},
                     own_view=dict(inv=dict(UP=0., DOWN=0.), cost=0., un=dict(UP=deque(), DOWN=deque()),
                                   orders={}, slot_key={}, key_role={}, n=1, max_slots=4096),
                     ledger=deepcopy(ledger), gateway_state_id=gateway.snapshot_id())
        return gateway, frame

    gw, frame = fixture()
    env = envelope(frame, Producer, captured['operations'])
    if condition == 'baseline':
        try:
            validate_mixed_envelope(frame, env, Producer.policy_id, Producer.continuation_id)
        except ValueError as exc:
            assert str(exc) == 'below passive minimum submitted quantity'
            print(json.dumps(dict(status='REPRODUCED', error=str(exc), native_jobs=0)))
            return
        raise AssertionError('captured failure no longer reproduces')
    validate_mixed_envelope(frame, env, Producer.policy_id, Producer.continuation_id)
    gw.commit(env.plan, now_ms=frame['t'], market_end_ms=frame['end'])
    assert gw.ledger.carriers['UP_1'].qty == 15.
    # Partial receipt then cancellation request keeps the remaining reservation.
    gw.record_send('UP_1', 'SENT', evidence='COMPONENT_ONLY_NOT_NATIVE')
    gw.receipt('UP_1', filled=.5, payment=.255, evidence='SYNTHETIC_PARTIAL_NOT_NATIVE')
    gw.ledger.request_cancel('UP_1')
    account = gw.ledger.account(1)
    assert abs(account['reserved_qty'] - 14.5) < 1e-8
    assert abs(account['reserved_cash'] - 14.5 * .51) < 1e-8
    assert gw.ledger.carriers['UP_1'].state == 'CANCEL_PENDING'
    try:
        gw.ledger.reserve('DOWN_2', 2, 'PASSIVE', 15., .5, 0., now_ms=frame['t'], market_end_ms=frame['end'])
    except ValueError as exc:
        assert 'potential own cross including pending' in str(exc)
    else:
        raise AssertionError('pending owner cross incorrectly released')
    for p, q in ((.06, 15.), (.5, 14.99), (.5, 30.), (.01, 100.)):
        try:
            validate_size('BTC', 'PASSIVE', p, q, quantity_step=.01)
        except ValueError:
            pass
        else:
            raise AssertionError((p, q))
    for p, q in ((.02, 59.61), (.5, .53), (.5, 30.)):
        validate_size('BTC', 'ACTIVE', p, q, quantity_step=.01)
    planner = EconomicGrantLedger(100.)
    planner.issue(Grant(1, 'component', 'UP', 0., 100., 50., 'EXPLICIT_COMPONENT'))
    planned = prepare_exact(planner, 'BTC', 'UP', .51, QuantityIntent(1, 15., 'PASSIVE', 'COMPONENT'),
                            key='test', quote_reference='component', now_ms=1, market_end_ms=300000,
                            grid=VenueGrid(.01, .01, .01, 0., 'COMPONENT_NOT_VENUE_SPEC'))
    assert planned.plan is not None and planned.plan.quantity == 15., planned.reason
    assert planned.plan.limits.min_quantity == 15.
    print(json.dumps(dict(status='PASS', captured_operation=captured, native_jobs=0,
                         checks=['mixed-envelope', 'gateway-commit', 'exact-planner', 'fixed15-new',
                                 'minimum-notional1', 'active-variable', 'partial-receipt', 'cancel-pending-reservation', 'own-cross-retained'])))


def main():
    from btc5m_fixed15_execution_condition_v2 import install, PINS
    parser = argparse.ArgumentParser()
    parser.add_argument('--child', choices=('baseline', 'repaired'))
    args = parser.parse_args()
    if args.child:
        child(args.child)
        return
    assert not CASE.exists()
    (CASE / 'tools').mkdir(parents=True)
    for name, digest in PINS.items():
        assert hashlib.sha256((ROOT / 'tools' / name).read_bytes()).hexdigest() == digest
        shutil.copy2(ROOT / 'tools' / name, CASE / 'tools' / name)
    (CASE / 'tools/__init__.py').write_text('__path__.append(' + repr(str(ROOT / 'tools')) + ')\n')
    changes = install(CASE)
    results = {}
    for condition in ('baseline', 'repaired'):
        cp = subprocess.run([sys.executable, str(Path(__file__)), '--child', condition], capture_output=True, text=True)
        assert cp.returncode == 0, cp.stdout + cp.stderr
        results[condition] = json.loads(cp.stdout)
    for name, digest in PINS.items():
        assert hashlib.sha256((ROOT / 'tools' / name).read_bytes()).hexdigest() == digest
    out = dict(status='PASS', scope='Pinned pre-send execution repair only; no strategy/native result',
               native_jobs=0, shared_files_unchanged=True, private_file_changes=changes, checks=results)
    (R / 'BTC5M_FIXED15_EXECUTION_REPAIR_V2_20260913_COMPONENT.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
