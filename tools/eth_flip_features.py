"""ETH5M tasks 1+2 (pre-registered; DISCOVERY 60% / CONFIRMATION 25% / FINAL 15% untouched).
Task 1: FAV + underdog-side hedge: when favourite mid <= h after t>12, buy k shares of the other side (once). Grid h x k; selected on discovery by EACH + overall>0; confirmation judges top-1.
Task 2: flip-prediction features at t=12 (ETH & BTC spot 1 s, taker-buy flow); AUC for flip and for TRUE-vs-FALSE flip on discovery | confirmation; then FAV gated by feature (direction & quantile from discovery), judged same way.
usage: python tools/eth_flip_features.py CACHE.pkl PUBLIC_ROOT eth_1s.json.gz btc_1s.json.gz [btc_extra.json.gz ...]"""
import sys, json, gzip, math, bisect, pickle, pathlib, random, itertools, statistics as S
cache, root, ethf, *btcf = sys.argv[1:]
ids, A_, WIN, CL = pickle.load(open(cache, 'rb'))
WS = {m: int(json.load(gzip.open(root + '/public_markets/%d/public_%d.json.gz' % (m, m), 'rt'))['market']['window_start_ms']) // 1000 for m in ids}
eth = {int(k): v for k, v in json.load(gzip.open(ethf, 'rt')).items()}; btc = {}
for f in btcf: btc.update({int(k): v for k, v in json.load(gzip.open(f, 'rt')).items()})
n = len(ids); i1, i2 = int(.6 * n), int(.85 * n); D, C = ids[:i1], ids[i1:i2]; rng = random.Random(5)
def each(pn, sub):
    mn = {c: S.fmean([pn[m] for m in sub if CL[m] == c] or [0.]) for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    neg = [c for c, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]
    return mn, (not neg or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos)))
def ci(x): b = sorted(S.fmean(rng.choice(x) for _ in x) for _ in range(500)); return b[12], b[487]
def rep(tag, pn, sub):
    mn, ok = each(pn, sub); x = [pn[m] for m in sub]; lo, hi = ci(x); return '%s n=%d overall %.1f CI[%.1f,%.1f] class %s EACH %s' % (tag, len(sub), S.fmean(x), lo, hi, {k: round(v, 1) for k, v in mn.items()}, 'PASS' if ok else 'fail')
def fav(m, hedge=None, lo=.55, hi=.70, thr=.40, cap=300., tick=15.):
    A = A_[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]; sh = net = 0.; stopped = False; hedged = 0.; hc = 0.
    for t in range(12, 290, 2):
        x = fm(t)
        if hedge and not hedged and t > 12 and x <= hedge[0]:
            p = min(.99, A[t][2] if Fv == 'UP' else A[t][1]); hedged = hedge[1]; hc = hedged * p
        if not stopped and t > 12 and x <= thr: stopped = True
        if stopped or not (lo <= x <= hi) or sh >= cap: continue
        sh += tick; net += tick * min(.99, A[t][1] if Fv == 'UP' else A[t][2])
    w = WIN[m]; return (sh if w == Fv else 0.) - net + ((hedged if w != Fv else 0.) - hc)
FA = {m: fav(m) for m in ids}
print('=== TASK 1: FAV + hedge (no-hedge baseline) ===\n', rep('baseline DISC', FA, D), '\n', rep('baseline CONF', FA, C))
res = []
for h, k in itertools.product((.45, .40, .35, .30, .25), (15., 30., 60., 90., 150.)):
    pn = {m: fav(m, (h, k)) for m in D}; mn, ok = each(pn, D); x = list(pn.values()); res.append(((h, k), ok, S.fmean(x), S.fmean(x) / (S.pstdev(x) / math.sqrt(len(x))), mn))
print('configs', len(res), 'DISC EACH pass', sum(r[1] for r in res)); [print('  ', r[0], 'EACH' if r[1] else 'no', 'mean %.1f t %.2f %s' % (r[2], r[3], {a: round(b, 1) for a, b in r[4].items()})) for r in sorted(res, key=lambda r: -r[3])[:4]]
pas = sorted([r for r in res if r[1] and r[2] > 0], key=lambda r: -r[3])
if pas: g = pas[0][0]; print(rep('CONF top-1 %s' % (g,), {m: fav(m, g) for m in C}, C))
else: print('no hedge config passes discovery -> confirmation not consulted')
print('\n=== TASK 2: flip features ===')
def at(d, s, i): return d[s][i] if s in d else None
def lr(d, a, b):
    x, y = at(d, a, 0), at(d, b, 0); return math.log(y / x) if x and y else None
