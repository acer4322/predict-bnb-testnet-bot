"""Independent source/score audit of saved predictions. No fitting or dispatch."""
import collections
import gzip
import hashlib
import json
import math
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / 'data/research'
STEM = 'BTC5M_CONCURRENT_FLOW_PROBE_V1_20260913'
PACKAGE = ROOT / '.lan_worker_v1/concurrent_flow_probe_20260913_v1'
RETURN = RESEARCH / 'lan_worker_returns/concurrent-flow-probe-20260913-v1'
CLASSES = ('WEAK_ONLY', 'STRONG_ONLY', 'BOTH')


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()) if path.suffix == '.gz' else path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def score(predictions):
    n = len(predictions)
    if not n:
        return {'n': 0}
    truth = [[int(x['label'] == k) for k in CLASSES] for x in predictions]
    probs = [x['p'] for x in predictions]
    return dict(n=n,
        joint_log_loss=math.fsum(-math.log(p[y.index(1)]) for p, y in zip(probs, truth)) / n,
        joint_brier=math.fsum(math.fsum((p[i] - y[i]) ** 2 for i in range(3)) for p, y in zip(probs, truth)) / n,
        marginal_brier={name: math.fsum((sum(p[i] for i in inds) - sum(y[i] for i in inds)) ** 2
            for p, y in zip(probs, truth)) / n for name, inds in [('weak', (0, 2)), ('strong', (1, 2)), ('both', (2,))]})


def compare(actual, expected):
    assert actual.keys() == expected.keys()
    for k, v in actual.items():
        if isinstance(v, dict):
            compare(v, expected[k])
        else:
            assert abs(v - expected[k]) < 1e-12, (k, v, expected[k])


