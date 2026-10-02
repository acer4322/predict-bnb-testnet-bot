"""Pre-declared FAV improvements that use SPOT to avoid true flips (2026-10-02).  The 15 markets of research-data 292ff721 are burned and are NOT used here; data = 519 true-labelled markets (id order, first 60% discovery / last 40% test).
Base FAV: band .55-.70, buy 15 sh / 2 s, cap 300, buy-freeze at F mid <= .40 (replay on real books, quotes at t+1 s, taker, no fee).  z_F(t) = signed spot displacement in favour of the decided favourite F,
ln(S_t/S0)*sign / (sigma*sqrt(300-t)); fair_F(t) = Phi(z_F) (sigma = std of 1 s log returns over the previous 1800 s).
Family A (entry confirmation): only buy while fair_F(t) >= c, c in {.50,.55,.60,.65}.   Family B (spot freeze): freeze buying when z_F(t) <= -z0, z0 in {.5,1.0,1.5} (and still freeze at the market rule).
Selection on discovery by the generalized margin (positive classes' min - |loss|; all three classes), then test.  Also reports the LOW-VOLATILITY subset (rv_5m < 3.1834e-05, the frozen q.50 threshold) where the holdout failed."""
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
def rvol(w0, n):
    r = []; prev = px(w0 - n)
    for s in range(w0 - n + 1, w0 + 1):
        c = px(s)
        if c and prev: r.append(math.log(c / prev))
        prev = c
    return S.pstdev(r)
SG = {m: rvol(WS[m], 1800) for m in ids}; RV5 = {m: rvol(WS[m], 300) for m in ids}; Phi = lambda x: .5 * (1 + math.erf(x / math.sqrt(2)))
A_ = {}
for m in ids:
    mk = M[m]; A = {}
    for t in range(0, 291):
        a = rr.at(mk, float(t)) or mk['bs'][0]; b = rr.at(mk, t + 1.) or a; A[t] = ((a['best_bid'] + a['best_ask']) / 2, rr.quotes(b, 'UP'), rr.quotes(b, 'DOWN'))
    A_[m] = A
def run(m, c=None, z0=None, lo=.55, hi=.70, thr=.40, cap=300., tick=15.):
    A = A_[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; sgn = 1 if Fv == 'UP' else -1; qi = 1 if Fv == 'UP' else 2; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]
    s0 = px(WS[m]); sh = net = 0.; stopped = False
    for t in range(12, 290, 2):
        f = fm(t); z = sgn * math.log(px(WS[m] + t) / s0) / (SG[m] * math.sqrt(max(1, 300 - t)))
        if not stopped and ((t > 12 and f <= thr) or (z0 is not None and z <= -z0)): stopped = True
        if stopped or not (lo <= f <= hi) or sh >= cap: continue
        if c is not None and Phi(z) < c: continue
        sh += tick; net += tick * min(.99, A[t][qi][0])
    return (sh if M[m]['win'] == Fv else 0.) - net
CFG = [(None, None)] + [(c, None) for c in (.50, .55, .60, .65)] + [(None, z) for z in (.5, 1., 1.5)]
PN = {c: {m: run(m, *c) for m in ids} for c in CFG}
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]; rng = random.Random(33); TH = 3.1834e-05
def st(c, sub):
    g = {k: [PN[c][m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}
    pos = [v for v in mn.values() if v > 0]; neg = [-v for v in mn.values() if v < 0]; mg = (min(pos) - max(neg) if neg else min(pos)) if pos else -999.
    return mn, mg, S.fmean(PN[c][m] for m in sub)
lab_ = lambda c: 'base' if c == (None, None) else ('confirm fair>=%.2f' % c[0] if c[0] else 'spot-freeze z<=-%.1f' % c[1])
def show(tag, c, sub):
    mn, mg, ov = st(c, sub); lo, hi = ci_mean([PN[c][m] for m in sub], rng, 400)
    print('%-8s %-22s n=%3d | noflip %6.1f false %6.1f true %6.1f | margin %6.1f | overall %5.1f [%5.1f,%5.1f] | %s' % (tag, lab_(c), len(sub), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], mg, ov, lo, hi, 'PASS' if mg > 0 else 'fail'))
res = sorted(CFG, key=lambda c: -st(c, D)[1]); print('discovery ranking by margin:'); [print('  %-22s margin %6.1f overall %5.1f' % (lab_(c), st(c, D)[1], st(c, D)[2])) for c in res[:4]]
low = lambda sub: [m for m in sub if RV5[m] < TH]
print('\nTEST all (n=%d)' % len(T)); [show('top%d' % (i + 1), c, T) for i, c in enumerate(res[:3])]; show('base', (None, None), T)
print('\nTEST low-vol subset (n=%d)' % len(low(T))); [show('top%d' % (i + 1), c, low(T)) for i, c in enumerate(res[:3])]; show('base', (None, None), low(T))
print('\nALL 519 all configs:'); [show('', c, ids) for c in CFG]
