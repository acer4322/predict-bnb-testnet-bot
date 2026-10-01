"""NEW MECHANISM family (pre-declared 2026-10-02): entry timing + path gate, favourite fixed at dec_t, FREEZE or SELL on flip (F mid<=.4), no DIP / no adding after flip.
Grid (72): dec_t {12,60,120,180} x band {(.60,.75),(.55,.70),(.65,.75)} x gate {none, stable30 (min F mid of previous 30 s >= .52), trend (F mid >= F mid 20 s ago)} x response {FREEZE,SELL}.
Buy 15 sh every 2 s (quotes at t+1 s), cap 300, stop 290 s, no fees.  Walk-forward: first 60% of true-labelled markets (by id) = discovery; selection rule fixed in advance:
maximise discovery overall mean subject to (noflip mean > 0) and (worst loss < 3 x avg win); selected + top-3 by discovery are evaluated on the last 40% with the fixed graduation criteria
(G1 overall CI lower>0, G2 noflip CI lower>0, D1, worst<3x avg win, 4/3/3 book>0) plus true-flip mean and worst-5%."""
import sys, random, itertools, statistics as S
import real_replay_stop as rr
from toy_graduation import ci_mean
lab = {int(r['market_id']): r['winner'] for r in rr.jl(sys.argv[2])['records']}
M = {m: v for m, v in rr.load(sys.argv[1], lab).items() if v['labelled']}; ids = sorted(M)
TS = [12. + 2 * i for i in range(139)]  # 12..288
def prep(mk):
    A = {}
    for t in range(0, 291):
        a = rr.at(mk, float(t)) or mk['bs'][0]; b = rr.at(mk, t + 1.) or a
        A[t] = ((a['best_bid'] + a['best_ask']) / 2, rr.quotes(b, 'UP'), rr.quotes(b, 'DOWN'))
    return A
P = {m: prep(M[m]) for m in ids}; CL = {m: rr.classify(M[m]) for m in ids}
def run(m, dec_t, lo, hi, gate, resp, cap=300., tick=15., gap=.01):
    A = P[m]; mid0 = A[dec_t][0]; F = 'UP' if mid0 >= .5 else 'DOWN'; qi = 1 if F == 'UP' else 2
    fm = lambda t: A[t][0] if F == 'UP' else 1 - A[t][0]
    sh = 0.; net = 0.; stopped = False
    for t in range(dec_t, 290, 2):
        f = fm(t)
        if not stopped and t > dec_t and f <= .4:
            stopped = True
            if resp == 'SELL': net -= sh * max(.01, A[t][qi][1] - gap); sh = 0.
        if stopped or not (lo <= f <= hi) or sh >= cap: continue
        if gate == 'stable30' and min(fm(max(0, t - k)) for k in range(0, 31, 2)) < .52: continue
        if gate == 'trend' and f < fm(max(0, t - 20)): continue
        sh += tick; net += tick * min(.99, A[t][qi][0])
    return (sh if M[m]['win'] == F else 0.) - net
CFG = list(itertools.product((12, 60, 120, 180), ((.60, .75), (.55, .70), (.65, .75)), ('none', 'stable30', 'trend'), ('FREEZE', 'SELL')))
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]; rng = random.Random(14)
def evalc(c, sub, full=False):
    pn = {m: run(m, c[0], *c[1], c[2], c[3]) for m in sub}; x = list(pn.values()); aw = S.fmean([v for v in x if v > 0]) if any(v > 0 for v in x) else float('nan')
    g = {k: [pn[m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    return pn, x, aw, g
def label(c): return 'dec%d band%.2f-%.2f %s %s' % (c[0], c[1][0], c[1][1], c[2], c[3])
res = []
for c in CFG:
    pn, x, aw, g = evalc(c, D); res.append((c, S.fmean(x), -min(x) / aw if aw == aw else 99, S.fmean(g['NO_FLIP']) if g['NO_FLIP'] else 0))
ok = sorted([r for r in res if r[3] > 0 and r[2] < 3], key=lambda r: -r[1]); print('discovery: %d of %d configs satisfy constraints; top 5:' % (len(ok), len(CFG)))
for r in ok[:5]: print('  %-40s overall %6.1f worst %.2fx noflip %.1f' % (label(r[0]), r[1], r[2], r[3]))
def report(tag, c):
    pn, x, aw, g = evalc(c, T); nf, ff, tf = (S.fmean(g[k]) for k in g); lo, hi = ci_mean(x, rng, 1000); nlo, _ = ci_mean(g['NO_FLIP'], rng, 1000); rev = g['FALSE_FLIP'] + g['TRUE_FLIP']; ts = sorted(g['TRUE_FLIP'])
    ok_ = (lo > 0, nlo > 0, S.fmean(rev) >= -.5 * nf, -min(x) < 3 * aw, 4 * nf + 3 * ff + 3 * tf > 0)
    print('TEST %-8s %-38s n=%d noflip %6.1f false %6.1f true %6.1f (worst5%% %6.1f) | overall %5.1f [%5.1f,%5.1f] | worst %.2fx | G1 %s G2 %s D1 %s W %s B %s' % (tag, label(c), len(T), nf, ff, tf, S.fmean(ts[:max(1, len(ts) // 20)]), S.fmean(x), lo, hi, -min(x) / aw, *('Y' if o else '.' for o in ok_)))
print()
if ok: report('SELECTED', ok[0][0]); [report('top%d' % (i + 2), r[0]) for i, r in enumerate(ok[1:3])]
report('ref', (12, (.60, .80), 'none', 'FREEZE')); report('ref', (12, (.55, .65), 'none', 'FREEZE'))
