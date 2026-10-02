"""Worker-only, fixed nonlinear older-prefix information test. No policy export."""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import statistics
import time


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--check-only', action='store_true'); args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    for name, digest in manifest['files'].items(): assert sha(root / name) == digest, name
    assert socket.gethostname().upper() == 'DESKTOP-JIERAGF', 'training must run on verified worker'
    import numpy as np
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    data = json.loads((root / 'dataset.json').read_text(encoding='utf-8')); rows = data['rows']
    # Preserve the prior EVENT2 baseline exactly, including its excluded-flat gap.
    previous = {}
    for r in rows:
        f = r['features']; token = ('P' if f['last_had_payment'] else '') + ('B' if f['last_had_birth'] else '') or 'D'
        prev = previous.get(r['market'])
        r['memory_key'] = [prev[1] if prev and prev[0] == r['last_fill_bucket'] else 'UNKNOWN', token]
        previous[r['market']] = (r['next_fill_bucket'], token)
    assert len(rows) == 1097 and data['prefix_invariance_checks'] == 24
    for r in rows:
        assert r['book_received_ms'] < r['anchor_ms'] < r['next_fill_bucket']
        assert r['older_last_ms'] is None or r['older_last_ms'] < r['last_fill_bucket']
        assert all(math.isfinite(v) for part in ('local', 'older') for v in r[part].values())
    if args.check_only:
        print(json.dumps(dict(check_pass=True, rows=len(rows), worker=socket.gethostname(), sklearn=sklearn.__version__))); return
    outdir = Path(os.environ['BTC5M_LAN_RESULT_DIR']); outdir.mkdir(parents=True, exist_ok=True)
    assert not (outdir / 'result.json').exists()
    started = time.monotonic(); mids = sorted({r['market'] for r in rows})
    folds = [dict(name='LOMO_' + str(m), train=[x for x in mids if x != m], test=[m]) for m in mids]
    folds.append(dict(name='FORWARD_TRAIN2', train=[2022527, 2022538], test=[m for m in mids if m not in (2022527, 2022538)]))
    results = []; predictions = []

    def matrix(items, whole):
        return np.asarray([[r['local'][k] for k in data['local_features']] +
                           ([r['older'][k] for k in data['older_features']] if whole else []) for r in items])

    def metrics(items, probs):
        vals = [(r['labels'][label], float(p)) for r, p in zip(items, probs)]
        return dict(n=len(vals), positive=sum(y for y, _ in vals),
            log_loss=sum(-y * math.log(p) - (1-y) * math.log(1-p) for y, p in vals) / len(vals),
            brier=sum((y-p)**2 for y, p in vals) / len(vals))

    for label in ('next_weak_payment', 'next_active_payment'):
        for fold in folds:
            train = [r for r in rows if r['market'] in fold['train'] and r['labels'][label] is not None]
            test = [r for r in rows if r['market'] in fold['test'] and r['labels'][label] is not None]
            assert set(fold['train']).isdisjoint(fold['test'])
            if fold['name'].startswith('FORWARD'):
                assert max(r['next_fill_bucket'] for r in train) < min(r['anchor_ms'] for r in test)
            y = np.asarray([r['labels'][label] for r in train]); rate = (sum(y) + 1) / (len(y) + 2)
            counts = collections.defaultdict(lambda: [0, 0])
            for r in train: counts[tuple(r['memory_key'])][r['labels'][label]] += 1
            event_probs = []
            for r in test:
                c = counts.get(tuple(r['memory_key'])); event_probs.append((c[1]+1)/(sum(c)+2) if c else rate)
            probs = {'INTERCEPT': np.full(len(test), rate), 'EVENT2': np.asarray(event_probs)}
            for name, whole in (('LOCAL', False), ('WHOLE', True)):
                model = HistGradientBoostingClassifier(**manifest['model_parameters'])
                model.fit(matrix(train, whole), y); xt = matrix(test, whole)
                probs[name] = np.clip(model.predict_proba(xt)[:, 1], 1e-6, 1-1e-6)
                if whole:
                    # Outcome-independent, within-market AND phase-third displacement.
                    # This uses future test contexts only as a noncausal diagnostic corruption,
                    # never as a candidate model input or a reported strict-past policy.
                    shuffled = xt.copy(); rng = np.random.default_rng(20260912)
                    groups = collections.defaultdict(list)
                    for i, r in enumerate(test): groups[(r['market'], min(2, int(r['features']['phase']*3)))].append(i)
                    for ids in groups.values():
                        shuffled[np.asarray(ids), len(data['local_features']):] = xt[rng.permutation(ids), len(data['local_features']):]
                    probs['WHOLE_DISPLACED'] = np.clip(model.predict_proba(shuffled)[:, 1], 1e-6, 1-1e-6)
            result = dict(label=label, fold=fold, models={})
            for name, p in probs.items():
                result['models'][name] = metrics(test, p)
                result['models'][name]['per_market'] = {}
                for m in fold['test']:
                    ids = [i for i, r in enumerate(test) if r['market'] == m]
                    result['models'][name]['per_market'][str(m)] = metrics([test[i] for i in ids], p[ids])
                # Early/middle/late descriptive breakdown is reported, never selected.
                result['models'][name]['phase_thirds'] = {}
                for phase in range(3):
                    ids = [i for i, r in enumerate(test) if min(2, int(r['features']['phase']*3)) == phase]
                    if ids: result['models'][name]['phase_thirds'][str(phase)] = metrics([test[i] for i in ids], p[ids])
                for r, probability in zip(test, p):
                    predictions.append(dict(model=name, fold=fold['name'], label=label, market=r['market'], anchor=r['anchor_ms'],
                        y=r['labels'][label], p=float(probability)))
            results.append(result)
    summaries = {}
    for label in ('next_weak_payment', 'next_active_payment'):
        local = [r for r in results if r['label'] == label and r['fold']['name'].startswith('LOMO')]
        forward = next(r for r in results if r['label'] == label and r['fold']['name'].startswith('FORWARD'))
        comparisons = {}
        for candidate, base in [('LOCAL', 'EVENT2'), ('WHOLE', 'LOCAL'), ('WHOLE', 'EVENT2'), ('WHOLE', 'WHOLE_DISPLACED')]:
            gains = [r['models'][base]['log_loss']-r['models'][candidate]['log_loss'] for r in local]
            fw = {m: forward['models'][base]['per_market'][m]['log_loss']-v['log_loss'] for m, v in forward['models'][candidate]['per_market'].items()}
            passed = sum(g > 0 for g in gains) >= 6 and statistics.median(gains) > 0 and statistics.median(fw.values()) > 0
            passed = passed and forward['models'][candidate]['log_loss'] < forward['models'][base]['log_loss'] and forward['models'][candidate]['brier'] <= forward['models'][base]['brier']
            comparisons[candidate + '_vs_' + base] = dict(lomo_gains=gains, lomo_wins=sum(g > 0 for g in gains), forward_gains=fw, pass_gate=bool(passed))
        gate = all(comparisons['WHOLE_vs_' + b]['pass_gate'] for b in ('LOCAL', 'EVENT2', 'WHOLE_DISPLACED'))
        summaries[label] = dict(verdict='OLDER_PREFIX_INFORMATION_SUPPORTED' if gate else 'NOT_SUPPORTED_UNDER_FIXED_PROBE', comparisons=comparisons)
    out = dict(version='BTC5M_TARGET_WHOLE_MARKET_MEMORY_V1', status='COMPLETE', worker=socket.gethostname(),
        manifest_sha256=sha(root/'manifest.json'), results=results, summaries=summaries,
        elapsed_seconds=time.monotonic()-started, native=False, runtime_promotion=False,
        limitations=data['limitations'] + ['A failed fixed summary/model does not disprove private whole-market controller memory.',
            'Displaced contexts are an offline noncausal negative control only; correlation is not controller identification.'])
    (outdir/'predictions.json').write_text(json.dumps(predictions, indent=2)+'\n', encoding='utf-8')
    out['predictions_sha256'] = sha(outdir/'predictions.json')
    (outdir/'result.json').write_text(json.dumps(out, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status='COMPLETE', summaries=summaries, elapsed=out['elapsed_seconds'])))


if __name__ == '__main__': main()
