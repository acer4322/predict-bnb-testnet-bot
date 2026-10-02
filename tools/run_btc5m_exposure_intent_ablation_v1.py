"""Frozen native replay: separate direction sign from exposure magnitude.

No Target-derived runtime direction. FIRST_CONFIRMED is a mechanism probe, not
an identified directional alpha. Only the exposure-sign seam varies by arm.
"""
from __future__ import annotations
import argparse
import collections
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import sys

MODES = ('CONTROL', 'FOLLOW_CURRENT', 'LATCH_FIRST_CONFIRMED')


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


class ExposureIntent:
    def __init__(self, mode):
        assert mode in MODES
        self.mode = mode; self.sign = 0; self.birth = None; self.rows = []

    def apply(self, legacy, own_net, frame):
        # Preserve the existing float calculation exactly in CONTROL.
        current = 1 if own_net > 0 else -1 if own_net < 0 else 0
        if self.sign == 0 and current:
            self.sign = current
            self.birth = dict(t=int(frame['t']), index=int(frame['index']), sign=current,
                provenance='FIRST_NONZERO_OWN_CONFIRMED_INVENTORY', inv=dict(frame['own_view']['inv']))
        if self.mode == 'CONTROL': return legacy
        sign = current if self.mode == 'FOLLOW_CURRENT' else self.sign
        return sign * abs(legacy)

    def capture(self, frame, producer, ledger, legacy, applied, desired):
        totals = producer.atomic.totals()
        accounts = {s: ledger.account(pid) for pid, s in ((1, 'UP'), (2, 'DOWN'))}
        carriers = ledger.carriers
        self.rows.append(dict(t=int(frame['t']), index=int(frame['index']), gateway_state_id=frame['gateway_state_id'],
            inv=dict(frame['own_view']['inv']), cost=float(frame['own_view']['cost']),
            legacy_exposure=legacy, applied_exposure=applied, retained_direction_sign=self.sign,
            desired=dict(desired), atomic_outstanding=totals, atomic_completed=producer.atomic.completed,
            reserved_qty={s: a['reserved_qty'] for s, a in accounts.items()},
            reserved_cash={s: a['reserved_cash'] for s, a in accounts.items()},
            carrier_states=dict(collections.Counter(c.state for c in carriers.values())),
            terminal_total=sum(c.state == 'TERMINAL' for c in carriers.values()),
            first_direction_birth=self.birth))


