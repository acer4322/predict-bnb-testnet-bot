"""Time-dependent flow rule, direction learned on DISCOVERY only: for each time bin, per-share taker edge of FOLLOWING and of FADING nOFI30 (theta=.3, quotes at t+1 s) on the discovery markets; the better direction per bin is then applied unchanged
to the confirmation markets.  Bins: [30,90), [90,150), [150,210), [210,270].  Per-market mean edge with market bootstrap CI.  Exploratory (the confirmation markets were seen in flow_signal_check/diag); needs new markets to be formal."""
import sys
import flow_signal_check as F
import numpy as np, statistics as S, random
D, C, lab, nofi = F.D, F.C, F.lab, F.nofi; rng = random.Random(4); TH = .3
BINS = [(30, 90), (90, 150), (150, 210), (210, 271)]
def edge(m, ds, lo, hi, sign):
    _, bb, ba, sg, vol = ds[m]; w = 1. if lab[m] == 'UP' else 0.; es = []
    for t in range(lo, hi, 2):
        f = nofi(sg, vol, t, 30)
        if f is None or np.isnan(ba[t + 1]) or np.isnan(bb[t + 1]): continue
        au, ad = min(.99, ba[t + 1]), min(.99, 1 - bb[t + 1]); f *= sign
        if f >= TH: es.append(w - au)
        elif f <= -TH: es.append((1 - w) - ad)
    return S.fmean(es) if es else None
def ci(xs, nb=400): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
print('bin | disc follow | disc fade | chosen | conf edge of chosen direction [CI] (n markets with trades) | conf of the other direction')
tot = []
for lo, hi in BINS:
    r = {}
    for sign in (1, -1):
        xs = [x for x in (edge(m, D, lo, hi, sign) for m in D) if x is not None]; r[sign] = S.fmean(xs) if xs else float('nan')
    ch = 1 if r[1] >= r[-1] else -1
    xc = [x for x in (edge(m, C, lo, hi, ch) for m in C) if x is not None]; xo = [x for x in (edge(m, C, lo, hi, -ch) for m in C) if x is not None]; l, h = ci(xc)
    print('[%3d,%3d) | %+6.2f | %+6.2f | %s | %+6.2f [%+6.2f,%+6.2f] (n=%d) | %+6.2f' % (lo, hi, 100 * r[1], 100 * r[-1], 'follow' if ch == 1 else 'fade', 100 * S.fmean(xc), 100 * l, 100 * h, len(xc), 100 * S.fmean(xo)))
