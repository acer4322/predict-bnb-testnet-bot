"""Target's TAKER fills earn +2 c at +5 s (they buy right before the Predict price moves).  Does the Predict BTC5M price LAG Binance spot at the 1-3 s horizon?
(1) correlation of spot 1 s log return at second s with Predict UP-mid change over second s+lag (lag -2..+5), pooled, 474 BTC markets (1 Hz books).
(2) taker rule (pre-declared): at second t (20..280), if spot moved >= x bp over the last k s toward UP (DOWN), buy UP (DOWN) at the ask quoted at t+1 s (our ~1 s reaction incl. latency); markout = side mid(t+1+h) - ask, h in {5,30}, and edge to resolution; one trade per signal second, min 5 s between trades per market.
Discovery = first 60% of markets by id, confirmation = next 25% (last 15% kept back).   usage: python tools/spot_predict_lag.py BTC_CACHE.pkl BTC_PUBLIC_ROOT spot1s.json.gz [more]"""
import sys, pickle, json, gzip, math, statistics as S, random
bc, root, *spf = sys.argv[1:]; ids, A_, WIN, CL = pickle.load(open(bc, 'rb'))
sp = {}
for f in spf: sp.update({int(k): v[0] for k, v in json.load(gzip.open(f, 'rt')).items()})
WS = {m: int(json.load(gzip.open('%s/%d/public_%d.json.gz' % (root, m, m), 'rt'))['market']['window_start_ms']) // 1000 for m in ids}
ids = [m for m in ids if sp.get(WS[m]) and sp.get(WS[m] + 290)]; print('markets with spot coverage', len(ids))
def lr(a, b):
    x, y = sp.get(a), sp.get(b); return math.log(y / x) * 1e4 if x and y else None
xs = {l: ([], []) for l in range(-2, 6)}
for m in ids:
    w = WS[m]; A = A_[m]
    for s in range(15, 280):
        r = lr(w + s - 1, w + s)
        if r is None: continue
        for l in xs:
            if 1 <= s + l <= 289: xs[l][0].append(r); xs[l][1].append(A[s + l][0] - A[s + l - 1][0])
def corr(a, b):
    ma, mb = S.fmean(a), S.fmean(b); return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
print('corr(spot ret second s, Predict UP-mid change second s+lag):', {l: round(corr(*xs[l]), 3) for l in xs})
n = len(ids); D, C = ids[:int(.6 * n)], ids[int(.6 * n):int(.85 * n)]; rng = random.Random(5)
def rule(sub, k, x):
    pm = []
    for m in sub:
        w = WS[m]; A = A_[m]; last = -99; e5 = []; e30 = []; er = []
        for t in range(20, 280):
            if t - last < 5: continue
            r = lr(w + t - k, w + t)
            if r is None or abs(r) < x: continue
            side = 'UP' if r > 0 else 'DOWN'; a = A[t + 1][1] if side == 'UP' else A[t + 1][2]; a = min(.99, a)
            mid = lambda tt: A[min(tt, 290)][0] if side == 'UP' else 1 - A[min(tt, 290)][0]
            e5.append(mid(t + 6) - a); e30.append(mid(t + 31) - a); er.append((1. if WIN[m] == side else 0.) - a); last = t
        if er: pm.append((S.fmean(e5), S.fmean(e30), S.fmean(er), len(er)))
    return pm
def ci(v): b = sorted(S.fmean(rng.choice(v) for _ in v) for _ in range(1000)); return b[25], b[974]
print('\ntaker on spot move (k s, >= x bp): per-market mean edge per share  [market bootstrap CI]')
for k in (1, 2, 3):
    for x in (1., 2., 3., 5.):
        row = []
        for nm, sub in (('DISC', D), ('CONF', C)):
            pm = rule(sub, k, x)
            if len(pm) < 10: row.append('%s n=%d' % (nm, len(pm))); continue
            r5 = [p[0] for p in pm]; rr_ = [p[2] for p in pm]; l5, h5 = ci(r5); lr_, hr = ci(rr_)
            row.append('%s mk=%d trades/mkt %.1f +5s %+.4f[%+.4f,%+.4f] res %+.4f[%+.4f,%+.4f]' % (nm, len(pm), S.fmean(p[3] for p in pm), S.fmean(r5), l5, h5, S.fmean(rr_), lr_, hr))
        print('  k=%d x=%.0f | %s' % (k, x, ' || '.join(row)))
