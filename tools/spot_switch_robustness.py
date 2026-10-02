"""Robustness of the frozen spot-volatility switch (docs/research_specs/SPOT_VOL_SWITCH_SPEC_20261002_ZH.md): rv_5m >= threshold -> UNDERDOG harvest, else FAV (freeze).
(A) taker fee f in {0,1%,2%} on notional and quote delay d in {1,3} s, thresholds fixed (q.50 = 3.1834e-05, q.67 = 3.9759e-05), evaluated on the TEST half (id >= 2768492);
(B) expanding-window block validation: 6 id blocks; for block k>=2 the threshold is the q-quantile of rv_5m over blocks < k, evaluated on block k, pooled over blocks 2-6;
(C) share of markets above the fixed thresholds per block (regime occupancy).
Pass rule (generalized EACH): exactly one losing class and the other two positive with |loss| < min(positives), or all three positive.  usage: python tools/spot_switch_robustness.py PUBLIC_ROOT LABELS.json btc_1s.json.gz"""
import sys, json, gzip, math, pathlib, random, statistics as S, bisect
import real_replay_stop as rr
from toy_graduation import ci_mean
root, labf, spotf = sys.argv[1:4]
lab = {int(r['market_id']): r['winner'] for r in rr.jl(labf)['records']}; M = {m: v for m, v in rr.load(root, lab).items() if v['labelled']}; ids = sorted(M); CL = {m: rr.classify(M[m]) for m in ids}
spot = {int(k): v[0] for k, v in json.load(gzip.open(spotf, 'rt')).items()}; ks = sorted(spot)
def px(s): i = bisect.bisect_right(ks, s) - 1; return spot[ks[i]] if i >= 0 else None
WS = {}
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
def rv(w0, n):
    r = []; prev = px(w0 - n)
    for s in range(w0 - n + 1, w0 + 1):
        c = px(s)
        if c and prev: r.append(math.log(c / prev))
        prev = c
    return S.pstdev(r)
RV = {m: rv(WS[m], 300) for m in ids}
def arrays(d):
    out = {}
    for m in ids:
        mk = M[m]; A = {}
        for t in range(0, 291):
            a = rr.at(mk, float(t)) or mk['bs'][0]; b = rr.at(mk, t + float(d)) or a
            A[t] = ((a['best_bid'] + a['best_ask']) / 2, rr.quotes(b, 'UP'), rr.quotes(b, 'DOWN'))
        out[m] = A
    return out
def fav(m, A, f, lo=.55, hi=.70, thr=.40, cap=300., tick=15.):
    Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if Fv == 'UP' else 2; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]; sh = net = 0.; stopped = False
    for t in range(12, 290, 2):
        x = fm(t)
        if not stopped and t > 12 and x <= thr: stopped = True
        if stopped or not (lo <= x <= hi) or sh >= cap: continue
        sh += tick; net += tick * min(.99, A[t][qi][0] * (1 + f))
    return (sh if M[m]['win'] == Fv else 0.) - net
def under(m, A, f, lo=.75, cap=300., tick=15.):
    w = M[m]['win']; n = pnl = 0.
    for t in range(12, 290, 2):
        if n >= cap: break
        mid = A[t][0]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if cm >= lo:
            u = 'DOWN' if cur == 'UP' else 'UP'; ask = min(.99, (A[t][2][0] if u == 'DOWN' else A[t][1][0]) * (1 + f)); n += tick; pnl += tick * ((1. if u == w else 0.) - ask)
    return pnl
def stat(pn, sub, rng):
    g = {k: [pn[m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}
    neg = [k for k, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; ok = (len(neg) == 0) or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos))
    lo, hi = ci_mean([pn[m] for m in sub], rng, 400); return mn, ok, S.fmean(pn[m] for m in sub), lo, hi
rng = random.Random(21); cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]
TH = {.50: 3.1834e-05, .67: 3.9759e-05}
print('(A) TEST half n=%d, thresholds fixed' % len(T))
print('%-26s | noflip   false    true | overall [95%% CI] | rule' % 'delay / fee / threshold')
for d in (1, 3):
    A = arrays(d)
    for f in (0., .01, .02):
        fv_ = {m: fav(m, A[m], f) for m in ids}; ud_ = {m: under(m, A[m], f) for m in ids}
        if f == 0.: print('  d=%d pure FAV overall %.1f | pure UNDERDOG %.1f (test)' % (d, S.fmean(fv_[m] for m in T), S.fmean(ud_[m] for m in T)))
        for q, th in TH.items():
            pn = {m: ud_[m] if RV[m] >= th else fv_[m] for m in ids}; mn, ok, ov, lo, hi = stat(pn, T, rng)
            print('d=%ds fee %.0f%% rv_5m>=q%.2f | %6.1f %6.1f %6.1f | %5.1f [%5.1f,%5.1f] | %s' % (d, 100 * f, q, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], ov, lo, hi, 'PASS' if ok else 'fail'))
        if d == 1 and f == 0.: P1 = (fv_, ud_)
fv_, ud_ = P1
print('\n(B) expanding-window blocks (delay 1 s, no fee), pooled over blocks 2-6')
k = 6; sz = len(ids) // k; blocks = [ids[b * sz:(b + 1) * sz if b < k - 1 else None] for b in range(k)]
for q in (.50, .67):
    pn = {}; rows = []
    for b in range(1, k):
        prior = sorted(RV[m] for bb in range(b) for m in blocks[bb]); th = prior[int(q * (len(prior) - 1))]
        for m in blocks[b]: pn[m] = ud_[m] if RV[m] >= th else fv_[m]
        mn, ok, ov, lo, hi = stat(pn, blocks[b], rng); rows.append((b + 1, th, mn, ok, ov, sum(RV[m] >= th for m in blocks[b]) / len(blocks[b])))
    for r in rows: print('  q%.2f block %d (th %.2e, UD %3.0f%%): noflip %6.1f false %6.1f true %6.1f | overall %6.1f | %s' % (q, r[0], r[1], 100 * r[5], r[2]['NO_FLIP'], r[2]['FALSE_FLIP'], r[2]['TRUE_FLIP'], r[4], 'PASS' if r[3] else 'fail'))
    sub = [m for b in range(1, k) for m in blocks[b]]; mn, ok, ov, lo, hi = stat(pn, sub, rng)
    print('  q%.2f POOLED blocks 2-6 n=%d: noflip %.1f false %.1f true %.1f | overall %.1f [%.1f,%.1f] | %s | blocks passing %d/5 | vs FAV only %.1f, UNDERDOG only %.1f' % (q, len(sub), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], ov, lo, hi, 'PASS' if ok else 'fail', sum(r[3] for r in rows), S.fmean(fv_[m] for m in sub), S.fmean(ud_[m] for m in sub)))
print('\n(C) share of markets above the fixed thresholds per block')
for b, bl in enumerate(blocks): print('  block %d: q.50 %3.0f%%  q.67 %3.0f%%  median rv_5m %.2e' % (b + 1, 100 * sum(RV[m] >= TH[.50] for m in bl) / len(bl), 100 * sum(RV[m] >= TH[.67] for m in bl) / len(bl), S.median(RV[m] for m in bl)))
