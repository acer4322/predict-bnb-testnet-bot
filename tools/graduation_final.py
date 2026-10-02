"""Graduation recomputation on TRUE labels (research-data 906676c), thresholds fixed in docs/research_specs/GRADUATION_LOCAL_TASKS_20261002_ZH.md:
 G1 overall mean CI lower > 0 | G2 no-flip mean CI lower > 0 | D1 rev-mean >= -.5*noflip-mean | single worst loss < 3 x average winning market | 4/3/3 book > 0.
Policies: BASE, HEDGE1.0, SELL, FREEZE (flip_hedge_scaled.sim).  Cost sensitivity (pre-declared): taker fee f on notional for every buy/sell f in {0,1%,2%}, quote delay in {1,2} s.
Sets: ALL519 binary true labels; EXISTING219; NEW300 (chosen by availability, not a pure hold-out).  The two 0.5/0.5 settlements are excluded (listed separately)."""
import sys, json, random, statistics as S
import real_replay_stop as rr
from flip_hedge_scaled import sim
from toy_graduation import ci_mean

root, labd = sys.argv[1], sys.argv[2]
L = lambda n: {int(r['market_id']): r['winner'] for r in rr.jl(f'{labd}/{n}')['records']}
allL, ex, new = L('GRADUATION_LABELS_ALL_TRUE.json'), L('GRADUATION_LABELS_EXISTING220_TRUE.json'), L('GRADUATION_LABELS_NEW300_TRUE.json')
M = rr.load(root, allL); M = {m: v for m, v in M.items() if v['labelled']}
print('loaded', len(M), 'of', len(allL), 'labelled books')
def getter(mk, f, delay):
    def get(t):
        a = rr.at(mk, t); b = rr.at(mk, t + delay) or a
        q = {s: (min(.99, rr.quotes(b, s)[0] * (1 + f)), max(.01, rr.quotes(b, s)[1] * (1 - f))) for s in ('UP', 'DOWN')}
        return (a['best_bid'] + a['best_ask']) / 2, q
    return get
rng = random.Random(77)
def report(tag, ms, pol, f, delay):
    grp = [rr.classify(M[m]) for m in ms]; pn = [sim(getter(M[m], f, delay), M[m]['win'], pol) for m in ms]
    g = {k: [x for x, q in zip(pn, grp) if q == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; nf, ff, tf = (S.fmean(g[k]) for k in g); rev = g['FALSE_FLIP'] + g['TRUE_FLIP']
    aw = S.fmean([x for x in pn if x > 0]); lo, hi = ci_mean(pn, rng, 1000); nlo, _ = ci_mean(g['NO_FLIP'], rng, 1000); worst = min(pn)
    ok = dict(G1=lo > 0, G2=nlo > 0, D1=S.fmean(rev) >= -.5 * nf, W=-worst < 3 * aw, B=4 * nf + 3 * ff + 3 * tf > 0)
    print('%-9s %-8s f=%.0f%% d=%gs n=%3d | noflip %6.1f [lo %6.1f] false %6.1f true %6.1f | overall %5.1f [%5.1f,%5.1f] | worst %6.1f=%.2fx(avgwin %.0f) | %s | %s' % (tag, pol, 100 * f, delay, len(ms), nf, nlo, ff, tf,
          S.fmean(pn), lo, hi, worst, -worst / aw, aw, ' '.join('%s%s' % (k, 'Y' if v else '.') for k, v in ok.items()), 'GRADUATES' if all(ok.values()) else ''))
sets = [('ALL519', sorted(M)), ('EXIST219', sorted(m for m in M if m in ex)), ('NEW300', sorted(m for m in M if m in new))]
if len(sys.argv) > 3 and sys.argv[3] == 'quick': sets = sets[:1]
for tag, ms in sets:
    grp = [rr.classify(M[m]) for m in ms]; print('\n#### %s mix %s' % (tag, {k: grp.count(k) for k in set(grp)}))
    for f, d in ((0., 1.), (.01, 1.), (.02, 1.), (.02, 2.)):
        for pol in ('BASE', 'HEDGE1.0', 'SELL', 'FREEZE'): report(tag, ms, pol, f, d)
