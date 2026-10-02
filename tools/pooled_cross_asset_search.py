"""Task 3: pooled BTC5M + ETH5M search (pre-registered). A rule is a candidate ONLY if it passes the generalized EACH on BTC (all 519 true-labelled, burned for selection) AND on ETH discovery AND ETH confirmation separately, with overall mean >0 in each; only then would the ETH FINAL 15% be consulted once.
Families: underdog (lo,hi,t0,t1,sizing,cost cap)=864 configs, FAV+hedge (h,k)=25, FAV price band x buy-freeze (lo,hi,thr)=36.  Prints per-family pass counts per sample and the joint count.
usage: python tools/pooled_cross_asset_search.py ETH_CACHE.pkl BTC_CACHE.pkl BTC_PUBLIC_ROOT BTC_LABELS.json"""
import sys, os, pickle, itertools, statistics as S, math
ec, bc, broot, blab = sys.argv[1:5]
if not os.path.exists(bc):
    import real_replay_stop as rr
    lab = {int(r['market_id']): r['winner'] for r in rr.jl(blab)['records']}; M0 = {m: v for m, v in rr.load(broot, lab).items() if v['labelled']}
    def maxgap(v):
        ts = [t for t in v['ts'] if t <= 290] or [0.]; ts = [0. if t < 0 else t for t in ts]; return max([b - a for a, b in zip(ts, ts[1:])] + [290. - ts[-1]])
    M = {m: v for m, v in M0.items() if rr.at(v, 12.) is not None and maxgap(v) <= 40.}; ids = sorted(M); CL = {m: rr.classify(M[m]) for m in ids}; WIN = {m: M[m]['win'] for m in ids}; A_ = {}
    for m in ids:
        mk = M[m]; A = {}
        for t in range(0, 291):
            a = rr.at(mk, float(t)) or mk['bs'][0]; b = rr.at(mk, t + 1.) or a; A[t] = ((a['best_bid'] + a['best_ask']) / 2, rr.quotes(b, 'UP')[0], rr.quotes(b, 'DOWN')[0])
        A_[m] = A
    pickle.dump((ids, A_, WIN, CL), open(bc, 'wb'))
E = pickle.load(open(ec, 'rb')); B = pickle.load(open(bc, 'rb'))
def split(X): ids = X[0]; n = len(ids); return {'ETH_D': ids[:int(.6 * n)], 'ETH_C': ids[int(.6 * n):int(.85 * n)], 'ALL': ids}
SPL = {'ETH': split(E), 'BTC': split(B)}; print('BTC markets', len(B[0]), 'ETH', len(E[0]))
def each(CL, pn, sub):
    mn = {c: S.fmean([pn[m] for m in sub if CL[m] == c] or [0.]) for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    neg = [c for c, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; return mn, (not neg or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos)))
def und(X, m, lo, hi, t0, t1, mode, cc):
    A = X[1][m]; w = X[2][m]; cost = pnl = 0.
    for t in range(t0, t1, 2):
        if cost >= cc: break
        mid, au, ad = A[t]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if not (lo <= cm <= hi): continue
        u = 'DOWN' if cur == 'UP' else 'UP'; p = min(.99, ad if u == 'DOWN' else au); q = 15. if mode == 0 else 2. / max(p, .01); cost += q * p; pnl += q * ((1. if u == w else 0.) - p)
    return pnl
def fav(X, m, lo=.55, hi=.70, thr=.40, hedge=None, cap=300., tick=15.):
    A = X[1][m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]; sh = net = 0.; stopped = False; hd = hc = 0.
    for t in range(12, 290, 2):
        x = fm(t)
        if hedge and not hd and t > 12 and x <= hedge[0]: hd = hedge[1]; hc = hd * min(.99, A[t][2] if Fv == 'UP' else A[t][1])
        if not stopped and t > 12 and x <= thr: stopped = True
        if stopped or not (lo <= x <= hi) or sh >= cap: continue
        sh += tick; net += tick * min(.99, A[t][1] if Fv == 'UP' else A[t][2])
    w = X[2][m]; return (sh if w == Fv else 0.) - net + ((hd if w != Fv else 0.) - hc)
fams = {'underdog': [(g, lambda X, m, g=g: und(X, m, *g)) for g in itertools.product((.65, .70, .75, .80), (.85, .90, .95, .99), (12, 40, 90), (150, 230, 290), (0, 1), (20., 40., 80.)) if g[3] > g[2] + 20],
        'fav_hedge': [(g, lambda X, m, g=g: fav(X, m, hedge=g)) for g in itertools.product((.45, .40, .35, .30, .25), (15., 30., 60., 90., 150.))],
        'fav_band': [(g, lambda X, m, g=g: fav(X, m, lo=g[0], hi=g[1], thr=g[2])) for g in itertools.product((.50, .55, .60), (.65, .70, .80, .90), (.30, .35, .40))]}
joint = []
for fn, cfgs in fams.items():
    cnt = {'BTC': 0, 'ETH_D': 0, 'ETH_C': 0, 'joint': 0}; best = []
    for g, f in cfgs:
        pb = {m: f(B, m) for m in B[0]}; pe = {m: f(E, m) for m in E[0]}; ok = {}; ms = {}
        mn, o = each(B[3], pb, B[0]); ok['BTC'] = o and S.fmean(pb.values()) > 0; ms['BTC'] = (S.fmean(pb.values()), mn)
        for k in ('ETH_D', 'ETH_C'): sub = SPL['ETH'][k]; mn, o = each(E[3], pe, sub); ok[k] = o and S.fmean(pe[m] for m in sub) > 0; ms[k] = (S.fmean(pe[m] for m in sub), mn)
        for k in ok: cnt[k] += ok[k]
        j = all(ok.values()); cnt['joint'] += j
        if j: joint.append((fn, g, ms))
        best.append((min(ms['BTC'][0], ms['ETH_D'][0], ms['ETH_C'][0]), g, ok, ms))
    print('\n%s: configs %d | EACH+mean>0 pass counts %s' % (fn, len(cfgs), cnt)); best.sort(key=lambda r: -r[0])
    for r in best[:3]: print('   best-min-mean', r[1], 'min mean %.1f' % r[0], {k: v for k, v in r[2].items()}, {k: (round(v[0], 1), {a: round(b, 1) for a, b in v[1].items()}) for k, v in r[3].items()})
print('\nJOINT candidates (pass BTC + ETH disc + ETH conf):', len(joint)); [print(' ', j) for j in joint[:10]]
