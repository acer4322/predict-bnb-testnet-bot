"""Does shrinking the investment change the TRADING LOGIC of the v1 version (5-min spot vol >= q.50 -> UNDERDOG harvest, else FAV with buy-freeze at .40)?  519 true-labelled markets, replay, quotes at t+1 s, taker, no fee.
Scenarios (clip = shares per order every 2 s, cap = max shares per market): ORIGINAL clip 15 / cap 300; PROPORTIONAL (clip and cap scaled together) x0.5, x0.2, x0.1; CAP-ONLY (clip 15, cap 150/60/30);
MIN-LOT (clip 10 = about the smallest historical live taker order, cap 30 or 60).  For each: PnL per 300-share-equivalent (PnL x 300/cap) by class, overall, generalized EACH, and logic descriptors: mean number of orders, mean second of an order, share of
shares bought before 60 s, average entry price, share of markets that hit the cap, correlation of the per-market PnL with the ORIGINAL scenario's PnL (scaled).   usage: python tools/v1_scaling_check.py PUBLIC_ROOT LABELS.json btc_1s.json.gz"""
import sys, json, gzip, math, pathlib, random, statistics as S, bisect
import real_replay_stop as rr
root, labf, spotf = sys.argv[1:4]
lab = {int(r['market_id']): r['winner'] for r in rr.jl(labf)['records']}; M = {m: v for m, v in rr.load(root, lab).items() if v['labelled']}; ids = sorted(M); CL = {m: rr.classify(M[m]) for m in ids}
spot = {int(k): v[0] for k, v in json.load(gzip.open(spotf, 'rt')).items()}; ks = sorted(spot)
def px(s): i = bisect.bisect_right(ks, s) - 1; return spot[ks[i]] if i >= 0 else None
WS = {}
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
def rv5(w0):
    r = []; prev = px(w0 - 300)
    for s in range(w0 - 299, w0 + 1):
        c = px(s)
        if c and prev: r.append(math.log(c / prev))
        prev = c
    return S.pstdev(r)
RV = {m: rv5(WS[m]) for m in ids}; TH = 3.1834e-05; A_ = {}
for m in ids:
    mk = M[m]; A = {}
    for t in range(0, 291):
        a = rr.at(mk, float(t)) or mk['bs'][0]; b = rr.at(mk, t + 1.) or a; A[t] = ((a['best_bid'] + a['best_ask']) / 2, rr.quotes(b, 'UP'), rr.quotes(b, 'DOWN'))
    A_[m] = A
def fav(m, clip, cap, lo=.55, hi=.70, thr=.40):
    A = A_[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if Fv == 'UP' else 2; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]; sh = net = 0.; stopped = False; buys = []
    for t in range(12, 290, 2):
        x = fm(t)
        if not stopped and t > 12 and x <= thr: stopped = True
        if stopped or not (lo <= x <= hi) or sh >= cap - 1e-9: continue
        q = min(clip, cap - sh); p = min(.99, A[t][qi][0]); sh += q; net += q * p; buys.append((t, q, p))
    return (sh if M[m]['win'] == Fv else 0.) - net, buys
def under(m, clip, cap, lo=.75):
    A = A_[m]; w = M[m]['win']; n = pnl = 0.; buys = []
    for t in range(12, 290, 2):
        if n >= cap - 1e-9: break
        mid = A[t][0]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if cm >= lo:
            u = 'DOWN' if cur == 'UP' else 'UP'; p = min(.99, A[t][2][0] if u == 'DOWN' else A[t][1][0]); q = min(clip, cap - n); n += q; pnl += q * ((1. if u == w else 0.) - p); buys.append((t, q, p))
    return pnl, buys
def v1(m, clip, cap):
    return under(m, clip, cap) if RV[m] >= TH else fav(m, clip, cap)
SCEN = [('ORIGINAL clip15 cap300', 15., 300.), ('PROPORTIONAL x0.5 (7.5/150)', 7.5, 150.), ('PROPORTIONAL x0.2 (3/60)', 3., 60.), ('PROPORTIONAL x0.1 (1.5/30)', 1.5, 30.),
        ('CAP-ONLY clip15 cap150', 15., 150.), ('CAP-ONLY clip15 cap60', 15., 60.), ('CAP-ONLY clip15 cap30', 15., 30.), ('MIN-LOT clip10 cap60', 10., 60.), ('MIN-LOT clip10 cap30', 10., 30.)]
res = {}; rng = random.Random(1)
for name, clip, cap in SCEN: res[name] = {m: v1(m, clip, cap) for m in ids}
base = {m: res[SCEN[0][0]][m][0] for m in ids}
def corr(a, b):
    ma, mb = S.fmean(a), S.fmean(b); c = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b); return c / math.sqrt(va * vb) if va * vb else float('nan')
print('%-30s | PnL per 300-share equivalent: noflip false true | overall | EACH | orders  mean sec  <60s share  avg px  cap hit | corr with original' % 'scenario')
for name, clip, cap in SCEN:
    k = 300. / cap; pn = {m: res[name][m][0] * k for m in ids}; g = {c: [pn[m] for m in ids if CL[m] == c] for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {c: S.fmean(v) for c, v in g.items()}
    neg = [c for c, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; ok = len(neg) == 0 or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos))
    allb = [b for m in ids for b in res[name][m][1]]; nord = S.fmean(len(res[name][m][1]) for m in ids); qt = sum(q for _, q, _ in allb)
    print('%-30s | %7.1f %7.1f %7.1f | %6.1f | %s | %5.1f %6.0f %6.0f%% %6.3f %5.0f%% | %.3f' % (name, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], S.fmean(pn.values()), 'PASS' if ok else 'fail', nord, sum(t * q for t, q, _ in allb) / qt, 100 * sum(q for t, q, _ in allb if t < 60) / qt,
          sum(p * q for _, q, p in allb) / qt, 100 * S.fmean(1. if sum(q for _, q, _ in res[name][m][1]) >= cap - 1e-6 else 0. for m in ids), corr([pn[m] for m in ids], [base[m] for m in ids])))
