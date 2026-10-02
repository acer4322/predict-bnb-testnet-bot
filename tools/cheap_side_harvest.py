"""Pre-registered 'cheap-side harvest' (long-volatility, Target-like pair-cost idea, taker): every 2 s buy 15 sh of EACH side whose ask <= c (both sides eligible, so a flip-and-return buys both cheaply), per-side cap, window [t0,t1].
Grid c{.25,.30,.35,.40,.45} x side cap{60,120,300} x t0{12,40} x t1{230,290} = 60 configs.  Selection on both assets' DISCOVERY (first 60%): EACH + mean>0 on both; rank by min t; top-1 judged on both CONFIRMATIONS (next 25%).  ETH final 15% untouched; BTC samples burned (disclosed).
usage: python tools/cheap_side_harvest.py ETH_CACHE.pkl BTC_CACHE.pkl"""
import sys, pickle, itertools, math, statistics as S
E = pickle.load(open(sys.argv[1], 'rb')); B = pickle.load(open(sys.argv[2], 'rb'))
def spl(X): n = len(X[0]); return X[0][:int(.6 * n)], X[0][int(.6 * n):int(.85 * n)]
def run(X, m, c, cap, t0, t1):
    A = X[1][m]; w = X[2][m]; sh = {'UP': 0., 'DOWN': 0.}; pnl = pair_cost = 0.
    for t in range(t0, t1, 2):
        mid, au, ad = A[t]
        for s, p in (('UP', au), ('DOWN', ad)):
            if p <= c and sh[s] < cap: sh[s] += 15; pnl += 15 * ((1. if s == w else 0.) - min(.99, p))
    return pnl
def each(X, pn, sub):
    mn = {k: S.fmean([pn[m] for m in sub if X[3][m] == k] or [0.]) for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    neg = [k for k, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; return mn, (not neg or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos)))
tst = lambda x: S.fmean(x) / (S.pstdev(x) / math.sqrt(len(x)) + 1e-9)
SP = {'ETH': (E,) + spl(E), 'BTC': (B,) + spl(B)}; res = []
for g in itertools.product((.25, .30, .35, .40, .45), (60, 120, 300), (12, 40), (230, 290)):
    r = {}
    for nm, (X, D, C) in SP.items():
        pn = {m: run(X, m, *g) for m in X[0]}; mnD, oD = each(X, pn, D); mnC, oC = each(X, pn, C); xD = [pn[m] for m in D]; xC = [pn[m] for m in C]
        r[nm] = dict(okD=oD and S.fmean(xD) > 0, tD=tst(xD), mD=S.fmean(xD), mnD=mnD, okC=oC and S.fmean(xC) > 0, mC=S.fmean(xC), mnC=mnC)
    res.append((g, r))
f = lambda mn: {k[:2] + '_' + k[-4:]: round(v, 1) for k, v in mn.items()}
print('configs', len(res), 'DISC pass: ETH %d BTC %d both %d' % (sum(r['ETH']['okD'] for g, r in res), sum(r['BTC']['okD'] for g, r in res), sum(r['ETH']['okD'] and r['BTC']['okD'] for g, r in res)))
for g, r in sorted(res, key=lambda gr: -min(gr[1]['ETH']['tD'], gr[1]['BTC']['tD']))[:4]: print(' ', g, {nm: (round(v['mD'], 1), round(v['tD'], 2), f(v['mnD']), v['okD']) for nm, v in r.items()})
pas = sorted([gr for gr in res if gr[1]['ETH']['okD'] and gr[1]['BTC']['okD']], key=lambda gr: -min(gr[1]['ETH']['tD'], gr[1]['BTC']['tD']))
if not pas: print('no config passes both discovery samples -> confirmation not consulted')
for i, (g, r) in enumerate(pas[:3]): print('CONFIRMATION', '(JUDGED top-1)' if i == 0 else '(descriptive)', g, {nm: (round(v['mC'], 1), f(v['mnC']), 'PASS' if v['okC'] else 'fail') for nm, v in r.items()})
