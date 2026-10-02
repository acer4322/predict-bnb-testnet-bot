"""ETH5M-specific rule search (underdog-harvest family), PRE-REGISTERED protocol:
DISCOVERY = first 60% of usable markets by id, CONFIRMATION = next 25%, FINAL = last 15% (never touched here).
Family (864 configs): fav-mid entry floor lo, fav-mid ceiling hi (stop when favourite is too sure), window [t0,t1], sizing (15 sh per buy | ~$2 per buy), total cost cap.
Selection on DISCOVERY ONLY: config must satisfy EACH (generalized: all classes >0, or one losing class with |loss| < min(other two)) AND overall mean > 0 in each of 3 discovery thirds;
rank by discovery overall t-stat; ONLY the top-1 is judged on CONFIRMATION (others descriptive).  Multiplicity: number of configs tried is printed.
usage: python tools/eth_specific_search.py PUBLIC_ROOT LABELS.json eth_1s.json.gz CACHE.pkl"""
import sys, os, json, pickle, math, itertools, statistics as S, collections, random
root, labf, spotf, cache = sys.argv[1:5]
if os.path.exists(cache): ids, A_, WIN, CL = pickle.load(open(cache, 'rb'))
else:
    import real_replay_stop as rr
    lab = {int(r['market_id']): r['winner'] for r in rr.jl(labf)['records']}; M0 = {m: v for m, v in rr.load(root, lab).items() if v['labelled']}
    def maxgap(v):
        ts = [t for t in v['ts'] if t <= 290] or [0.]; ts = [0. if t < 0 else t for t in ts]; return max([b - a for a, b in zip(ts, ts[1:])] + [290. - ts[-1]])
    M = {m: v for m, v in M0.items() if rr.at(v, 12.) is not None and maxgap(v) <= 40.}; ids = sorted(M); CL = {m: rr.classify(M[m]) for m in ids}; WIN = {m: M[m]['win'] for m in ids}; A_ = {}
    for m in ids:
        mk = M[m]; A = {}
        for t in range(0, 291):
            a = rr.at(mk, float(t)) or mk['bs'][0]; b = rr.at(mk, t + 1.) or a; A[t] = ((a['best_bid'] + a['best_ask']) / 2, rr.quotes(b, 'UP')[0], rr.quotes(b, 'DOWN')[0])
        A_[m] = A
    pickle.dump((ids, A_, WIN, CL), open(cache, 'wb'))
print('markets', len(ids))
def run(m, lo, hi, t0, t1, mode, ccap):
    A = A_[m]; w = WIN[m]; cost = pnl = 0.
    for t in range(t0, t1, 2):
        if cost >= ccap: break
        mid, au, ad = A[t]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if not (lo <= cm <= hi): continue
        u = 'DOWN' if cur == 'UP' else 'UP'; p = min(.99, ad if u == 'DOWN' else au)
        q = 15. if mode == 0 else 2. / max(p, .01)
        cost += q * p; pnl += q * ((1. if u == w else 0.) - p)
    return pnl
n = len(ids); i1, i2 = int(.6 * n), int(.85 * n); D, C = ids[:i1], ids[i1:i2]; th = len(D) // 3; D3 = [D[:th], D[th:2 * th], D[2 * th:]]
def each(pn, sub):
    mn = {c: S.fmean([pn[m] for m in sub if CL[m] == c] or [0.]) for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    neg = [c for c, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]
    ok = not neg or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos)); return mn, ok
def tstat(x): return S.fmean(x) / (S.pstdev(x) / math.sqrt(len(x)) + 1e-9)
grid = list(itertools.product((.65, .70, .75, .80), (.85, .90, .95, .99), (12, 40, 90), (150, 230, 290), (0, 1), (20., 40., 80.)))
print('configs tried on discovery:', len(grid)); res = []; allp = {}
for g in grid:
    if g[3] <= g[2] + 20: continue
    pn = {m: run(m, *g[:2], g[2], g[3], g[4], g[5]) for m in D}; allp[g] = pn; mn, ok = each(pn, D)
    th3 = [S.fmean(pn[m] for m in s) for s in D3]; res.append((g, ok, S.fmean(pn.values()), tstat(list(pn.values())), th3, mn))
passE = [r for r in res if r[1]]; passR = [r for r in passE if all(x > 0 for x in r[4])]
print('discovery: EACH pass %d / %d ; + all thirds positive %d' % (len(passE), len(res), len(passR)))
best = sorted(res, key=lambda r: -r[3])[:5]; print('top-5 by t-stat regardless of EACH:'); [print(' ', r[0], 'EACH' if r[1] else 'no', 'mean %.1f t %.2f thirds %s class %s' % (r[2], r[3], [round(x, 1) for x in r[4]], {k: round(v, 1) for k, v in r[5].items()})) for r in best]
if not passR: print('NO candidate passes the pre-registered discovery filter; stopping (confirmation not consulted).'); sys.exit()
passR.sort(key=lambda r: -r[3]); print('top candidates passing filter:')
for r in passR[:5]: print(' ', r[0], 'mean %.1f t %.2f thirds %s class %s' % (r[2], r[3], [round(x, 1) for x in r[4]], {k: round(v, 1) for k, v in r[5].items()}))
rng = random.Random(3)
for i, r in enumerate(passR[:3]):
    g = r[0]; pn = {m: run(m, *g[:2], g[2], g[3], g[4], g[5]) for m in C}; mn, ok = each(pn, C); x = list(pn.values()); bs = sorted(S.fmean(rng.choice(x) for _ in x) for _ in range(500))
    print('CONFIRMATION%s %s: overall %.1f CI[%.1f,%.1f] class %s EACH %s' % (' (JUDGED top-1)' if i == 0 else ' (descriptive)', g, S.fmean(x), bs[12], bs[487], {k: round(v, 1) for k, v in mn.items()}, 'PASS' if ok else 'fail'))
