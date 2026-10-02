"""Read frozen v29 admission attempts; no policy imports or replay.

Counts describe the observed FULL trajectory, not counterfactual fills.
The reserve categories are mutually exclusive, in the order below.
"""
import collections
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'data/research/lan_worker_returns/btc5m-v12g-native-admission-probe-20260927-v29/arms'
MARKETS = [2628553, 2628769, 2629133, 2629199, 2628653,
           2628410, 2629019, 2628557, 2628721, 2628999]
EPS = 1e-8
totals = collections.Counter()
origins = collections.defaultdict(collections.Counter)
per_market = []
for market in MARKETS:
    source = BASE / f'v12g29_NATIVE_GATE_ON_{market}/restoration_trace.json.gz'
    raw = source.read_bytes()
    trace = json.loads(gzip.decompress(raw))
    counts = collections.Counter()
    for row in trace['admissions']:
        counts['attempts'] += 1
        origins[row['origin']]['attempts'] += 1
        assert row['allowed'] == (row['margin_ok'] and row['reserve_ok'])
        if row['allowed']:
            counts['allowed'] += 1
            continue
        counts['rejected'] += 1
        origins[row['origin']]['rejected'] += 1
        if not row['margin_ok']:
            counts['margin_failed'] += 1
            label = 'margin_negative_worst_margin' if row['worst_margin'] < 0 else 'margin_nonnegative_worst_margin'
            counts[label] += 1
        if not row['reserve_ok']:
            counts['reserve_failed'] += 1
            floor = 0.25 * row['peak_G']
            if row['G'] <= EPS:
                label = 'reserve_G_nonpositive'
            elif row['G'] < floor - EPS:
                label = 'reserve_positive_G_below_floor'
            elif row['worst_G'] < floor - EPS:
                label = 'reserve_pending_exhausts_headroom'
            else:
                label = 'reserve_candidate_exceeds_headroom'
            counts[label] += 1
        if not row['margin_ok'] and not row['reserve_ok']:
            counts['both_failed'] += 1
    totals.update(counts)
    per_market.append({'market': market, 'source': source.relative_to(ROOT).as_posix(),
                       'sha256': hashlib.sha256(raw).hexdigest(), 'counts': dict(counts)})
parent = ROOT / 'data/research/lan_worker_returns/btc5m-v12g-active-repair4-small-20260927-v28'
baseline_checks = []
for market in (2628553, 2628769):
    sources = [parent / f'arms/v12g28_AR4_R25_{market}/result.json.gz',
               BASE / f'v12g29_BASE_OFF_CHECK_{market}/result.json.gz']
    records = [json.loads(gzip.decompress(p.read_bytes())) for p in sources]
    equal = records[0]['safety_gate'] == records[1]['safety_gate']
    assert equal
    baseline_checks.append({'market': market, 'safety_gate_equal': equal,
                            'failed_checks': [k for k, v in records[0]['safety_gate'].items()
                                              if k != 'pass' and not v],
                            'sources': [{'path': p.relative_to(ROOT).as_posix(),
                                         'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                        for p in sources]})
groups = {}
raw_groups = {}
for label, path, arm in [('ON', BASE.parent / 'ROWS.json', 'NATIVE_GATE_ON'),
                         ('OFF', parent / 'ROWS.json', 'AR4_R25')]:
    rows = {r['market']: r for r in json.loads(path.read_text(encoding='utf-8'))
            if r['arm'] == arm and r['market'] in MARKETS}
    assert set(rows) == set(MARKETS)
    raw_groups[label] = rows
    groups[label] = {'N': len(rows),
                     'mean_cost': sum(r['cost'] for r in rows.values()) / len(rows),
                     'sum_P': sum(max(r['UP'], 0) + max(r['DOWN'], 0) for r in rows.values()),
                     'sum_L': sum(max(-r['UP'], 0) + max(-r['DOWN'], 0) for r in rows.values()),
                     'rows_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
result = {
    'scope': 'frozen FULL v29, 10 consumed markets, call-attempt weighted',
    'not_a_counterfactual': True,
    'reserve_floor': '0.25 * inherited global peak_G; not a new threshold',
    'limitations': ['Repeated calls may describe similar states; not unique orders.',
                   'Pending can reduce headroom even when it does not solely cross the floor.',
                   'G is a branch payoff, not spendable cash; classifications do not prove a private intent.',
                   'Relaxing a predicate changes later states; these counts cannot predict fills or payoff.'],
    'totals': dict(totals), 'origins': {k: dict(v) for k, v in origins.items()},
    'per_market': per_market,
    'baseline_failure_subcheck_parity': baseline_checks,
    'unrounded_row_aggregates': groups,
    'pooled_cost_ratio': groups['ON']['mean_cost'] / groups['OFF']['mean_cost'],
    'mean_market_cost_ratio': sum(raw_groups['ON'][m]['cost'] / raw_groups['OFF'][m]['cost']
                                  for m in MARKETS) / len(MARKETS),
}
out = Path(__file__).with_name('GATE_CHECK.json')
out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'totals': result['totals'], 'origins': result['origins']}, indent=2))
