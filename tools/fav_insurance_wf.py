"""FAV + cheap pre-flip insurance (long-volatility leg we can actually trade): base = FAV (band .55-.70, buy 15 sh every 2 s, cap 300, FREEZE at F mid<=.40).
Insurance: each time FAV buys, also buy h*15 shares of the OTHER side (the underdog) at its ask if the underdog's mid is within [.10, ud_hi]; stops at the same freeze/stop rules; held to settlement.
Grid (pre-declared, 8): h {.15,.30,.50,.80} x ud_hi {.40,.45}.  Criteria = corrected EACH: NO_FLIP>0, FALSE_FLIP>0, |TRUE_FLIP| < min(noflip,false); margin = min(noflip,false)-|true|.
Walk-forward (first 60% discovery; rule: maximise discovery margin subject to noflip>0, false>0), test on last 40% with market-bootstrap CI; also the frequency-weighted expectation and the all-519 table.  Replay on real books, taker, no fees."""
import sys, random, statistics as S
sys.argv = sys.argv[:3]
import fav_improve_wf as F
P, M, ids, CL = F.P, F.M, F.ids, F.CL
def run(m, h, ud_hi, lo=.55, hi=.70, thr=.40, cap=300., tick=15.):
    A = P[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if Fv == 'UP' else 2; oi = 2 if Fv == 'UP' else 1; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]
    sh = ud = net = 0.; stopped = False
    for t in range(12, 290, 2):
        f = fm(t)
        if not stopped and t > 12 and f <= thr: stopped = True
        if stopped or not (lo <= f <= hi) or sh >= cap: continue
        sh += tick; net += tick * min(.99, A[t][qi][0])
        if h > 0 and .10 <= 1 - f <= ud_hi: ud += h * tick; net += h * tick * min(.99, A[t][oi][0])
    win = M[m]['win']; return (sh if win == Fv else ud if win != Fv else 0.) - net
CFG = [(h, u) for h in (.15, .30, .50, .80) for u in (.40, .45)] + [(0., .40)]
PN = {c: {m: run(m, *c) for m in ids} for c in CFG}; cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]; rng = random.Random(91)
def st(c, sub):
    g = {k: [PN[c][m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}; n = len(sub)
    mg = min(mn['NO_FLIP'], mn['FALSE_FLIP']) + min(0., mn['TRUE_FLIP']); return mn, mg, sum(mn[k] * len(g[k]) / n for k in mn)
def show(tag, c, sub):
    mn, mg, ex = st(c, sub); bs = sorted(st(c, [rng.choice(sub) for _ in sub])[1] for _ in range(300))
    print('%-6s h=%.2f ud<=%.2f | noflip %6.1f false %6.1f true %6.1f | margin %6.1f [%6.1f,%6.1f] | expectation %5.1f | %s' % (tag, c[0], c[1], mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], mg, bs[8], bs[291], ex, 'EACH PASS' if mg > 0 and mn['NO_FLIP'] > 0 and mn['FALSE_FLIP'] > 0 and mn['TRUE_FLIP'] < 0 else ('all positive' if min(mn.values()) > 0 else 'fail')))
res = sorted([(c,) + st(c, D) for c in CFG if st(c, D)[0]['NO_FLIP'] > 0 and st(c, D)[0]['FALSE_FLIP'] > 0], key=lambda r: -r[2])
print('discovery (rows with noflip>0 and false>0): %d' % len(res)); [print('  disc h=%.2f ud<=%.2f margin %6.1f expectation %5.1f' % (r[0][0], r[0][1], r[2], r[3])) for r in res[:4]]
print('\nTEST (last 40%%, n=%d)' % len(T)); [show('top%d' % (i + 1), r[0], T) for i, r in enumerate(res[:4])]; show('base', (0., .40), T)
print('\nALL 519'); [show('top%d' % (i + 1), r[0], ids) for i, r in enumerate(res[:3])]; show('base', (0., .40), ids)
