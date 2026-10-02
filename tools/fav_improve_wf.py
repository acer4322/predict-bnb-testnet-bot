"""FAV improvement search under the user's relaxed standard (2026-10-02): over the three market groups (NO_FLIP, FALSE_FLIP, TRUE_FLIP; class by the fixed rule F mid<=.4 after 12 s),
every LOSING group's |mean loss| must be smaller than the absolute value of the positive means of the other groups.
  SUM  version: |loss_g| < sum of the positive means of the other groups   (primary)
  EACH version: |loss_g| < the smallest positive mean among the other groups (stricter; needs both others positive when only g loses)
Margin m_sum = sum(positive means) - sum(|negative means|).  Grid (40, pre-declared): band {(.55,.70),(.60,.75),(.55,.65),(.60,.70)} x response {FREEZE@.40,FREEZE@.45,FREEZE@.50,SELL@.40,HEDGE@.40} x cap {150,300}.
Walk-forward: first 60% of true-labelled markets (by id) discovery; rule: maximise discovery m_sum subject to NO_FLIP mean > 0; selected + top-3 are scored on the last 40% with market-bootstrap CI of m_sum and the SUM/EACH flags.
Replay on real books (quotes at t+1 s, taker at ask / SELL at bid-.01, HEDGE buys the opposite side at ask), no fees."""
import sys, random, itertools, statistics as S
sys.argv = sys.argv[:3]
import entry_timing_wf as E
P, M, ids, CL = E.P, E.M, E.ids, E.CL
def run(m, lo, hi, resp, thr, cap, tick=15., gap=.01):
    A = P[m]; F = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if F == 'UP' else 2; oi = 2 if F == 'UP' else 1; fm = lambda t: A[t][0] if F == 'UP' else 1 - A[t][0]
    sh = opp = net = 0.; stopped = False
    for t in range(12, 290, 2):
        f = fm(t)
        if not stopped and t > 12 and f <= thr:
            stopped = True
            if resp == 'SELL': net -= sh * max(.01, A[t][qi][1] - gap); sh = 0.
            elif resp == 'HEDGE': opp += sh; net += sh * min(.99, A[t][oi][0])
        if stopped or not (lo <= f <= hi) or sh >= cap: continue
        sh += tick; net += tick * min(.99, A[t][qi][0])
    win = M[m]['win']; return (sh if win == F else 0.) + (opp if win != F else 0.) - net
def gm(pn, sub):
    g = {k: [pn[m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; return {k: (S.fmean(v) if v else 0.) for k, v in g.items()}, g
def flags(mn):
    neg = [k for k, v in mn.items() if v < 0]; pos = {k: v for k, v in mn.items() if v > 0}
    s = all(-mn[k] < sum(v for kk, v in pos.items() if kk != k) for k in neg); e = all(len([v for kk, v in pos.items() if kk != k]) == 2 and -mn[k] < min(v for kk, v in pos.items() if kk != k) for k in neg) if neg else True
    return s, e, sum(pos.values()) - sum(-mn[k] for k in neg)
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]; rng = random.Random(61)
CFG = [(b[0], b[1], r, th, c) for b in ((.55, .70), (.60, .75), (.55, .65), (.60, .70)) for r, th in (('FREEZE', .40), ('FREEZE', .45), ('FREEZE', .50), ('SELL', .40), ('HEDGE', .40)) for c in (150., 300.)]
PN = {c: {m: run(m, *c) for m in ids} for c in CFG}
def label(c): return 'band %.2f-%.2f %-6s@%.2f cap%d' % (c[0], c[1], c[2], c[3], c[4])
def show(tag, c, sub):
    mn, g = gm(PN[c], sub); s, e, m_ = flags(mn)
    bs = sorted(flags(gm({m: PN[c][m] for m in sub}, [rng.choice(sub) for _ in sub]) [0])[2] for _ in range(300))
    print('%-9s %-38s noflip %6.1f false %6.1f true %6.1f | m_sum %6.1f [%6.1f,%6.1f] | SUM %s EACH %s' % (tag, label(c), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], m_, bs[8], bs[291], 'Y' if s else '.', 'Y' if e else '.'))
res = []
for c in CFG:
    mn, _ = gm(PN[c], D); s, e, m_ = flags(mn); res.append((c, mn, s, e, m_))
ok = sorted([r for r in res if r[1]['NO_FLIP'] > 0], key=lambda r: -r[4]); print('discovery: %d/%d configs have NO_FLIP>0; SUM passes %d, EACH passes %d' % (len(ok), len(CFG), sum(r[2] for r in ok), sum(r[3] for r in ok)))
for r in ok[:4]: print('  disc %-38s m_sum %6.1f SUM %s EACH %s' % (label(r[0]), r[4], r[2], r[3]))
print('\nTEST (last 40%%, n=%d)' % len(T))
for i, r in enumerate(ok[:4]): show('top%d' % (i + 1), r[0], T)
show('ref', (.55, .70, 'FREEZE', .40, 300.), T); show('ref-nofreeze', (.55, .70, 'FREEZE', .50, 300.), T)
print('\nALL 519 for the selected:'); show('top1', ok[0][0], ids)
