"""ETH 5-minute markets (research_pack/eth5m, 860 consecutive official-label markets): transfer of the FROZEN BTC rules, no re-tuning, with a three-way time split: DISCOVERY = first 60% (by id), CONFIRMATION = next 25%, FINAL = last 15% (computed but NOT printed
unless --final is given; keep it for one decision).  Rules (replay on real books, quotes at t+1 s, taker, no fee): FAV (band .55-.70, clip 15, cap 300, buy-freeze at F mid <= .40); UNDERDOG (current favourite mid >= .75, buy the other side); v1 = rv_5m >= th -> UNDERDOG else FAV;
v2 = rv_5m >= th -> UNDERDOG else idle, th = ETH discovery quantile q.50 / q.67 of the previous-5-minute 1 s ETHUSDT return std (the BTC absolute thresholds do not transfer).  Reports class means, overall (market bootstrap CI), generalized EACH, flip rate and class mix per split and per 6 id blocks,
and the per-share edge of buying the current favourite by price x time bin (discovery vs confirmation replication).   usage: python tools/eth_frozen_transfer.py PUBLIC_ROOT LABELS.json eth_1s.json.gz [--final]"""
import sys, json, gzip, math, pathlib, random, statistics as S, bisect, collections
import real_replay_stop as rr
from toy_graduation import ci_mean
root, labf, spotf = sys.argv[1:4]; FINAL = '--final' in sys.argv
lab = {int(r['market_id']): r['winner'] for r in rr.jl(labf)['records']}; M0 = {m: v for m, v in rr.load(root, lab).items() if v['labelled']}
def maxgap(v):
    ts = [t for t in v['ts'] if t <= 290] or [0.]; ts = [0. if t < 0 else t for t in ts]; return max([b - a for a, b in zip(ts, ts[1:])] + [290. - ts[-1]])
M = {m: v for m, v in M0.items() if rr.at(v, 12.) is not None and maxgap(v) <= 40.}; print('excluded (no book by 12 s or a gap > 40 s inside 0-290 s incl. to 290 s; forward-filled books are kept):', len(M0) - len(M), 'of', len(M0)); ids = sorted(M); CL = {m: rr.classify(M[m]) for m in ids}
print('ETH markets loaded', len(ids), 'ids', ids[0], ids[-1])
spot = {int(k): v[0] for k, v in json.load(gzip.open(spotf, 'rt')).items()}; ks = sorted(spot)
def px(s): i = bisect.bisect_right(ks, s) - 1; return spot[ks[i]] if i >= 0 else None
WS = {}
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
def rv5(w0):
    r = []; prev = px(w0 - 300)
    for s in range(w0 - 299, w0 + 1):
        c = px(s)
        if c and prev: r.append(math.log(c / prev))
        prev = c
    return S.pstdev(r) if len(r) > 30 else None
RV = {m: rv5(WS[m]) for m in ids}
A_ = {}
for m in ids:
    mk = M[m]; A = {}
    for t in range(0, 291):
        a = rr.at(mk, float(t)) or mk['bs'][0]; b = rr.at(mk, t + 1.) or a; A[t] = ((a['best_bid'] + a['best_ask']) / 2, rr.quotes(b, 'UP'), rr.quotes(b, 'DOWN'))
    A_[m] = A
def fav(m, lo=.55, hi=.70, thr=.40, cap=300., tick=15.):
    A = A_[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if Fv == 'UP' else 2; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]; sh = net = 0.; stopped = False
    for t in range(12, 290, 2):
        x = fm(t)
        if not stopped and t > 12 and x <= thr: stopped = True
        if stopped or not (lo <= x <= hi) or sh >= cap: continue
        sh += tick; net += tick * min(.99, A[t][qi][0])
    return (sh if M[m]['win'] == Fv else 0.) - net
def under(m, lo=.75, cap=300., tick=15.):
    A = A_[m]; w = M[m]['win']; n = pnl = 0.
    for t in range(12, 290, 2):
        if n >= cap: break
        mid = A[t][0]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if cm >= lo: u = 'DOWN' if cur == 'UP' else 'UP'; p = min(.99, A[t][2][0] if u == 'DOWN' else A[t][1][0]); n += tick; pnl += tick * ((1. if u == w else 0.) - p)
    return pnl
FA = {m: fav(m) for m in ids}; UD = {m: under(m) for m in ids}
n = len(ids); i1, i2 = int(.6 * n), int(.85 * n); D, C, Fi = ids[:i1], ids[i1:i2], ids[i2:]
q = lambda p: sorted(RV[m] for m in D if RV[m])[int(p * (len([1 for m in D if RV[m]]) - 1))]; TH = {'q.50': q(.5), 'q.67': q(.67)}; print('ETH rv_5m thresholds (discovery quantiles):', {k: '%.3e' % v for k, v in TH.items()})
def pol(kind, m):
    if kind == 'FAV': return FA[m]
    if kind == 'UNDERDOG': return UD[m]
    th = TH['q.50'] if kind.endswith('q50') else TH['q.67']; hi = RV[m] is not None and RV[m] >= th
    return UD[m] if hi else (FA[m] if kind.startswith('v1') else 0.)
