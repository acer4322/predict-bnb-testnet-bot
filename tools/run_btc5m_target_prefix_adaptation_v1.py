"""Worker-only causal per-market update of the frozen EVENT2 prior."""
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


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--check-only', action='store_true'); args = ap.parse_args()
    root = Path(__file__).resolve().parent; manifest = json.loads((root/'manifest.json').read_text())
    for name, digest in manifest['files'].items(): assert sha(root/name) == digest
    assert socket.gethostname().upper() == 'DESKTOP-JIERAGF'
    rows = json.loads((root/'dataset.json').read_text())['rows']; previous = {}; by_market = collections.defaultdict(list)
    for r in rows:
        f = r['features']; token = ('P' if f['last_had_payment'] else '') + ('B' if f['last_had_birth'] else '') or 'D'
        prev = previous.get(r['market']); r['key'] = (prev[1] if prev and prev[0] == r['last_fill_bucket'] else 'UNKNOWN', token)
        previous[r['market']] = (r['next_fill_bucket'], token)
        assert r['book_received_ms'] < r['anchor_ms'] < r['next_fill_bucket']
        by_market[r['market']].append(r)
    if args.check_only:
        print(json.dumps(dict(check_pass=True, rows=len(rows), worker=socket.gethostname()))); return
    outdir = Path(os.environ['BTC5M_LAN_RESULT_DIR']); outdir.mkdir(parents=True, exist_ok=True)
    assert not (outdir/'result.json').exists(); start = time.monotonic()
    mids = sorted(by_market)
    folds = [dict(name='LOMO_'+str(m), train=[x for x in mids if x != m], test=[m]) for m in mids]
    folds.append(dict(name='FORWARD_TRAIN2', train=[2022527, 2022538], test=[m for m in mids if m not in (2022527, 2022538)]))
    preds = []; results = []; reveal_checks = 0

    def score(pp):
        return dict(n=len(pp), log_loss=sum(-p['y']*math.log(p['p'])-(1-p['y'])*math.log(1-p['p']) for p in pp)/len(pp),
                    brier=sum((p['y']-p['p'])**2 for p in pp)/len(pp))

    for label in ('next_weak_payment', 'next_active_payment'):
        for fold in folds:
            train = [r for r in rows if r['market'] in fold['train'] and r['labels'][label] is not None]
            assert set(fold['train']).isdisjoint(fold['test'])
            if fold['name'].startswith('FORWARD'):
                assert max(r['next_fill_bucket'] for r in train) < min(r['anchor_ms'] for m in fold['test'] for r in by_market[m])
            counts = collections.defaultdict(lambda: [0, 0]); global_rate = (sum(r['labels'][label] for r in train)+1)/(len(train)+2)
            for r in train: counts[r['key']][r['labels'][label]] += 1
            scored = collections.defaultdict(list)
            for mid in fold['test']:
                past = []  # Reset on market boundary; never import another test market.
                for r in by_market[mid]:
                    revealed = [old for old in past if old['next_fill_bucket'] < r['anchor_ms']]
                    assert len(revealed) == len(past)
                    reveal_checks += len(revealed)
                    c = counts.get(r['key']); prior = (c[1]+1)/(sum(c)+2) if c else global_rate
                    probs = dict(INTERCEPT=global_rate, EVENT2=prior)
                    for name, history in [('RECENT8', revealed[-8:]), ('WHOLE_PREFIX', revealed)]:
                        known = [old['labels'][label] for old in history if old['key'] == r['key'] and old['labels'][label] is not None]
                        strength = manifest['prior_strength']
                        probs[name] = (strength*prior+sum(known))/(strength+len(known))
                    if r['labels'][label] is not None:
                        for name, probability in probs.items():
                            p = dict(model=name, fold=fold['name'], label=label, market=mid, anchor=r['anchor_ms'],
                                y=r['labels'][label], p=probability)
                            scored[name].append(p); preds.append(p)
                    past.append(r)
            results.append(dict(label=label, fold=fold, models={name: score(pp) | dict(per_market={str(m): score([p for p in pp if p['market']==m]) for m in fold['test']}) for name, pp in scored.items()}))
    summaries = {}
    for label in ('next_weak_payment', 'next_active_payment'):
        lomo = [r for r in results if r['label']==label and r['fold']['name'].startswith('LOMO')]
        forward = next(r for r in results if r['label']==label and r['fold']['name'].startswith('FORWARD'))
        comparisons = {}
        for candidate, base in [('RECENT8','EVENT2'), ('WHOLE_PREFIX','EVENT2'), ('WHOLE_PREFIX','RECENT8')]:
            gains = [r['models'][base]['log_loss']-r['models'][candidate]['log_loss'] for r in lomo]
            fw = {m:forward['models'][base]['per_market'][m]['log_loss']-v['log_loss'] for m,v in forward['models'][candidate]['per_market'].items()}
            passed = sum(g>0 for g in gains)>=6 and statistics.median(gains)>0 and statistics.median(fw.values())>0
            passed = passed and forward['models'][candidate]['log_loss']<forward['models'][base]['log_loss'] and forward['models'][candidate]['brier']<=forward['models'][base]['brier']
            comparisons[candidate+'_vs_'+base] = dict(lomo_gains=gains, lomo_wins=sum(g>0 for g in gains), forward_gains=fw, pass_gate=passed)
        gate = all(comparisons['WHOLE_PREFIX_vs_'+b]['pass_gate'] for b in ('EVENT2','RECENT8'))
        summaries[label] = dict(verdict='WHOLE_PREFIX_ADAPTATION_INFORMATION_SUPPORTED' if gate else 'NOT_SUPPORTED_UNDER_FIXED_PROBE', comparisons=comparisons)
    out = dict(version='BTC5M_TARGET_PREFIX_ADAPTATION_V1', status='COMPLETE', worker=socket.gethostname(),
        manifest_sha256=sha(root/'manifest.json'), results=results, summaries=summaries, revealed_label_clock_checks=reveal_checks,
        elapsed_seconds=time.monotonic()-start, native=False, runtime_promotion=False,
        limitations=['Offline Target teacher history only; private receipt arrival remains unknown.',
            'Conditional next observed fill, no WAIT/order placement/rejection inference.',
            'Per-market update may capture market heterogeneity or resting execution plans, not private controller memory.',
            'All consumed markets; follow-up hypothesis after summary probe failure, not independent fresh certification.'])
    (outdir/'predictions.json').write_text(json.dumps(preds,indent=2)+'\n')
    out['predictions_sha256'] = sha(outdir/'predictions.json')
    (outdir/'result.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(dict(status='COMPLETE',summaries=summaries,elapsed=out['elapsed_seconds'])))


if __name__=='__main__': main()
