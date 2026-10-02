"""DIP with repair AND stop-loss (pre-declared, 24 configs): as dip_repair_wf (pre-flip FAV .55-.65, FREEZE at flip, DIP buys F while F mid in [.30,.40] up to cap, repair once when F mid >= .60)
plus: STOP S in {.20,.25}: if F mid <= S after DIP shares were bought, sell the DIP shares at bid-gap and stop DIP; TSTOP in {290,200}: no DIP buys after that second.
Action {SELLALL,SELLDIP,HEDGE} x cap {150,300}.  Walk-forward, rule fixed in advance: on the first 60% maximise overall mean subject to true-flip worst-5% mean >= FREEZE-only and worst loss < 3 x avg win;
then everything is evaluated on the last 40% (all 24 printed; only SELECTED counts as a result)."""
import sys, random, itertools, statistics as S
sys.argv = sys.argv[:3]
import entry_timing_wf as E
from toy_graduation import ci_mean
P, M, ids, CL = E.P, E.M, E.ids, E.CL
def run(m, S_, tstop, act, cap, R=.60, dlo=.30, lo=.55, hi=.65, tick=15., gap=.01, fcap=300.):
    A = P[m]; F = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if F == 'UP' else 2; oi = 2 if F == 'UP' else 1; fm = lambda t: A[t][0] if F == 'UP' else 1 - A[t][0]
    sh = dsh = opp = net = 0.; flipped = False; done = False
    for t in range(12, 290, 2):
        f = fm(t)
        if not flipped and t > 12 and f <= .4: flipped = True
        if not flipped:
            if lo <= f <= hi and sh < fcap: sh += tick; net += tick * min(.99, A[t][qi][0])
            continue
        if done: continue
        if dsh > 0 and f <= S_: net -= dsh * max(.01, A[t][qi][1] - gap); sh -= dsh; dsh = 0.; done = True; continue
        if dsh > 0 and f >= R:
            if act == 'SELLALL': net -= sh * max(.01, A[t][qi][1] - gap); sh = dsh = 0.
            elif act == 'SELLDIP': net -= dsh * max(.01, A[t][qi][1] - gap); sh -= dsh; dsh = 0.
            else: opp += sh; net += sh * min(.99, A[t][oi][0])
            done = True; continue
        if t <= tstop and dlo <= f <= .40 and dsh < cap: sh += tick; dsh += tick; net += tick * min(.99, A[t][qi][0])
    win = M[m]['win']; return (sh if win == F else 0.) + (opp if win != F else 0.) - net
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]; rng = random.Random(51)
CFG = list(itertools.product((.20, .25), (290, 200), ('SELLALL', 'SELLDIP', 'HEDGE'), (150, 300)))
def stat(sub, c):
    pn = {m: run(m, *c) for m in sub}; x = list(pn.values()); aw = S.fmean([v for v in x if v > 0]); g = {k: [pn[m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    ts = sorted(g['TRUE_FLIP']); return x, aw, g, S.fmean(ts), S.fmean(ts[:max(1, len(ts) // 20)])
base = stat(D, (.2, 290, 'SELLALL', 0)); print('discovery FREEZE-only: true mean %.1f worst5%% %.1f overall %.1f' % (base[3], base[4], S.fmean(base[0])))
res = [(c, *stat(D, c)) for c in CFG]
ok = sorted([r for r in res if r[5] >= base[4] and -min(r[1]) / r[2] < 3], key=lambda r: -S.fmean(r[1])); print('%d of %d configs pass the discovery constraints' % (len(ok), len(CFG)))
for r in sorted(res, key=lambda r: -S.fmean(r[1]))[:4]: print('  disc top  S=%.2f tstop=%d %-7s cap%d overall %5.1f true %6.1f w5%% %6.1f worst %.2fx' % (*r[0], S.fmean(r[1]), r[4], r[5], -min(r[1]) / r[2]))
def rep(tag, c):
    x, aw, g, tm, t5 = stat(T, c); nf, ff = S.fmean(g['NO_FLIP']), S.fmean(g['FALSE_FLIP']); lo, hi_ = ci_mean(x, rng, 1000); rev = g['FALSE_FLIP'] + g['TRUE_FLIP']
    print('%-9s S=%.2f tstop=%3d %-7s cap%3d | false %6.1f true %6.1f (w5%% %6.1f) | overall %5.1f [%5.1f,%5.1f] | worst %.2fx | G1 %s D1 %s' % (tag, *c, ff, tm, t5, S.fmean(x), lo, hi_, -min(x) / aw, 'Y' if lo > 0 else '.', 'Y' if S.fmean(rev) >= -.5 * nf else '.'))
print('\nTEST (n=%d)' % len(T)); rep('FREEZE', (.2, 290, 'SELLALL', 0))
if ok: rep('SELECTED', ok[0][0])
for c in CFG: rep('grid', c)
