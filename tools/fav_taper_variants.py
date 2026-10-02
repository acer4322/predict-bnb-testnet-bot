"""Pre-registered FAV true-flip-exposure variants (BTC + ETH).  Base = frozen FAV (band .55-.70, 15 sh/2 s from t=12, cap 300 sh, freeze at fav mid <= .40).
A1 time taper: size = 15*max(floor, 1-(t-12)/T), T in {120,200,290}, floor in {0,.25,.5}.  A2 holdings taper: size = 15*max(floor, 1-sh/S0), S0 in {150,300,450}, floor in {0,.25,.5}.
B price weights: size = 15*w[band] for bands [.55,.60),[.60,.65),[.65,.70] with w1 in {0,.5,1}, w2 in {.5,1}, w3 in {1,1.5,2}.   (45 configs)
Selection: per asset, chronological DISCOVERY = first 60%, CONFIRMATION = next 25%.  Candidate must pass generalized EACH and mean>0 on BOTH assets' discovery; rank by min of the two discovery t-stats; ONLY top-1 judged on both confirmations (others descriptive).
BTC samples were burned in earlier work (disclosed); ETH FINAL 15% untouched.  usage: python tools/fav_taper_variants.py ETH_CACHE.pkl BTC_CACHE.pkl"""
import sys, pickle, itertools, math, random, statistics as S
E = pickle.load(open(sys.argv[1], 'rb')); B = pickle.load(open(sys.argv[2], 'rb')); rng = random.Random(9)
def spl(X): n = len(X[0]); return X[0][:int(.6 * n)], X[0][int(.6 * n):int(.85 * n)]
def fav(X, m, kind, a, b, c, thr=.40, cap=300.):
    A = X[1][m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]; sh = net = 0.; stopped = False
    for t in range(12, 290, 2):
        x = fm(t)
        if not stopped and t > 12 and x <= thr: stopped = True
        if stopped or not (.55 <= x <= .70) or sh >= cap: continue
        if kind == 'A1': q = 15. * max(b, 1 - (t - 12) / a)
        elif kind == 'A2': q = 15. * max(b, 1 - sh / a)
        else: q = 15. * (a if x < .60 else b if x < .65 else c)
        if q <= 0: continue
        sh += q; net += q * min(.99, A[t][1] if Fv == 'UP' else A[t][2])
    return (sh if X[2][m] == Fv else 0.) - net
def each(X, pn, sub):
    mn = {c: S.fmean([pn[m] for m in sub if X[3][m] == c] or [0.]) for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    neg = [c for c, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; return mn, (not neg or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos)))
def tst(x): return S.fmean(x) / (S.pstdev(x) / math.sqrt(len(x)) + 1e-9)
cfg = [('B', 1, 1, 1)] + [('A1', T, f, 0) for T in (120, 200, 290) for f in (0, .25, .5)] + [('A2', S0, f, 0) for S0 in (150, 300, 450) for f in (0, .25, .5)] + [('B', a, b, c) for a in (0, .5, 1) for b in (.5, 1) for c in (1, 1.5, 2)]
SP = {'ETH': (E,) + spl(E), 'BTC': (B,) + spl(B)}; res = []
for g in cfg:
    r = {}
    for nm, (X, D, C) in SP.items():
        pn = {m: fav(X, m, *g) for m in X[0]}; mnD, okD = each(X, pn, D); xD = [pn[m] for m in D]; mnC, okC = each(X, pn, C); xC = [pn[m] for m in C]
        r[nm] = dict(okD=okD and S.fmean(xD) > 0, tD=tst(xD), mnD=mnD, mD=S.fmean(xD), okC=okC and S.fmean(xC) > 0, mnC=mnC, mC=S.fmean(xC), tC=tst(xC))
    res.append((g, r))
print('configs', len(cfg)); f = lambda mn: {k[:2] + '_' + k[-4:]: round(v, 1) for k, v in mn.items()}
for g, r in res[:1]: print('BASE (B,1,1,1)', {nm: (round(v['mD'], 1), f(v['mnD']), 'conf', round(v['mC'], 1), f(v['mnC'])) for nm, v in r.items()})
print('DISC EACH pass: ETH %d, BTC %d, both %d' % (sum(r['ETH']['okD'] for g, r in res), sum(r['BTC']['okD'] for g, r in res), sum(r['ETH']['okD'] and r['BTC']['okD'] for g, r in res)))
[print(' best', k, [(g, {nm: (round(v['mD'], 1), f(v['mnD'])) for nm, v in r.items()}) for g, r in sorted([x for x in res if x[0][0] == k], key=lambda gr: -min(gr[1]['ETH']['tD'], gr[1]['BTC']['tD']))[:1]]) for k in ('A1', 'A2')]
print('best by min discovery t (any):'); srt = sorted(res, key=lambda gr: -min(gr[1]['ETH']['tD'], gr[1]['BTC']['tD']))
for g, r in srt[:5]: print(' ', g, {nm: (round(v['mD'], 1), round(v['tD'], 2), f(v['mnD']), v['okD']) for nm, v in r.items()})
pas = [gr for gr in srt if gr[1]['ETH']['okD'] and gr[1]['BTC']['okD']]
if not pas: print('no config passes on both discovery samples -> confirmation not consulted')
else:
    for i, (g, r) in enumerate(pas[:3]): print('CONFIRMATION', '(JUDGED top-1)' if i == 0 else '(descriptive)', g, {nm: (round(v['mC'], 1), f(v['mnC']), 'PASS' if v['okC'] else 'fail') for nm, v in r.items()})