def main():
    m = read(PACKAGE / 'manifest.json')
    assert m == read(RESEARCH / (STEM + '_PREREGISTERED.json'))
    for name, expected in m['files'].items():
        assert sha(PACKAGE / name) == expected
    data = read(PACKAGE / 'dataset.json')
    rows = data['rows']
    old = read(ROOT / '.lan_worker_v1/target_event_memory_20260912_v1/dataset.json')['rows']
    assert len(rows) == len(old) == 1097
    for r, original in zip(rows, old):
        assert all(r[k] == v for k, v in original.items())
    sources = {}
    for source in data['sources']:
        path = ROOT / source['path']
        assert sha(path) == source['sha256']
        s = read(path)
        grouped = collections.defaultdict(list)
        for leg in s['targetActions']:
            assert leg['shares'] > 0 and leg['quote_type'] == 'BID'
            grouped[leg['event_ms']].append(leg)
        first = {(p['role'], p['side'], p['order_hash']): p['first_event_ms'] for p in s['targetParents']}
        sources[s['market']['market_id']] = (grouped, first)
    availability = collections.Counter()
    prev_by_market = {}
    for r in rows:
        grouped, first = sources[r['market']]
        anchor = r['anchor_ms']
        quantities = {side: math.fsum(x['shares'] for t, legs in grouped.items() if t < anchor
                      for x in legs if x['side'] == side) for side in ('UP', 'DOWN')}
        strong = max(quantities, key=quantities.get)
        weak = 'DOWN' if strong == 'UP' else 'UP'
        gap = quantities[strong] - quantities[weak]
        assert gap > 0 and strong == r['strong_side']
        assert abs(gap - r['features']['net_fraction'] * math.exp(r['features']['gross_log'])) < 1e-7
        assert r['book_received_ms'] < anchor < r['next_fill_bucket']
        later_times = [t for t in grouped if t > anchor]
        assert min(later_times) == r['next_fill_bucket']

        def signature(legs):
            sides = {x['side'] for x in legs}
            token = 'BOTH' if len(sides) == 2 else 'WEAK_ONLY' if weak in sides else 'STRONG_ONLY'
            delta = math.fsum(x['shares'] * (int(x['side'] == weak) - x['price']) for x in legs)
            return token, token + ('_GAIN' if delta > 1e-9 else '_SPEND' if delta < -1e-9 else '_UNCHANGED')

        current = grouped[r['last_fill_bucket']]
        assert signature(current) == (r['side_token'], r['cash_token'])
        prior = prev_by_market.get(r['market'])
        history = list(current)
        if prior and prior['next_fill_bucket'] == r['last_fill_bucket']:
            legs = grouped[prior['last_fill_bucket']]
            assert signature(legs) == (r['side_prior'], r['cash_prior'])
            history += legs
        else:
            assert (r['side_prior'], r['cash_prior']) == ('UNKNOWN', 'UNKNOWN')
        availability['event_past_history_rows'] += int(all(x['event_ms'] < anchor for x in history))
        availability['observer_received_history_rows'] += int(all(x['observed_at_ms'] < anchor for x in history))
        future = grouped[r['next_fill_bucket']]
        label = signature(future)[0]
        assert label == r['joint_label']
        assert r['side_labels'] == dict(weak=int(label != 'STRONG_ONLY'), strong=int(label != 'WEAK_ONLY'), both=int(label == 'BOTH'))
        assert r['next_all_parents_first_observed_here'] == all(first[(x['role'], x['side'], x['order_hash'])] == r['next_fill_bucket'] for x in future)
        assert r['next_surplus_crossing'] == (math.fsum(x['shares'] * (1 if x['side'] == weak else -1) for x in future) > gap + 1e-7)
        prev_by_market[r['market']] = r
    result = read(RETURN / 'result.json')
    assert result['status'] == 'COMPLETE' and result['worker'] == 'DESKTOP-JIERAGF'
    assert result['manifest_sha256'] == sha(PACKAGE / 'manifest.json')
    assert result['predictions_sha256'] == sha(RETURN / 'predictions.json')
    assert result['legacy_parity'] == dict(n=1864, max_absolute_error=0.0)
    predictions = read(RETURN / 'predictions.json')
    assert len(predictions) == 9320
    row_by_key = {(r['market'], r['anchor_ms']): r for r in rows}
    grouped_predictions = collections.defaultdict(list)
    seen = set()
    for p in predictions:
        key = (p['fold'], p['model'], p['market'], p['anchor'])
        assert key not in seen
        seen.add(key)
        assert p['label'] == row_by_key[(p['market'], p['anchor'])]['joint_label']
        assert all(0 < v < 1 for v in p['p']) and abs(sum(p['p']) - 1) < 1e-12
        grouped_predictions[(p['fold'], p['model'])].append(p)
    recomputed = {}
    for fold in result['results']:
        f = fold['fold']
        assert set(f['train']).isdisjoint(f['test'])
        if f['name'] == 'FORWARD_TRAIN2':
            assert max(r['next_fill_bucket'] for r in rows if r['market'] in f['train']) < min(r['anchor_ms'] for r in rows if r['market'] in f['test'])
        for model, saved in fold['models'].items():
            ps = grouped_predictions[(f['name'], model)]
            assert {(p['market'], p['anchor']) for p in ps} == {k for k in row_by_key if k[0] in f['test']}
            actual = score(ps)
            compare(actual, saved['score'])
            recomputed[(f['name'], model)] = actual
            for mid, metrics in saved['per_market'].items():
                compare(score([p for p in ps if p['market'] == int(mid)]), metrics)
            for name, field, value in [('first_observed_parents', 'next_all_parents_first_observed_here', True),
                                       ('no_surplus_cross', 'next_surplus_crossing', False)]:
                compare(score([p for p in ps if row_by_key[(p['market'], p['anchor'])][field] == value]), saved[name])
    gates = {}
    for candidate, baseline in m['comparisons']:
        gains = [recomputed[(f['fold']['name'], baseline)]['joint_log_loss'] - recomputed[(f['fold']['name'], candidate)]['joint_log_loss'] for f in result['results'][:-1]]
        forward = result['results'][-1]['models']
        fg = [forward[baseline]['per_market'][mid]['joint_log_loss'] - s['joint_log_loss'] for mid, s in forward[candidate]['per_market'].items()]
        c, b = (recomputed[('FORWARD_TRAIN2', model)] for model in (candidate, baseline))
        passed = sum(v > 0 for v in gains) >= 6 and statistics.median(gains) > 0 and statistics.median(fg) > 0 and c['joint_log_loss'] < b['joint_log_loss'] and all(c['marginal_brier'][k] <= b['marginal_brier'][k] for k in ('weak', 'strong', 'both'))
        key = candidate + '_vs_' + baseline
        assert passed == (result['comparisons'][key]['verdict'] == 'SUPPORTED_INFORMATION_CLUE')
        gates[key] = dict(passed=passed, lomo_wins=sum(v > 0 for v in gains), forward_wins=sum(v > 0 for v in fg), forward_relative_log_loss_reduction=(b['joint_log_loss'] - c['joint_log_loss']) / b['joint_log_loss'])
    report = dict(status='PASS', fit=False, native=False, rows=len(rows), predictions=len(predictions),
        source_hashes=True, original_rows_preserved=True, source_inventory_labels_and_parent_metadata=True,
        saved_scores_and_gates_recomputed=True, clock_audit=dict(availability), gates=gates,
        result_sha256=sha(RETURN / 'result.json'), predictions_sha256=sha(RETURN / 'predictions.json'),
        note='Event-time retrospective Target anatomy only. Observer receipt is not Target private receipt; no online availability or causal submission claim.')
    (RESEARCH / (STEM + '_VERIFICATION.json')).write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
