"""Read-only observer on frozen R247; separate from all production controllers."""
from __future__ import annotations

import argparse
import bisect
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import time
from collections import deque

ROOT = Path(__file__).resolve().parents[1]
TOL = 1e-8


def clean(x):
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in sorted(x.items(), key=lambda z: str(z[0]))}
    if isinstance(x, (list, tuple, deque)):
        return [clean(v) for v in x]
    if isinstance(x, set):
        return sorted((clean(v) for v in x), key=lambda v: json.dumps(v, sort_keys=True))
    if hasattr(x, 'item'):
        return clean(x.item())
    if isinstance(x, float) and not math.isfinite(x):
        return {'nonFinite': str(x)}
    if isinstance(x, Path):
        return str(x)
    if x is None or isinstance(x, (str, int, float, bool)):
        return x
    raise TypeError(type(x).__name__)


def encoded(x):
    return json.dumps(clean(x), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fingerprint(x):
    return hashlib.sha256(encoded(x)).hexdigest()


def latest_public(rows, times, t):
    i = bisect.bisect_left(times, t)-1
    return rows[i] if i >= 0 else None


def native_quantities(position, volume, balance):
    up, down = (volume+position)/2, (volume-position)/2
    return {'UP': up, 'DOWN': down, 'cost': down-balance}


def cash_check(inv, cost, native):
    q = native_quantities(native['position'], native['trading_volume'], native['balance'])
    errors = {'UP': inv['UP']-q['UP'], 'DOWN': inv['DOWN']-q['DOWN'], 'cost': cost-q['cost']}
    return {'nativeBinary': q, 'errors': errors,
            'pass': all(abs(x) <= TOL for x in errors.values())}


def native_state(sim):
    s = sim.bt.state_values(0)
    return {k: float(getattr(s, k)) for k in
            ('position', 'balance', 'fee', 'num_trades', 'trading_volume', 'trading_value')}


# Raw state only. No calls to candidate/routing methods, no inferred legal menu.
STATE_FIELDS = (
    'inv', 'cost', 'sideCost', 'un', 'pairedQty', 'pairReserve', 'orders',
    'slot_key', 'key_role', 'key_scope_gen', 'scopeSide', 'scopeGeneration',
    'scopeRiskCreditTotal', 'scopeRiskCreditConsumed', 'keyRepairQuotaRemaining',
    'keyOverflowQtyRemaining', 'obligations', 'activeObligationId', 'handoffKeys',
    'serviceLedger', 'repairLots', 'keyLotReservations', 'hybridMeta',
    'activeKeys', 'activeMeta', 'coreActiveKey', 'coreActiveMaterialized',
    'ordinaryActiveMintedCredit', 'ordinaryActiveCreditGeneration',
)
HISTORY_FIELDS = ('fillHist', 'placeHist', 'slot_history', 'splitEvents',
                  'scope_credit_events', 'serviceEvents', 'r247Events', 'r239events')


def state(sim):
    return {k: getattr(sim, k) for k in STATE_FIELDS}


def policy_signature(sim, result):
    # Everything JSON-like owned by the policy, including complete histories.
    # Only read-only input/feed/backend objects and the observer are excluded.
    ignore = {'bt', 'events', 'times', 'raw', 'payload', 'meta', 'observer', 'execModel'}
    values = {}
    opaque = []
    for k, v in vars(sim).items():
        if k in ignore:
            continue
        try:
            values[k] = clean(v)
        except TypeError:
            opaque.append(k)
    if opaque:
        raise ValueError(f'unaccounted policy fields: {opaque}')
    return fingerprint({'result': result, 'policy': values})


class Observer:
    def __init__(self, public):
        self.public = public
        self.times = [x['availableMs'] for x in public]
        assert self.times == sorted(set(self.times))
        self.digest = hashlib.sha256()
        self.process_n = self.decision_n = self.joined = self.scope_n = 0
        self.max_error = {'UP': 0., 'DOWN': 0., 'cost': 0.}
        self.mismatches = []
        self.samples = []
        self.max_slots = 0
        self.peak_abs_net = self.abs_net_integral_ms = 0.
        self.prev_t = None
        self.prev_net = 0.
        self.public_nonnull = {}

    def post_process(self, sim, t):
        self.process_n += 1
        n = native_state(sim)
        c = cash_check(sim.inv, sim.cost, n)
        for k, x in c['errors'].items():
            self.max_error[k] = max(self.max_error[k], abs(x))
        if not c['pass'] and len(self.mismatches) < 8:
            self.mismatches.append({'t': t, 'native': n, 'check': c,
                                    'ourInv': dict(sim.inv), 'ourCost': sim.cost})
        net = abs(float(sim.inv['UP']-sim.inv['DOWN']))
        if self.prev_t is not None:
            self.abs_net_integral_ms += max(0, t-self.prev_t)*self.prev_net
        self.prev_t, self.prev_net = t, net
        self.peak_abs_net = max(self.peak_abs_net, net)

    def before_option(self, sim, t):
        self.decision_n += 1
        s = state(sim)
        p = latest_public(self.public, self.times, t)
        if p is not None:
            assert p['availableMs'] < t
            self.joined += 1
            for k, v in p['features'].items():
                self.public_nonnull[k] = self.public_nonnull.get(k, 0) + int(v is not None)
        self.scope_n += int(sim.scopeGeneration > 0)
        self.max_slots = max(self.max_slots, len(sim.slot_key))
        item = {'t': t, 'state': s, 'public': p}
        self.digest.update(encoded(item))
        self.digest.update(b'\n')
        if len(self.samples) < 4 and sim.scopeGeneration > 0:
            sample = clean(item)
            if len(encoded(sample)) <= 16*1024:
                self.samples.append(sample)

    def compact(self):
        return {'processReceipts': self.process_n, 'optionReceipts': self.decision_n,
                'joinedPublic': self.joined, 'scopeReceipts': self.scope_n,
                'recordedClockViolations': 0, 'maxCashErrors': self.max_error,
                'firstMismatches': self.mismatches, 'stateStreamSha256': self.digest.hexdigest(),
                'maxSlotsAtOption': self.max_slots, 'peakAbsNet': self.peak_abs_net,
                'absNetIntegralShareMs': self.abs_net_integral_ms,
                'publicNonnullCounts': self.public_nonnull, 'samples': self.samples,
                'completeFeasibleOptionMenu': False,
                'collectorAvailabilityContractProven': False}


def verify_package():
    m = json.loads((ROOT/'MANIFEST.json').read_text())
    assert ROOT.resolve() == Path.cwd().resolve(), 'isolated cwd required'
    assert not (ROOT/'.lan_worker_v1/staging').exists(), 'stale override path'
    for rel, expected in m['files'].items():
        p = (ROOT/rel).resolve()
        assert p.is_relative_to(ROOT) and p.stat().st_size == expected['bytes'], rel
        assert hashlib.sha256(p.read_bytes()).hexdigest() == expected['sha256'], rel
    assert m['marketId'] == 2022527 and m['maxBE'] == 2
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--preflight-only', action='store_true')
    a = ap.parse_args()
    m = verify_package()
    # Existing worker runtime, not a package install or a global staging override.
    backend_root = Path('C:/BTC5M-worker/.tmp/hftbacktest_244').resolve()
    assert (backend_root/'hftbacktest/__init__.py').is_file(), 'frozen external HFT missing'
    sys.path.insert(0, str(backend_root))
    sys.path.insert(0, str(ROOT))
    r247 = importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle')
    origins = {}
    for name in m['expectedModules']:
        mod = importlib.import_module('tools.'+name)
        path = Path(mod.__file__).resolve()
        assert path == ROOT/'tools'/f'{name}.py', (name, path)
        origins[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    archive = importlib.import_module('src.predict_bot.execution_tape_archive_v1')
    assert Path(archive.__file__).resolve() == ROOT/'src/predict_bot/execution_tape_archive_v1.py'
    import hftbacktest
    backend = {'path': hftbacktest.__file__,
               'version': importlib.metadata.version('hftbacktest')}
    assert backend['version'] == '2.4.4', backend
    assert Path(hftbacktest.__file__).resolve().is_relative_to(backend_root)
    backend['loadedFileSha256'] = {}
    for mod in list(sys.modules.values()):
        file = getattr(mod, '__file__', None)
        if file and Path(file).resolve().is_relative_to(backend_root):
            path = Path(file).resolve()
            with path.open('rb') as f:
                backend['loadedFileSha256'][str(path.relative_to(backend_root))] = hashlib.file_digest(f, 'sha256').hexdigest()
    public = json.loads((ROOT/'public.json').read_text())
    assert len(public) == m['publicRows']
    preflight = {'ok': True, 'BE': 0, 'moduleCount': len(origins), 'backend': backend,
                 'manifestSha256': hashlib.sha256((ROOT/'MANIFEST.json').read_bytes()).hexdigest()}
    print(json.dumps({'preflight': preflight}), flush=True)
    if a.preflight_only:
        return
    outdir = Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    started = time.time()
    attempted = 0
    rows = []
    observer = Observer(public)
    base = r247.BoundedCoreServiceFavorableRecycleSim

    class Observed(base):
        def __init__(self, tape):
            self.observer = observer
            super().__init__(tape, 1, 4)

        def process(self, t):
            value = super().process(t)
            self.observer.post_process(self, t)
            return value

        def _open_one_option(self, t, qv, end):
            self.observer.before_option(self, t)
            return super()._open_one_option(t, qv, end)

    try:
        for branch, cls in [('N', base), ('S', Observed)]:
            attempted += 1
            (outdir/'BE_ACCOUNTING.json').write_text(json.dumps({'attemptedBE': attempted,
                 'startingBranch': branch, 'marketId': 2022527, 'oldITTUsed': 0}), encoding='utf-8')
            print(json.dumps({'starting': branch, 'attemptedBE': attempted}), flush=True)
            sim = cls(ROOT/'tapes/2022527.json.xz')
            try:
                assert sim.inv == {'UP': 0., 'DOWN': 0.} and sim.cost == 0.
                assert all(abs(v) <= TOL for v in native_state(sim).values())
                result = sim.run_r247('UP')  # Legacy post-hoc endpoint only, not realized winner.
                assert sim.traj == [] and sim.target == {'UP': 0., 'DOWN': 0.}
                assert abs(sum(float(f[2])*float(f[3]) for f in sim.fillHist)-sim.cost) <= TOL
                assert abs(sum(float(f[2]) for f in sim.fillHist)-sum(sim.inv.values())) <= TOL
                assert abs(result['floor']-(min(sim.inv.values())-sim.cost)) <= TOL
                assert abs(result['best']-(max(sim.inv.values())-sim.cost)) <= TOL
                native = native_state(sim)
                check = cash_check(sim.inv, sim.cost, native)
                sig = policy_signature(sim, result)
                correctness = {k: result.get(k) for k in ('r247ServiceCorrectnessPass',
                    'r247ServiceChecks', 'unauthorizedOverflowQty', 'repairQuotaExcessMax')}
                row = {'branch': branch, 'policySignature': sig, 'native': native,
                       'cashCheck': check, 'correctness': correctness,
                       'grossEndpoints': {s: sim.inv[s]-sim.cost for s in ['UP', 'DOWN']},
                       'floor': result['floor'], 'best': result['best'],
                       'submits': sim.submits, 'fillEvents': sim.fills,
                       'binaryBuyNotional': sim.cost, 'terminalInventory': dict(sim.inv),
                       'scopeCompletions': result['scopeCompletions'],
                       'scopeFlips': result['scopeFlips'], 'roleFills': result['roleFills'],
                       'filledQty': result['filledQty'], 'scopeGeneration': sim.scopeGeneration,
                       'maxPhysicalSlots': sim.max_simultaneous_slots,
                       'completeHistorySha256': fingerprint({k: getattr(sim,k) for k in HISTORY_FIELDS}),
                       'realizedWinner': None, 'realizedPnl': None, 'fullNetPnl': None}
                rows.append(clean(row))
                print(json.dumps({'completed': branch, 'fills': sim.fills, 'cashPass': check['pass']}), flush=True)
            finally:
                sim.close()
        n, s = rows
        parity = n['policySignature'] == s['policySignature'] and n['native'] == s['native']
        cash = all(r['cashCheck']['pass'] for r in rows) and all(x <= TOL for x in observer.max_error.values())
        correct = all(r['correctness']['r247ServiceCorrectnessPass'] and
                      abs(r['correctness']['unauthorizedOverflowQty']) <= 1e-9 and
                      abs(r['correctness']['repairQuotaExcessMax']) <= 1e-9 and
                      r['maxPhysicalSlots'] <= 4 for r in rows)
        exercise = all(r['fillEvents'] > 0 and r['scopeGeneration'] > 0 for r in rows) and observer.joined > 0
        verdict = ('OBSERVER_NOT_INERT_STOP' if not parity else
                   'CASH_LABEL_NOT_RECONCILED_STOP' if not cash else
                   'SUBSTRATE_CORRECTNESS_STOP' if not correct else
                   'NOT_EXERCISED_STOP' if not exercise else
                   'SOURCE_CAPTURE_SUPPORTED_IN_THIS_SMOKE')
        result = {'version': m['version'], 'marketId': 2022527, 'verdict': verdict,
                  'attemptedBE': attempted, 'elapsedSeconds': time.time()-started,
                  'preflight': preflight, 'rows': rows, 'observer': observer.compact(),
                  'gates': {'observerParity': parity, 'cashReconciled': cash,
                            'substrateCorrectness': correct, 'exercised': exercise},
                  'trainingReady': False, 'economicEdgeIdentified': False,
                  'fullNetCost': 'UNRESOLVED; backend fee is not venue full-cost accounting',
                  'realizedDistributionDDLOBO': 'UNRESOLVED; one unscored market',
                  'promotion': 'NO_AUTOMATIC_STAGE_A', 'live8781Touched': False}
        blob = json.dumps(clean(result), indent=2, allow_nan=False).encode()
        assert len(blob) <= 256*1024, 'compact output exceeded preregistered cap'
        (outdir/'COMPACT.json').write_bytes(blob)
        print(json.dumps({'verdict': verdict, 'gates': result['gates'], 'attemptedBE': attempted}), flush=True)
    except Exception as e:
        (outdir/'ERROR.json').write_text(json.dumps({'verdict': 'EXECUTION_ERROR',
             'attemptedBE': attempted, 'error': f'{type(e).__name__}: {e}', 'completedRows': rows}), encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
