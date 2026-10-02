"""Frozen-model Phase-B smoke; reuse existing native branches, no policy changes."""
from __future__ import annotations
import argparse
import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile
import numpy as np
import joblib


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1048576), b''):
            h.update(b)
    return h.hexdigest()


def equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return bool(np.isclose(a, b, rtol=1e-11, atol=1e-9))
    return a == b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--bundle', required=True)
    ap.add_argument('--model-dir', required=True)
    ap.add_argument('--reference', required=True)
    ap.add_argument('--source-dir', required=True)
    ap.add_argument('--spec-file', help='Preregistered legal action catalog; no outcome-based selection')
    ap.add_argument('--output', default='AUTO')
    args = ap.parse_args()
    sys.path.insert(0, str(Path.cwd()))
    src = Path(args.source_dir)
    world = module('phaseb_frozen_world', src / 'train_management_v1_current_v3b_execution_world.py')
    e1 = module('phaseb_frozen_e1', src / 'run_gpt6_e1_v3b_one_shot_native_repair_smoke3.py')
    if args.spec_file:
        catalog = json.loads(Path(args.spec_file).read_text(encoding='utf-8'))
        e1.SPECS = {int(r['marketId']): r['spec'] for r in catalog['rows']}
    assert world.v3b is e1.v3b
    md = Path(args.model_dir)
    package = joblib.load(md / 'world_models.joblib')
    assert package['stateFeatures'] == world.STATE and package['actionFeatures'] == world.ACTION
    train_report = json.loads((md / 'result.json').read_text(encoding='utf-8'))
    training = [json.loads(x) for x in (md / 'training_rows.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    ordered = sorted({(r['windowEndMs'], r['marketId']) for r in training})
    trained = {m for _, m in ordered[:train_report['trainMarkets']]}
    ref = {r['marketId']: r for r in json.loads(Path(args.reference).read_text(encoding='utf-8'))['rows']}
    features = world.STATE + world.ACTION + ['side', 'role', 'route']
    labels = [f'{target}{h}s' for h in (3, 5) for target in ('anyFill', 'fillQty', 'repairPayQty', 'overflowQty', 'cancelReq', 'terminal')]
    models = package['models']

    def predictions(row):
        x = world.X([row], True)
        out = {}
        for label in labels:
            model = models[(label, 'stateAction')]
            out[label] = (float(model.predict_proba(x)[0, 1]) if label.startswith(('anyFill', 'cancelReq', 'terminal'))
                          else max(0., float(model.predict(x)[0])))
        return out

    class Capture(e1.E1OneShot):
        def __init__(self, tape, spec, mutate):
            super().__init__(tape, spec, mutate)
            self._end_ms = int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
            self.training_rows = []
            self.pre_rows = []
            self.common_candidates = {}
            self.open_attempts = self.open_without_submit = 0

        def _snapshot_prefix(self, t):
            super()._snapshot_prefix(t)
            for k in ('ctrl', 'trt'):
                c = self.spec[k]
                self.common_candidates[k] = world.TrainingTraceSim._state_row(
                    self, t, c['side'], c['role'], c['price'], c['qty'], 'PASSIVE', 'CANDIDATE', 'COMMON_PREFIX')

        def _open_one_option(self, t, qv, end):
            self.open_attempts += 1
            before = self.submits
            ans = super()._open_one_option(t, qv, end)
            self.open_without_submit += int(self.submits == before)
            return ans

    original = e1.base.MinimalPairRoleSim._submit_role

    def capture_submit(sim, t, side, role, price, qty, proj, source):
        target = isinstance(sim, Capture) and int(t) == int(sim.spec['t']) and not sim.training_rows
        n = sim.n
        if target:
            pre = world.TrainingTraceSim._state_row(sim, t, side, role, price, qty, 'PASSIVE', f'{side}_{n}', source)
        ok = original(sim, t, side, role, price, qty, proj, source)
        if target and ok:
            sim.pre_rows.append(pre)
            sim.training_rows.append(world.TrainingTraceSim._state_row(sim, t, side, role, price, qty, 'PASSIVE', f'{side}_{n}', source))
        return ok

    rows = []
    e1.base.MinimalPairRoleSim._submit_role = capture_submit
    try:
        with tempfile.TemporaryDirectory(prefix='phaseb_smoke3_') as td:
            with zipfile.ZipFile(args.bundle) as z:
                co = {int(r['marketId']): r for r in json.loads(z.read('cohort.json'))['rows']}
                for mid in e1.SPECS:
                    z.extract(f'tapes/{mid}.json.xz', td)
            for mid, spec in e1.SPECS.items():
                pair = {'marketId': mid, 't': spec['t'], 'trainingMembership': 'TRAIN' if mid in trained else 'VALIDATION', 'branches': {}}
                for name, mutate in (('control', False), ('treatment', True)):
                    sim = Capture(Path(td) / 'tapes' / f'{mid}.json.xz', spec, mutate)
                    try:
                        result = sim.run_e1()
                        observed = world.TrainingTraceSim.finalize_labels(sim)
                        assert len(observed) == 1, (mid, name, 'intervention must submit exactly one captured carrier')
                        row = observed[0]
                        metric = e1.metrics(result, co[mid]['winner'], spec['favoredSide'])
                        key = row['key']
                        fills = [x for x in sim.fill_accounting if str(x['key']) == key]
                        lifecycle = [x for x in sim.slot_history if str(x.get('key')) == key]
                        changes = {k: [sim.pre_rows[0][k], row[k]] for k in features if not equal(sim.pre_rows[0][k], row[k])}
                        seq = []
                        for x in sim.fill_accounting:
                            role = sim.key_role.get(x['key'], '')
                            v = 'E' if role == 'SATELLITE_EXPAND' else 'R' if role in world.v3b.REPAIR_ROLES else 'O'
                            if not seq or seq[-1] != v:
                                seq.append(v)
                        cycles = sum(seq[i:i+3] == ['E', 'R', 'E'] for i in range(len(seq)-2))
                        pair['branches'][name] = {
                            'featuresPostSubmit': {k: row[k] for k in features},
                            'featuresPreSubmit': {k: sim.pre_rows[0][k] for k in features},
                            'postMinusPreFeatureChanges': changes,
                            'predictedPostSubmit': predictions(row),
                            'predictedPreSubmit': predictions(sim.pre_rows[0]),
                            'observed': {k: row[k] for k in labels},
                            'prefixDigest': result['e1PrefixDigest'],
                            'intervention': result['e1Intervention'],
                            'metrics': metric,
                            'frozenMetricsParity': equal(metric, ref[mid][name]['metrics']) if name in ref[mid] else None,
                            'frozenPrefixParity': result['e1PrefixDigest'] == ref[mid][name]['prefixDigest'] if ref[mid].get(name, {}).get('prefixDigest') else None,
                            'maxSlots': result.get('maxSimultaneousSlots'),
                            'full5sObserved': int(row['t']) + 5000 <= sim._end_ms,
                            'activity': {'tradeCoverage': int(metric['fills'] > 0), 'fills': metric['fills'], 'submits': metric['submits'], 'alternations': metric['alternations'], 'buyNotional': metric['buyNotional'], 'grossExposure': result['upQty'] + result['downQty'], 'openAttempts': sim.open_attempts, 'openAttemptWithoutSubmitFraction': sim.open_without_submit / max(1, sim.open_attempts), 'expandRepairExpandRoleCycleProxy': cycles, 'zeroAction': metric['submits'] == 0, 'nearZeroActionLe1': metric['submits'] <= 1, 'fixedFavoredPerNotional': metric['fixedFavoredPayoff']/max(1e-12,metric['buyNotional']), 'winnerPnlPerNotionalPosthoc': metric['winnerPnlPostHoc']/max(1e-12,metric['buyNotional'])},
                            'carrierFills': fills, 'carrierLifecycle': lifecycle,
                            'quantityLedgerSummary': result['quantityLedgerSummary'],
                            'commonPrefixCandidates': sim.common_candidates,
                        }
                        if not mutate:
                            native = [x for x in training if x['marketId'] == mid and x['t'] == row['t'] and x['key'] == key]
                            pair['controlTrainingRowMatches'] = len(native)
                            pair['controlTrainingFeatureMismatches'] = [k for k in features if len(native) != 1 or not equal(row[k], native[0][k])]
                            pair['controlTrainingLabelMismatches'] = [k for k in labels if len(native) != 1 or not equal(row[k], native[0][k])]
                    finally:
                        sim.close()
                    print(json.dumps({'marketId': mid, 'branch': name, 'captured': 1, 'referenceParity': pair['branches'][name]['frozenMetricsParity']}), flush=True)
                a, b = pair['branches']['control'], pair['branches']['treatment']
                pair['commonPrefixCandidateParity'] = equal(a['commonPrefixCandidates'], b['commonPrefixCandidates'])
                pair['prefixParity'] = a['prefixDigest'] == b['prefixDigest']
                pair['deltas'] = {label: {'observed': b['observed'][label]-a['observed'][label], 'predictedPostSubmit': b['predictedPostSubmit'][label]-a['predictedPostSubmit'][label], 'predictedPreSubmit': b['predictedPreSubmit'][label]-a['predictedPreSubmit'][label]} for label in labels}
                pair['correctnessPass'] = bool(pair['prefixParity'] and pair['commonPrefixCandidateParity'] and not pair['controlTrainingFeatureMismatches'] and not pair['controlTrainingLabelMismatches'] and a['frozenMetricsParity'] and all(x['frozenMetricsParity'] is not False and x['frozenPrefixParity'] is not False and x['full5sObserved'] and not x['metrics']['ledgerViolations'] and x['maxSlots'] <= 4 for x in (a,b)) and a['intervention']['submitMatchesFrozen'] and b['intervention']['exactPreconditions'] and b['intervention']['submitOk'])
                rows.append(pair)
    finally:
        e1.base.MinimalPairRoleSim._submit_role = original
    summary = {}
    for label in labels:
        ds = [r['deltas'][label] for r in rows]
        non = [d for d in ds if abs(d['observed']) > 1e-9]
        summary[label] = {'markets': len(rows), 'carrierRows': 2*len(rows), 'nonTiedPairs': len(non), 'observedTies': len(ds)-len(non)}
        for mode in ('PostSubmit', 'PreSubmit'):
            errors = [(b['predicted'+mode][label]-b['observed'][label]) for r in rows for b in r['branches'].values()]
            summary[label][mode] = {'correctNonTieSigns': sum(d['observed']*d['predicted'+mode] > 0 for d in non), 'mae': float(np.mean(np.abs(errors))), 'brier' : float(np.mean(np.square(errors))) if label.startswith(('anyFill','terminal','cancelReq')) else None}
    out = {'version': 'GPT6_PHASEB_WORLD_COUNTERFACTUAL_CORPUS_V1' if args.spec_file else 'GPT6_PHASEB_WORLD_COUNTERFACTUAL_SMOKE3_V1', 'researchOnly': True, 'runtimeAuthority': False,
           'modelRetrained': False, 'allCorrectnessPass': all(r['correctnessPass'] for r in rows), 'summary': summary, 'rows': rows,
           'provenance': {'modelSha256': sha(md/'world_models.joblib'), 'trainingRowsSha256': sha(md/'training_rows.jsonl'), 'e1SourceSha256': sha(src/'run_gpt6_e1_v3b_one_shot_native_repair_smoke3.py'), 'worldSourceSha256': sha(src/'train_management_v1_current_v3b_execution_world.py'), 'bundleSha256': sha(args.bundle), 'validatorSha256': sha(__file__)},
           'limitations': ['Consumed development probes, not untouched reserve evaluation', 'Post-submit state is train-compatible; pre-submit scoring is a diagnostic shift', 'Raw independent quantity heads are not guaranteed accounting coherent', 'Open-attempt no-submit fraction is not eligible-opportunity HOLD rate', 'Role E-R-E cycles are a labeled-fill proxy, not exact slot lifecycle cycles', 'Small paired cohort does not establish calibration; trainingMembership must be retained']}
    output = Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if args.output.upper() == 'AUTO' else Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'allCorrectnessPass': out['allCorrectnessPass'], 'summary': summary}), flush=True)


if __name__ == '__main__':
    main()
