"""Post-flip observation (pre-declared 2026-10-02).  Base FAV: band .55-.70, buy 15 sh / 2 s, cap 300, buy-freeze at the first F mid<=.40 (t>12 s).  After the flip wait DELTA seconds; at tf+DELTA:
if F mid >= R keep holding (HOLD) else CUT: SELL = sell every F share at bid-.01, HEDGE = buy the opposite side for every F share at ask.  Grid (18): DELTA {5,10,20} x R {.35,.40,.45} x CUT {SELL,HEDGE}.
Also reports the AUC of 'F mid at tf+DELTA' (and of the max rebound within DELTA) for separating FALSE_FLIP from TRUE_FLIP.  Criteria: corrected EACH (noflip>0, false>0, |true|<min(noflip,false)); margin = min(noflip,false)-|true|.
Walk-forward: first 60% discovery; rule: maximise discovery margin subject to noflip>0 and false>0; test on the last 40% with market-bootstrap CI; replay on real books, taker, no fees."""
import sys, random, itertools, statistics as S
sys.argv = sys.argv[:3]
import fav_improve_wf as F
P, M, ids, CL = F.P, F.M, F.ids, F.CL
def run(m, delta, R, cut, lo=.55, hi=.70, cap=300., tick=15., gap=.01):
    A = P[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if Fv == 'UP' else 2; oi = 2 if Fv == 'UP' else 1; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]
    sh = opp = net = 0.; tf = None; done = False
    for t in range(12, 290, 2):
        f = fm(t)
        if tf is None and t > 12 and f <= .40: tf = t
        if tf is None:
            if lo <= f <= hi and sh < cap: sh += tick; net += tick * min(.99, A[t][qi][0])
        elif not done and t >= tf + delta:
            done = True
            if f < R:
                if cut == 'SELL': net -= sh * max(.01, A[t][qi][1] - gap); sh = 0.
                else: opp += sh; net += sh * min(.99, A[t][oi][0])
    win = M[m]['win']; return (sh if win == Fv else 0.) + (opp if win != Fv else 0.) - net
def feat(m, delta):
    A = P[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]
    tf = next((t for t in range(14, 290, 2) if fm(t) <= .40), None)
    if tf is None or tf + delta > 288: return None
    return fm(tf + delta), max(fm(t) for t in range(tf, tf + delta + 1)) - fm(tf)
def auc(pos, neg):
    if not pos or not neg: return float('nan')
    return sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))
for dl in (5, 10, 20):
    fs = {m: feat(m, dl) for m in ids}; fa = [fs[m] for m in ids if fs[m] and CL[m] == 'FALSE_FLIP']; tr = [fs[m] for m in ids if fs[m] and CL[m] == 'TRUE_FLIP']
    print('DELTA %2ds: AUC(F mid at tf+DELTA) %.3f | AUC(max rebound) %.3f | n false %d true %d' % (dl, auc([a[0] for a in fa], [t[0] for t in tr]), auc([a[1] for a in fa], [t[1] for t in tr]), len(fa), len(tr)))
CFG = list(itertools.product((5, 10, 20), (.35, .40, .45), ('SELL', 'HEDGE'))); PN = {c: {m: run(m, *c) for m in ids} for c in CFG}
cutn = int(.6 * len(ids)); D, T = ids[:cutn], ids[cutn:]; rng = random.Random(101)
def st(c, sub):
    g = {k: [PN[c][m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}; n = len(sub)
    return mn, min(mn['NO_FLIP'], mn['FALSE_FLIP']) + min(0., mn['TRUE_FLIP']), sum(mn[k] * len(g[k]) / n for k in mn)
def show(tag, c, sub):
    mn, mg, ex = st(c, sub); bs = sorted(st(c, [rng.choice(sub) for _ in sub])[1] for _ in range(300))
    print('%-6s DELTA %2d R %.2f %-5s | noflip %6.1f false %6.1f true %6.1f | margin %6.1f [%6.1f,%6.1f] | expectation %5.1f | %s' % (tag, *c, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], mg, bs[8], bs[291], ex, 'EACH PASS' if mg > 0 and mn['FALSE_FLIP'] > 0 and mn['NO_FLIP'] > 0 and mn['TRUE_FLIP'] < 0 else 'fail'))
res = sorted([(c,) + st(c, D) for c in CFG if st(c, D)[0]['NO_FLIP'] > 0 and st(c, D)[0]['FALSE_FLIP'] > 0], key=lambda r: -r[2])
print('\ndiscovery rows with noflip>0 and false>0: %d of %d; EACH margin>0: %d' % (len(res), len(CFG), sum(r[2] > 0 for r in res)))
print('TEST (last 40%%, n=%d)' % len(T)); [show('top%d' % (i + 1), r[0], T) for i, r in enumerate(res[:3])]
print('ALL 519'); [show('top%d' % (i + 1), r[0], ids) for i, r in enumerate(res[:3])]