KINDS = ['FAV', 'UNDERDOG', 'v1_q50', 'v1_q67', 'v2_q50', 'v2_q67']; rng = random.Random(8)
def ev(pn, sub):
    g = {c: [pn[m] for m in sub if CL[m] == c] for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {c: (S.fmean(v) if v else 0.) for c, v in g.items()}
    neg = [c for c, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; ok = len(neg) == 0 or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos)); lo, hi = ci_mean([pn[m] for m in sub], rng, 600)
    return mn, ok, S.fmean(pn[m] for m in sub), lo, hi, {c: len(v) for c, v in g.items()}
def table(tag, sub):
    mix = collections.Counter(CL[m] for m in sub); print('\n--- %s (n=%d; flip rate %.0f%%; mix %s) ---' % (tag, len(sub), 100 * (1 - mix['NO_FLIP'] / len(sub)), dict(mix)))
    for k in KINDS:
        pn = {m: pol(k, m) for m in ids}; mn, ok, ov, lo, hi, nn = ev(pn, sub)
        print('%-9s | noflip %6.1f false %6.1f true %6.1f | overall %6.1f [%6.1f,%6.1f] | EACH %s' % (k, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], ov, lo, hi, 'PASS' if ok else 'fail'))
table('DISCOVERY', D); table('CONFIRMATION', C)
if FINAL: table('FINAL (one-shot)', Fi)
print('\nper-block (6 id blocks over all markets except FINAL): FAV | UNDERDOG mean PnL and flip rate')
vis = ids if FINAL else D + C; sz = len(vis) // 6
for b in range(6):
    sub = vis[b * sz:(b + 1) * sz if b < 5 else None]; print('  block %d ids %d-%d: FAV %6.1f | UNDERDOG %6.1f | flip %.0f%%' % (b + 1, sub[0], sub[-1], S.fmean(FA[m] for m in sub), S.fmean(UD[m] for m in sub), 100 * S.fmean(1. if CL[m] != 'NO_FLIP' else 0. for m in sub)))
# price x time edge cells
PB = [(.50, .55), (.55, .60), (.60, .65), (.65, .70), (.70, .75), (.75, .80), (.80, .90)]; TB = [(12, 60), (60, 120), (120, 180), (180, 240), (240, 290)]
cell = collections.defaultdict(lambda: collections.defaultdict(list))
for m in ids:
    if m in Fi and not FINAL: continue
    A = A_[m]; w = M[m]['win']
    for t in range(12, 290, 2):
        mid = A[t][0]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid; ask = min(.99, A[t][1][0] if cur == 'UP' else A[t][2][0])
        for i, (a, b) in enumerate(PB):
            if a <= cm < b:
                for j, (c, d) in enumerate(TB):
                    if c <= t < d: cell[(i, j)][m].append((1. if cur == w else 0.) - ask)
def cic(xs, nb=300): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
rp = rn = 0; tot = 0
for (i, j), c in sorted(cell.items()):
    d = [S.fmean(v) for m, v in c.items() if m in set(D)]; t_ = [S.fmean(v) for m, v in c.items() if m in set(C)]
    if len(d) < 25 or len(t_) < 25: continue
    l1, h1 = cic(d); l2, h2 = cic(t_); tot += 1; rp += (l1 > 0 and l2 > 0); rn += (h1 < 0 and h2 < 0)
print('\nfavourite price x time cells with >=25 markets in both splits: %d | robustly positive in both: %d | robustly negative in both: %d' % (tot, rp, rn))
def auc(xs, ys):
    pos = [x for x, y in zip(xs, ys) if y == 1]; neg = [x for x, y in zip(xs, ys) if y == 0]; return sum((p > q_) + .5 * (p == q_) for p in pos for q_ in neg) / (len(pos) * len(neg)) if pos and neg else float('nan')
print('\nstate indicator: AUC of rv_5m for (flip) and for (UNDERDOG > FAV), discovery | confirmation')
for nm, tgt in (('flip', lambda m: 1 if CL[m] != 'NO_FLIP' else 0), ('UD>FAV', lambda m: 1 if UD[m] > FA[m] else 0)):
    r = []
    for sub in (D, C):
        z = [(RV[m], tgt(m)) for m in sub if RV[m] is not None]; r.append(auc([a for a, _ in z], [b for _, b in z]))
    print('  %-7s %.3f | %.3f' % (nm, *r))
z = [(A_[m][12][0] if A_[m][12][0] >= .5 else 1 - A_[m][12][0], 1 if CL[m] != 'NO_FLIP' else 0) for m in D + C]; print('  AUC(favourite mid at 12 s -> flip), disc+conf: %.3f' % auc([a for a, _ in z], [b for _, b in z]))
