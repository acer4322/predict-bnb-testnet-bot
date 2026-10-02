"""Design of the small-amount test mode (no parameter tuning): statistical power, capital and fee drag per candidate and per scale.  Inputs: engine per-market results for FAV_TAKER / UNDER_TAKER on 185 markets (docs/research_specs/results/CLOUD_LAB_RESULTS_20261002.json,
real events, true labels) and the 5-minute spot volatility table.  Candidates: FAV, UNDERDOG, v1 (rv_5m>=3.1834e-05 -> UNDERDOG else FAV), v2 (rv>=th -> UNDERDOG else idle).  PnL is linear in size, so the t-statistic of a mean does not depend on the scale:
SE(n, s) = s * sd / sqrt(n).  Reports mean/sd per market at full scale, the net mean after a fee model (per share min(p,1-p) x 200 bps, local model, NOT verified against the deployed contract), the sample size needed to detect (80% power, one-sided 5%) the
observed in-sample mean and fixed effects of +5 and +10 per market at full scale, days of continuous 5-minute trading (288 markets per day), and the capital per market / worst market at scales .3 and .2.
usage: python tools/small_test_design.py CLOUD_LAB_RESULTS.json SPOT_RV5_TABLE.json"""
import sys, json, math, statistics as S
lab = json.load(open(sys.argv[1])); rv = {int(k): v['rv_5m'] for k, v in json.load(open(sys.argv[2])).items()}
fav = {r['market']: r for r in lab['FAV_TAKER']}; ud = {r['market']: r for r in lab['UNDER_TAKER']}; ms = sorted(m for m in fav if m in ud and m in rv); TH = 3.1834e-05; Z = 1.645 + .8416
def fee(r):
    sh = r['up'] + r['dn']; return 0. if sh <= 0 else sh * min(r['cost'] / sh, 1 - r['cost'] / sh) * .02
cand = {'FAV': lambda m: (fav[m]['pnl'], fee(fav[m]), fav[m]['cost']), 'UNDERDOG': lambda m: (ud[m]['pnl'], fee(ud[m]), ud[m]['cost']),
        'v1 (low vol FAV)': lambda m: (ud[m]['pnl'], fee(ud[m]), ud[m]['cost']) if rv[m] >= TH else (fav[m]['pnl'], fee(fav[m]), fav[m]['cost']),
        'v2 (low vol idle)': lambda m: (ud[m]['pnl'], fee(ud[m]), ud[m]['cost']) if rv[m] >= TH else (0., 0., 0.)}
print('markets', len(ms), '(engine, real events, true labels)')
print('%-18s | mean pnl | fee | net mean | sd | n for in-sample net mean | n for +5/mkt | n for +10/mkt | days (288/day) for +5 | cost/mkt x.3 x.2 | worst x.3 x.2' % 'candidate')
for k, f in cand.items():
    pn = [f(m)[0] for m in ms]; fe = [f(m)[1] for m in ms]; co = [f(m)[2] for m in ms]; mean = S.fmean(pn); net = mean - S.fmean(fe); sd = S.pstdev(pn)
    need = lambda e: (Z * sd / e) ** 2 if e > 0 else float('inf')
    print('%-18s | %7.1f | %4.1f | %7.1f | %5.1f | %s | %6.0f | %6.0f | %5.1f | %5.1f %5.1f | %6.1f %6.1f' % (k, mean, S.fmean(fe), net, sd, ('%8.0f' % need(net)) if net > 0 else '     inf', need(5.), need(10.), need(5.) / 288, .3 * S.fmean(c for c in co if c > 0), .2 * S.fmean(c for c in co if c > 0), .3 * min(pn), .2 * min(pn)))
print('\nSE of the mean per market (USD) for n markets at scale s: s*sd/sqrt(n)')
for k, f in cand.items():
    sd = S.pstdev([f(m)[0] for m in ms]); print('  %-18s sd %.1f -> n=100: x1 %.1f | x.3 %.2f | x.2 %.2f ; n=300: x.3 %.2f ; n=1000: x.3 %.2f' % (k, sd, sd / 10, .3 * sd / 10, .2 * sd / 10, .3 * sd / math.sqrt(300), .3 * sd / math.sqrt(1000)))
