"""Spot-volatility regime switch (pre-declared 4 configs): feature in {rv_30m, rv_5m} (previous 30 min / 5 min realized vol of BTCUSDT 1 s returns, known at window start), threshold = discovery quantile q in {.50,.67}.
If feature >= threshold -> UNDERDOG harvest (buy the non-favourite while the favourite mid >= .75, 15 sh / 2 s, cap 300, stop 290, taker) else FAV (band .55-.70, buy-freeze .40, cap 300).
Walk-forward: thresholds from the first 60% (by id), evaluation on the last 40%; no refit.  Reports overall mean with market-bootstrap CI, class means, the generalized EACH test (exactly one losing class, the others positive,
|loss| < min of the positives) and the share of UNDERDOG mode; pure strategies on the same markets for reference."""
import sys, random, statistics as S
import spot_regime_auc as R
F, ids, CL = R.F, R.ids, R.CL
fav, ud, feat = R.fav, R.ud, R.feat; D, T = R.D, R.T; rng = random.Random(9)
def qv(name, q): v = sorted(feat[m][name] for m in D if feat[m][name]); return v[int(q * (len(v) - 1))]
def evaluate(pn, sub):
    g = {k: [pn[m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}
    neg = [k for k, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; ok = len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos)
    return mn, ok, S.fmean(pn[m] for m in sub)
def line(tag, pn, sub, mode=None):
    mn, ok, ov = evaluate(pn, sub); bs = sorted(S.fmean(rng.choice([pn[m] for m in sub]) for _ in sub) for _ in range(400))
    print('%-28s n=%3d%s | noflip %6.1f false %6.1f true %6.1f | overall %5.1f [%5.1f,%5.1f] | EACH %s' % (tag, len(sub), (' UD %3.0f%%' % (100 * sum(mode[m] for m in sub) / len(sub))) if mode else '        ', mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], ov, bs[10], bs[389], 'PASS' if ok else 'fail'))
print('TEST markets (last 40%%): %d' % len(T)); line('FAV only', fav, T); line('UNDERDOG only', ud, T)
for name in ('rv_30m', 'rv_5m'):
    for q in (.50, .67):
        th = qv(name, q); mode = {m: bool(feat[m][name] and feat[m][name] >= th) for m in ids}; pn = {m: ud[m] if mode[m] else fav[m] for m in ids}
        line('%s>=q%.2f -> UNDERDOG' % (name, q), pn, T, mode)
print('\nsame rule on the DISCOVERY half (in-sample, for reference)')
for name in ('rv_30m', 'rv_5m'):
    for q in (.50, .67):
        th = qv(name, q); mode = {m: bool(feat[m][name] and feat[m][name] >= th) for m in ids}; pn = {m: ud[m] if mode[m] else fav[m] for m in ids}
        line('%s>=q%.2f -> UNDERDOG' % (name, q), pn, D, mode)
