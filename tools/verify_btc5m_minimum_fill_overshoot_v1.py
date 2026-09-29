"""Independent float/grouping reconstruction of the saved Decimal fill audit."""
import collections
import json
import math
from audit_btc5m_target_core_loop_topology_v1 import ROOT, R, read, sha

STEM = 'BTC5M_MINIMUM_FILL_OVERSHOOT_V1_20260913'


def main():
    report_path = R / (STEM + '.json')
    d = read(report_path)
    eps = d['dollar_comparison_tolerance']
    total = collections.Counter()
    markets = []
    for src in d['sources']:
        p = ROOT / src['path']
        assert sha(p) == src['sha256']
        raw = read(p)
        groups = collections.defaultdict(list)
        times = collections.defaultdict(list)
        for a in raw['targetActions']:
            groups[(a['role'], a['side'], a['order_hash'])].append(a)
            times[a['event_ms']].append(a)
        expected = next(m for m in d['markets'] if m['market'] == raw['market']['market_id'])
        count = collections.Counter()
        for parent in expected['parent_rows']:
            legs = groups[(parent['role'],parent['side'],parent['order_hash'])]
            cash = math.fsum(a['shares']*a['price'] for a in legs)
            assert math.isclose(cash,parent['observed_cash'],abs_tol=1e-7)
            assert math.isclose(math.fsum(a['shares'] for a in legs),parent['observed_shares'],abs_tol=1e-7)
            assert len(legs) == parent['fill_legs']
            if parent['role'] != 'MAKER':
                continue
            small = sum(a['shares']*a['price'] < 1-eps for a in legs)
            count['legs'] += len(legs)
            count['parents'] += 1
            count['subdollar_legs'] += small
            count['subdollar_legs_with_parent_observed_cash_ge_one'] += small if cash >= 1-eps else 0
            count['dollar_boundary_legs'] += sum(abs(a['shares']*a['price']-1) <= eps for a in legs)
        assert all(expected['stats']['MAKER'][k] == v for k,v in count.items())
        total += count
        up = down = 0.
        crossings = []
        for t, legs in sorted(times.items()):
            before = up-down
            up += math.fsum(a['shares'] for a in legs if a['side']=='UP')
            down += math.fsum(a['shares'] for a in legs if a['side']=='DOWN')
            after = up-down
            if abs(before)>eps and before*after < -eps:
                crossings.append(t)
        assert crossings == [r['t'] for r in expected['crossings']]
        assert math.isclose(up,expected['final_inventory']['UP'],abs_tol=1e-7)
        assert math.isclose(down,expected['final_inventory']['DOWN'],abs_tol=1e-7)
        markets.append(dict(market=expected['market'],crossings=len(crossings),maker_checks=dict(count)))
    assert all(d['stats']['MAKER'][k] == v for k,v in total.items())
    assert sum(m['crossings'] for m in markets) == d['crossings']['total']
    out = dict(status='PASS', source_artifact_sha256=sha(report_path), markets=markets,
               checks=['All source hashes and parent cash/share/leg totals verified.',
                       'Independent float maker leg counts agree with Decimal by market and pooled.',
                       'Independent cumulative inventory signs reproduce every crossing timestamp.',
                       'Dollar boundary tolerance1e-8 separates a serialized0.9999999999999999 leg from genuinely sub-dollar legs.'],
               fit=False,native=False)
    (R / (STEM + '_VERIFICATION.json')).write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(status='PASS',maker_totals=dict(total),crossings=sum(m['crossings'] for m in markets))))


if __name__ == '__main__':
    main()
