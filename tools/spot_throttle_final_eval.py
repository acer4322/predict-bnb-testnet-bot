"""ONE-SHOT formal evaluation of the frozen v2 spec (docs/research_specs/SPOT_VOL_THROTTLE_SPEC_20261002_ZH.md): markets with id > 2810203 (batch02 15 + batch03 85 = 100), engine + real events + official labels.
Rule: rv_5m >= 3.1834e-05 -> UNDERDOG (strategy_lab UNDER_TAKER) else idle (PnL 0); alternative q.67 = 3.9759e-05.  PASS = all three class means positive or exactly one losing class with |loss| < min(other two), AND overall mean's
market-bootstrap 95% CI lower bound > 0.  Also reports traded share, worst single market, fee 1%/2% (fee x cost, UNDER_TAKER cost field), and references (FAV only, UNDERDOG only, v1).
usage: python tools/spot_throttle_final_eval.py res_b2.json spot_b2.json.gz events_b2_markets res_b3.json spot_b3.json.gz events_b3_markets"""
import sys, json, gzip, math, pathlib, random, statistics as S, bisect
from toy_graduation import ci_mean
a = sys.argv[1:]; rng = random.Random(77); TH = {'q.50': 3.1834e-05, 'q.67': 3.9759e-05}
def load(res, spotf, evd):
    spot = {int(k): v[0] for k, v in json.load(gzip.open(spotf, 'rt')).items()}; ks = sorted(spot); px = lambda s: spot[ks[bisect.bisect_right(ks, s) - 1]]
    def rv(w0, n=300):
        r = []; prev = px(w0 - n)
        for s in range(w0 - n + 1, w0 + 1): c = px(s); r.append(math.log(c / prev)); prev = c
        return S.pstdev(r)
    ws = {int(p.name): int(json.load(open(p / 'META.json'))['window_start_ms']) // 1000 for p in pathlib.Path(evd).iterdir()}
    r = json.load(open(res)); ud = {x['market']: x for x in r['UNDER_TAKER']}; fv = {x['market']: x for x in r['FAV_TAKER']}
    return [dict(m=m, rv=rv(ws[m]), cls=fv[m]['cls'], fav=fv[m]['pnl'], ud=ud[m]['pnl'], udcost=ud[m]['cost']) for m in sorted(fv)]
rows = load(*a[0:3]) + load(*a[3:6]); print('markets', len(rows), 'ids', min(r['m'] for r in rows), max(r['m'] for r in rows), 'above q.50: %d, above q.67: %d' % (sum(r['rv'] >= TH['q.50'] for r in rows), sum(r['rv'] >= TH['q.67'] for r in rows)))
def ev(tag, pn):
    g = {k: [p for p, r in zip(pn, rows) if r['cls'] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}
    neg = [k for k, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; ok = len(neg) == 0 or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos))
    lo, hi = ci_mean(pn, rng, 2000); verdict = 'PASS' if ok and lo > 0 else 'FAIL'
    print('%-34s | noflip %6.1f false %6.1f true %6.1f (n %d/%d/%d) | overall %6.1f [%6.1f,%6.1f] | classes %s | traded %3d | worst %6.1f | %s' % (tag, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], *[len(g[k]) for k in g], S.fmean(pn), lo, hi, 'ok' if ok else 'no', sum(1 for p in pn if p != 0), min(pn), verdict))
for q, th in TH.items():
    for f in (0., .01, .02): ev('v2 %s idle low vol, fee %.0f%%' % (q, 100 * f), [(r['ud'] - f * r['udcost']) if r['rv'] >= th else 0. for r in rows])
print('references (not the frozen candidate):')
ev('FAV only', [r['fav'] for r in rows]); ev('UNDERDOG only', [r['ud'] for r in rows]); ev('v1 q.50 (FAV in low vol)', [r['ud'] if r['rv'] >= TH['q.50'] else r['fav'] for r in rows])