def feats(m):
    A = A_[m]; Fv = 1 if A[12][0] >= .5 else -1; w = WS[m]; f = {}
    f['fav_mid12'] = max(A[12][0], 1 - A[12][0])
    for nm, d in (('eth', eth), ('btc', btc)):
        r12 = lr(d, w, w + 12); r60 = lr(d, w - 60, w); r300 = lr(d, w - 300, w)
        f[nm + '_ret12'] = None if r12 is None else Fv * r12; f[nm + '_pre60'] = None if r60 is None else Fv * r60; f[nm + '_pre300'] = None if r300 is None else Fv * r300
    f['lead12'] = None if f['btc_ret12'] is None or f['eth_ret12'] is None else f['btc_ret12'] - f['eth_ret12']
    tb = [eth[s] for s in range(w - 60, w + 12) if s in eth and len(eth[s]) > 3 and eth[s][1] > 0]
    f['eth_tb'] = None if not tb else Fv * (sum(x[3] for x in tb) / sum(x[1] for x in tb) - .5)
    rr_ = [lr(eth, s - 1, s) for s in range(w - 299, w + 1)]; rr_ = [x for x in rr_ if x is not None]; f['eth_rv'] = S.pstdev(rr_) if len(rr_) > 30 else None
    return f
F = {m: feats(m) for m in ids}
def auc(xs, ys):
    p = [x for x, y in zip(xs, ys) if y]; q = [x for x, y in zip(xs, ys) if not y]
    return sum((a > b) + .5 * (a == b) for a in p for b in q) / (len(p) * len(q)) if p and q else float('nan')
names = list(F[ids[0]]); print('feature | AUC(flip) disc|conf | AUC(TRUE vs FALSE among flips) disc|conf | n')
AUCD = {}
for nm in names:
    row = []
    for tg in ('flip', 'true'):
        for sub in (D, C):
            z = [(F[m][nm], (CL[m] != 'NO_FLIP') if tg == 'flip' else (CL[m] == 'TRUE_FLIP')) for m in sub if F[m][nm] is not None and (tg == 'flip' or CL[m] != 'NO_FLIP')]
            row.append(auc([a for a, _ in z], [b for _, b in z]))
    AUCD[nm] = row; print('  %-10s %.3f|%.3f  %.3f|%.3f' % (nm, *row))
print('\nGated FAV (trade only if feature on the safe side vs discovery quantile q; direction from discovery AUC(flip)):')
res = []
for nm in names:
    if nm == 'fav_mid12': pass
    dirn = 1 if AUCD[nm][0] >= .5 else -1
    vals = sorted(F[m][nm] for m in D if F[m][nm] is not None)
    for q in (.3, .5, .7):
        th = vals[int(q * (len(vals) - 1))]
        safe = lambda m: F[m][nm] is not None and ((F[m][nm] <= th) if dirn > 0 else (F[m][nm] >= th))
        pn = {m: (FA[m] if safe(m) else 0.) for m in D}; mn, ok = each(pn, D); x = list(pn.values()); t = S.fmean(x) / (S.pstdev(x) / math.sqrt(len(x)) + 1e-9)
        res.append(((nm, q, dirn, th), ok, S.fmean(x), t, mn))
print('configs', len(res), 'DISC EACH pass', sum(r[1] for r in res)); [print('  ', r[0], 'EACH' if r[1] else 'no', 'mean %.1f t %.2f %s' % (r[2], r[3], {a: round(b, 1) for a, b in r[4].items()})) for r in sorted(res, key=lambda r: -r[3])[:4]]
pas = sorted([r for r in res if r[1] and r[2] > 0], key=lambda r: -r[3])
if pas:
    nm, q, dirn, th = pas[0][0]; safe = lambda m: F[m][nm] is not None and ((F[m][nm] <= th) if dirn > 0 else (F[m][nm] >= th)); print(rep('CONF top-1 %s' % (pas[0][0],), {m: (FA[m] if safe(m) else 0.) for m in C}, C))
else: print('no gate passes discovery -> confirmation not consulted')
