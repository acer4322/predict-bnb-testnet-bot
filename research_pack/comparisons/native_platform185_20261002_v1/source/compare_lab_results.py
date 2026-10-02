"""Compare two strategy_lab --out result files (e.g. the cloud Linux build vs the local native Windows engine) market by market.  Reports per strategy: markets in common, exact-match share (|diff| < 1e-6), mean/max |PnL diff|,
shares and cost differences, and the largest differences.   usage: python tools/hft_repro/compare_lab_results.py cloud.json local.json"""
import sys, json
a, b = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
for strat in sorted(set(a) & set(b)):
    ra = {r['market']: r for r in a[strat]}; rb = {r['market']: r for r in b[strat]}; ms = sorted(set(ra) & set(rb))
    d = [(abs(ra[m]['pnl'] - rb[m]['pnl']), m) for m in ms]; ex = sum(x < 1e-6 for x, _ in d)
    print('%s: common %d | exact PnL match %d (%.0f%%) | mean |diff| %.4f | max |diff| %.3f | mean |shares diff| %.4f | mean |cost diff| %.4f' % (strat, len(ms), ex, 100 * ex / max(1, len(ms)), sum(x for x, _ in d) / max(1, len(d)), max((x for x, _ in d), default=0.),
          sum(abs((ra[m]['up'] + ra[m]['dn']) - (rb[m]['up'] + rb[m]['dn'])) for m in ms) / max(1, len(ms)), sum(abs(ra[m]['cost'] - rb[m]['cost']) for m in ms) / max(1, len(ms))))
    for x, m in sorted(d, reverse=True)[:5]: print('   market %d: cloud %.3f local %.3f' % (m, ra[m]['pnl'], rb[m]['pnl']))
