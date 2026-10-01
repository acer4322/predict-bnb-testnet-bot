"""Actual native time only. Used by isolated scratch, safe to unit-test without DLL."""
import dataclasses
import hashlib
import json
import sys
from pathlib import Path

MAX_NS = 2**63 - 1
CALLS = []
DRAINS = []
CONTEXT = ('direct', None)
INVALID = False


def valid_clock(now):
    return 0 <= now < MAX_NS


def advance_to(bt, target_ms):
    global INVALID
    target = int(target_ms) * 1_000_000
    before = int(bt.current_timestamp)
    rec = dict(stage=CONTEXT[0], index=CONTEXT[1], target_ns=target, before_ns=before,
               effective_function='eof_runtime.advance_to', rc=None)
    CALLS.append(rec)
    if valid_clock(before) and before >= target:
        rec.update(after_ns=before, reached=True, elapse_called=False)
        return True
    if not valid_clock(before):
        rec.update(reached=False, error='INVALID_OR_UNINITIALIZED_CLOCK')
        raise RuntimeError('NATIVE_CLOCK_INVALID_OR_UNINITIALIZED')
    rec['elapse_called'] = True
    rc = int(bt.elapse(target - before))
    rec['rc'] = rc
    if rc not in (0, 1):
        INVALID = True
        rec.update(reached=False, error='NATIVE_EXECUTION_INVALID')
        raise RuntimeError(f'NATIVE_EXECUTION_INVALID rc={rc}; close, never continue')
    after = int(bt.current_timestamp)
    if not valid_clock(after) or after < before:
        INVALID = True
        rec.update(after_ns=after, reached=False, error='CLOCK_REGRESSION_OR_INVALID')
        raise RuntimeError('NATIVE_CLOCK_REGRESSION_OR_INVALID')
    reached = after >= target
    rec.update(after_ns=after, reached=reached)
    if rc == 0 and not reached:
        raise RuntimeError('NATIVE_RC0_WITHOUT_TARGET_REACHED')
    return reached


def require_advance(ex, bt, target_ms, stage, index):
    global CONTEXT
    CONTEXT = (stage, index)
    assert ex.advance_to is advance_to, 'EOF_EXECUTION_BINDING_INVALID'
    if not ex.advance_to(bt, target_ms):
        raise RuntimeError(f'EOF_CENSORED_BEFORE_SOURCE stage={stage} index={index} target_ms={target_ms}')
    check_process_time(bt, target_ms)


def check_process_time(bt, t):
    now = int(bt.current_timestamp)
    if not valid_clock(now) or int(t) * 1_000_000 > now:
        raise RuntimeError('PROCESS_TIME_AHEAD_OF_NATIVE_CLOCK')


def observe_drain(rc, before, actual, upper):
    if rc not in (0, 1, 2, 3) or not valid_clock(actual) or actual < before:
        raise RuntimeError('NATIVE_DRAIN_CLOCK_INVALID')
    observed = actual // 1_000_000
    DRAINS.append(dict(rc=rc, before_ns=before, actual_ns=actual,
                       query_upper_bound_ns=upper, observed_ms=observed))
    return observed


def finish(out, sim, backend, expected_sha):
    # Save before close; no engine inspection after a non-EOF execution error.
    result = dict(calls=CALLS, drains=DRAINS, invalid=INVALID, backend=str(backend), expected_sha=expected_sha)
    try:
        import hftbacktest as h
        binary = Path(backend) / 'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        result.update(loaded_hftbacktest=str(h.__file__), actual_sha=hashlib.sha256(binary.read_bytes()).hexdigest())
        assert result['actual_sha'] == expected_sha
        assert Path(h.__file__).resolve().parent == binary.parent.resolve()
        if sim is not None and not INVALID:
            result.update(native_clock_ns=int(sim.bt.current_timestamp), frames=sim.frame_count,
                          source_updates=len(sim.payload['updates']), producer_calls=sim.producer.calls,
                          receipts=list(sim._receipt_ledger.seen.values()), receipt_sequence=sim._receipt_ledger.sequence,
                          receipt_native=dict(sim._receipt_ledger.native), our_inventory=dict(sim.inv), our_cost=sim.cost,
                          native={k:float(getattr(sim.bt.state_values(0),k)) for k in sim._receipt_ledger.native},
                          carriers={k:dataclasses.asdict(c) for k,c in sim.gateway.ledger.carriers.items()},
                          terminal_drain=sim.terminal_drain)
    except Exception as exc:
        result['capture_error'] = type(exc).__name__ + ': ' + str(exc)
    (Path(out) / 'execution_clock.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')


def instrument_wrapper(module):
    original = module.transformed
    def transformed(*args, **kwargs):
        from patches import instrument_source
        candidate = json.loads((Path(__file__).parent / 'CANDIDATE.json').read_text())
        return instrument_source(original(*args, **kwargs), candidate)
    module.transformed = transformed