def self_test():
    frame = dict(t=1, index=1, own_view=dict(inv=dict(UP=1., DOWN=0.)))
    for mode in MODES:
        c = ExposureIntent(mode)
        assert c.apply(0., 0., frame) == 0 and c.birth is None
        first = c.apply(-.2, .3, frame)
        assert first == (-.2 if mode == 'CONTROL' else .2)
        birth = dict(c.birth)
        assert c.apply(0., 0., dict(frame, t=2)) == 0 and c.birth == birth
        reversed_result = c.apply(.4, -.5, dict(frame, t=3))
        assert reversed_result == (-.4 if mode == 'FOLLOW_CURRENT' else .4)
        assert c.birth == birth and c.sign == 1
        mirrored = ExposureIntent(mode)
        assert mirrored.apply(.2, -.3, frame) == -first


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--mode', choices=MODES, required=True)
    ap.add_argument('--check-only', action='store_true'); args = ap.parse_args(); self_test()
    package = Path(__file__).resolve().parent; manifest = json.loads((package/'manifest.json').read_text())
    for name, digest in manifest['files'].items(): assert sha(package/name) == digest, name
    wrapper = load('intent_clock_wrapper', package/'clock_wrapper.py')
    source = wrapper.transformed(package, manifest, 'FIXED_TRAIN_EVENTS'); replace = wrapper.replace_once
    source = replace(source, 'self.total_frames=1;self.clock_observations=[]',
        'self.total_frames=1;self.clock_observations=[];self.intent=_ExposureIntent(_MODE)')
    original = "exposure=math.tanh(w[3]*x['own_net']);up=(1.+exposure)/2."
    source = replace(source, original,
        "legacy_exposure=math.tanh(w[3]*x['own_net']);exposure=self.intent.apply(legacy_exposure,x['own_net'],f);up=(1.+exposure)/2.")
    marker = "    for s in pid:\n"
    source = replace(source, marker,
        "    self.intent.capture(f,self,ledger,legacy_exposure,exposure,desired)\n" + marker)
    source = replace(source, " except Exception as ex:\n  result['status']='ERROR'",
        " except Exception as ex:\n  result['failure_capture']=_save_failure(out,locals())\n  result['status']='ERROR'")
    compile(source, str(package/'frozen_runner.py'), 'exec')
    assert socket.gethostname().upper() == 'DESKTOP-JIERAGF', 'worker only'
    for path, digest in manifest['remote_inputs'].items(): assert sha(Path(path)) == digest, path
    backend = Path(manifest['backend']); binary = backend/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    assert sha(binary) == manifest['native_sha256']
    if args.check_only:
        sys.path.insert(0, str(backend)); import hftbacktest
        assert Path(hftbacktest.__file__).resolve().parent == (backend/'hftbacktest').resolve()
        print(json.dumps(dict(status='PASS', worker=socket.gethostname(), mode=args.mode,
            checks='frozen hashes, native import, syntax, flat retention, own reversal, side symmetry; no replay'))); return
    assert int(os.environ.get('OMP_NUM_THREADS', '999')) <= 4
    namespace = dict(__name__='intent_frozen', __file__=str(package/'frozen_runner.py'), _MODE=args.mode, _ExposureIntent=ExposureIntent)

    def write_trace(out, trace, producer, name):
        payload = dict(states=trace.states, plans=trace.clock_plans, native_actions=trace.actions,
            observations=producer.clock_observations, intent=producer.intent.rows)
        raw = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        with gzip.GzipFile(filename=str(out/name), mode='wb', mtime=0) as stream: stream.write(raw)
        return hashlib.sha256(raw).hexdigest()

    def save_trace(out, trace, producer, actual_frames):
        digest = write_trace(out, trace, producer, 'clock_trace.json.gz')
        return dict(mode='FIXED_TRAIN_EVENTS', intent_mode=args.mode, actual_replay_frames=actual_frames,
            runtime_clock_total_frames=producer.total_frames, trace_file='clock_trace.json.gz', trace_payload_sha256=digest,
            manifest_sha256=sha(package/'manifest.json'), transformed_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
            first_direction_birth=producer.intent.birth, controller_rows=len(producer.intent.rows),
            target_direction_input=False, oracle=False, numeric_parameters_frozen=True,
            limitation='First own inventory side is an experimental seed, not identified Target intent.')

    def save_failure(out, context):
        capture = dict(status='PARTIAL', fields_missing='UNKNOWN', intent_mode=args.mode)
        try:
            if context.get('tr') is not None and context.get('producer') is not None:
                capture['trace_payload_sha256'] = write_trace(out, context['tr'], context['producer'], 'failure_trace.json.gz')
            sim = context.get('sim')
            if sim is not None:
                (out/'failure_receipts.json').write_text(json.dumps(getattr(sim, '_receipt_delta_rows', []), indent=2)+'\n')
        except Exception as error: capture['capture_error'] = repr(error)
        return capture

    namespace.update(_save_clock_trace=save_trace, _save_failure=save_failure)
    exec(source, namespace); original_loader = namespace['load_module']

    def loader(name, path):
        module = original_loader(name, path)
        if path == namespace['HELPER_PATH']:
            class Trace(module.TraceLite):
                def __init__(self): super().__init__(); self.clock_plans = []
                def plan(self, event):
                    super().plan(event); self.clock_plans.append(dict(t=self.now, operations=event['operations']))
            module.TraceLite = Trace
        return module

    namespace['load_module'] = loader
    sys.argv = [str(package/'frozen_runner.py'), '--market', '2026085', '--route-mode', 'PASSIVE_ONLY',
        '--rearm-mode', 'RESPONSIBILITY_DUAL_CAPACITY']
    namespace['main']()


if __name__ == '__main__': main()
