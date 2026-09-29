"""No-fit independent result/hash/baseline verification for two memory jobs."""
import collections
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    returns = ROOT/'data/research/lan_worker_returns'
    old = json.loads((returns/'target-event-memory-20260912-v1/predictions.json').read_text())
    key = lambda p: (p['label'], p['fold'], p['market'], p['anchor'])
    prior = {key(p): p['p'] for p in old if p['model'] == 'EVENT2'}
    audits = []
    for job, package, progress in [
        ('target-whole-market-memory-20260912-v1', 'target_whole_market_memory_20260912_v1', 'TARGET_WHOLE_MARKET_MEMORY_PROGRESS_20260912.json'),
        ('target-prefix-adaptation-20260912-v1', 'target_prefix_adaptation_20260912_v1', 'TARGET_PREFIX_ADAPTATION_PROGRESS_20260912.json')]:
        folder = returns/job; pkg = ROOT/'.lan_worker_v1'/package
        result = json.loads((folder/'result.json').read_text()); predictions = json.loads((folder/'predictions.json').read_text())
        manifest = json.loads((pkg/'manifest.json').read_text())
        assert result['status'] == 'COMPLETE' and result['worker'] == 'DESKTOP-JIERAGF'
        assert result['predictions_sha256'] == sha(folder/'predictions.json')
        assert result['manifest_sha256'] == sha(pkg/'manifest.json')
        for name, digest in manifest['files'].items(): assert sha(pkg/name) == digest
        assert prior == {key(p): p['p'] for p in predictions if p['model'] == 'EVENT2'}
        grouped = collections.defaultdict(list)
        for p in predictions: grouped[(p['label'], p['fold'], p['model'])].append(p)
        checks = 0

        def verify(pp, expected):
            nonlocal checks
            assert len(pp) == expected['n']
            ll = -sum(p['y']*math.log(p['p'])+(1-p['y'])*math.log1p(-p['p']) for p in pp)/len(pp)
            brier = sum((p['y']-p['p'])**2 for p in pp)/len(pp)
            assert abs(ll-expected['log_loss']) < 1e-12 and abs(brier-expected['brier']) < 1e-12
            checks += 1

        for r in result['results']:
            for model, metrics in r['models'].items():
                pp = grouped[(r['label'], r['fold']['name'], model)]
                verify(pp, metrics)
                for mid, m in metrics['per_market'].items(): verify([p for p in pp if p['market'] == int(mid)], m)
        audits.append(dict(job=job, status='PASS', metric_checks=checks, exact_event2_parity=True,
            manifest_sha256=sha(pkg/'manifest.json'), predictions_sha256=sha(folder/'predictions.json'),
            result_sha256=sha(folder/'result.json'), progress_sha256=sha(ROOT/'data/research'/progress)))
    out = dict(status='PASS', native=False, fits=0, audits=audits)
    path = ROOT/'data/research/BTC5M_TARGET_WHOLE_MARKET_MEMORY_VERIFICATION_V1_20260912.json'
    path.write_text(json.dumps(out, indent=2)+'\n'); print(json.dumps(out))


if __name__ == '__main__': main()
