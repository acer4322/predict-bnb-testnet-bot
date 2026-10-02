"""DIP with REPAIR (user idea, 2026-10-02): pre-flip FAV harvest (dec12, band .55-.65), FREEZE at flip (F mid<=.4), then buy the original favourite F in .25-.40 up to a DIP cap;
when F mid recovers to >= R after the flip ('enough profit'), REPAIR once and stop: SELLALL (sell every F share at bid-gap) | SELLDIP (sell only DIP shares) | HEDGE (buy opposite side for all F shares at ask).
Grid (pre-declared, 27): R {.50,.55,.60} x action x DIP cap {60,150,300}.  Walk-forward: first 60% = discovery; rule fixed in advance: maximise discovery overall mean subject to
true-flip worst-5% mean >= that of FREEZE-only (cap 0) on discovery.  Selected + all 27 on the last 40% are printed with true-flip mean/worst-5%, false-flip, overall CI, worst/avgwin."""
import sys, random, itertools, statistics as S
sys.argv = sys.argv[:3]
import entry_timing_wf as E
from toy_graduation import ci_mean
P, M, ids, CL = E.P, E.M, E.ids, E.CL
def run(m, R, act, cap, lo=.55, hi=.65, tick=15., gap=.01, fcap=300.):
    A = P[m]; F = 'UP' if A[12][0] >= .5 else 'DOWN'; qi = 1 if F == 'UP' else 2; oi = 2 if F == 'UP' else 1; fm = lambda t: A[t][0] if F == 'UP' else 1 - A[t][0]
    sh = 0.; dsh = 0.; opp = 0.; net = 0.; flipped = False; done = False
    for t in range(12, 290, 2):
        f = fm(t)
        if not flipped and t > 12 and f <= .4: flipped = True
        if not flipped:
            if lo <= f <= hi and sh < fcap: sh += tick; net += tick * min(.99, A[t][qi][0])
            continue
        if done: continue
        if f >= R and (sh > 0) and cap > 0 and dsh > 0:
            if act == 'SELLALL': net -= sh * max(.01, A[t][qi][1] - gap); sh = 0.; dsh = 0.
            elif act == 'SELLDIP': net -= dsh * max(.01, A[t][qi][1] - gap); sh -= dsh; dsh = 0.
            elif act == 'HEDGE': opp += sh; net += sh * min(.99, A[t][oi][0])
            done = True; continue
        if .25 <= f <= .40 and dsh < cap: sh += tick; dsh += tick; net += tick * min(.99, A[t][qi][0])
    win = M[m]['win']; return (sh if win == F else 0.) + (opp if win != F else 0.) - net
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]; rng = random.Random(41)
CFG = [(R, a, c) for R in (.50, .55, .60) for a in ('SELLALL', 'SELLDIP', 'HEDGE') for c in (60, 150, 300)]
def stat(sub, c):
    pn = {m: run(m, *c) for m in sub}; x = list(pn.values()); aw = S.fmean([v for v in x if v > 0]); g = {k: [pn[m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    ts = sorted(g['TRUE_FLIP']); return x, aw, g, S.fmean(ts), S.fmean(ts[:max(1, len(ts) // 20)])
base = stat(D, (.5, 'SELLALL', 0)); print('discovery FREEZE-only: true mean %.1f worst5%% %.1f overall %.1f' % (base[3], base[4], S.fmean(base[0])))
res = []
for c in CFG:
    x, aw, g, tm, t5 = stat(D, c); res.append((c, S.fmean(x), tm, t5, -min(x) / aw))
ok = sorted([r for r in res if r[3] >= base[4]], key=lambda r: -r[1]); print('%d of %d configs keep discovery true-flip worst-5%% >= FREEZE-only' % (len(ok), len(CFG)))
def rep(tag, c, sub=T):
    x, aw, g, tm, t5 = stat(sub, c); nf, ff = S.fmean(g['NO_FLIP']), S.fmean(g['FALSE_FLIP']); lo, hi_ = ci_mean(x, rng, 1000); rev = g['FALSE_FLIP'] + g['TRUE_FLIP']
    print('%-9s R=%.2f %-7s cap%3d | noflip %5.1f false %6.1f true %6.1f (w5%% %6.1f) | overall %5.1f [%5.1f,%5.1f] | worst %.2fx | D1 %s' % (tag, *c, nf, ff, tm, t5, S.fmean(x), lo, hi_, -min(x) / aw, 'Y' if S.fmean(rev) >= -.5 * nf else '.'))
print('\nTEST (last 40%%, n=%d)' % len(T)); rep('FREEZE', (.5, 'SELLALL', 0))
if ok: rep('SELECTED', ok[0][0])
for c in CFG: rep('grid', c)
