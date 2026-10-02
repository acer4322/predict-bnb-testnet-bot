"""Minimum order = 1 USDT NOTIONAL (user, 2026-10-02), not a share count.  v1 (spot vol >= q.50 -> UNDERDOG, else FAV freeze) scaled by s with each order = max(s*15 shares, 1.0/price shares) (rounded up to 0.01 share), cap = s*300 shares.
Reports per scale: the share of orders that the minimum notional forces above the proportional size (distortion), PnL per 300-share-equivalent by class and overall, descriptors (orders per market, mean second of an order, <60 s share, avg price, cap-hit share),
correlation with the ORIGINAL scale, and the capital: mean cost per traded market and worst single-market loss in USD.   usage: python tools/v1_min_notional_scaling.py PUBLIC_ROOT LABELS.json btc_1s.json.gz"""
import sys, math, statistics as S
import v1_scaling_check as V  # data + helpers (prints its own table first)
M, ids, CL, RV, TH, A_ = V.M, V.ids, V.CL, V.RV, V.TH, V.A_
def clipq(s, p, raw): return max(raw * s, math.ceil(100. / max(p, .01)) / 100.)
def fav(m, s, lo=.55, hi=.70, thr=.40):
    A = A_[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if Fv == 'UP' else 2; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]; sh = net = 0.; stopped = False; b = []; forced = 0
    for t in range(12, 290, 2):
        x = fm(t)
        if not stopped and t > 12 and x <= thr: stopped = True
        if stopped or not (lo <= x <= hi) or sh >= 300 * s - 1e-9: continue
        p = min(.99, A[t][qi][0]); q = clipq(s, p, 15.); forced += q > 15 * s + 1e-9; q = min(q, max(300 * s - sh, 1. / p)); sh += q; net += q * p; b.append((t, q, p))
    return (sh if M[m]['win'] == Fv else 0.) - net, b, forced, net
def under(m, s, lo=.75):
    A = A_[m]; w = M[m]['win']; n = pnl = cost = 0.; b = []; forced = 0
    for t in range(12, 290, 2):
        if n >= 300 * s - 1e-9: break
        mid = A[t][0]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if cm >= lo:
            u = 'DOWN' if cur == 'UP' else 'UP'; p = min(.99, A[t][2][0] if u == 'DOWN' else A[t][1][0]); q = clipq(s, p, 15.); forced += q > 15 * s + 1e-9; q = min(q, max(300 * s - n, 1. / p)); n += q; cost += q * p; pnl += q * ((1. if u == w else 0.) - p); b.append((t, q, p))
    return pnl, b, forced, cost
def v1(m, s): return under(m, s) if RV[m] >= TH else fav(m, s)
SC = [1., .5, .3, .2, .1]; res = {s: {m: v1(m, s) for m in ids} for s in SC}; base = {m: res[1.][m][0] for m in ids}
def corr(a, b):
    ma, mb = S.fmean(a), S.fmean(b); c = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b); return c / math.sqrt(va * vb) if va * vb else float('nan')
print('\nscale | forced-above-proportional orders | per-300sh equivalent noflip false true | overall | orders/mkt mean sec <60s px cap-hit | corr w/ original | mean cost USD/mkt | worst market USD | EACH')
for s in SC:
    r = res[s]; k = 1. / s; pn = {m: r[m][0] * k for m in ids}; g = {c: [pn[m] for m in ids if CL[m] == c] for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {c: S.fmean(v) for c, v in g.items()}
    neg = [c for c, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; ok = len(neg) == 0 or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos))
    allb = [x for m in ids for x in r[m][1]]; qt = sum(q for _, q, _ in allb); forced = sum(r[m][2] for m in ids); nord = sum(len(r[m][1]) for m in ids)
    print('x%.1f | %4.0f%% | %6.1f %6.1f %6.1f | %5.1f | %4.1f %4.0f %3.0f%% %.3f %3.0f%% | %.3f | %6.1f | %7.1f | %s' % (s, 100 * forced / max(1, nord), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], S.fmean(pn.values()), nord / len(ids), sum(t * q for t, q, _ in allb) / qt,
          100 * sum(q for t, q, _ in allb if t < 60) / qt, sum(p * q for _, q, p in allb) / qt, 100 * S.fmean(1. if sum(q for _, q, _ in r[m][1]) >= 300 * s - 1e-6 else 0. for m in ids),
          corr([pn[m] for m in ids], [base[m] for m in ids]), S.fmean(r[m][3] for m in ids if r[m][1]), min(r[m][0] for m in ids), 'PASS' if ok else 'fail'))
# which leg is binding?
for leg, f in (('FAV leg (low vol)', fav), ('UNDERDOG leg (high vol)', under)):
    for s in (.5, .3, .2, .1):
        ms = [m for m in ids if (RV[m] < TH) == (leg.startswith('FAV'))]; fo = sum(f(m, s)[2] for m in ms); no = sum(len(f(m, s)[1]) for m in ms)
        print('  %-24s x%.1f: orders forced above proportional size %4.0f%% (min shares for $1 at p=.62: %.2f, at p=.25: %.1f, at p=.10: %.0f)' % (leg, s, 100 * fo / max(1, no), 1 / .62, 1 / .25, 1 / .10))
