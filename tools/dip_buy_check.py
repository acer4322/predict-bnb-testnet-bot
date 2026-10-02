"""Diagnostic (NOT a graduation candidate: it is a bet on false flips, which the user's definition excludes).  After the decided favourite F flips (F mid <= .4, t>=12 s)
buy F every 2 s while F mid in [lo,hi] (cap 300 sh, stop 290 s).  Reports overall / by-half / by class with market bootstrap CIs, worst/avg-win.
Pre-declared bands: (.25,.40) (.30,.40) (.20,.40).  Fee 0, delay 1 s."""
import sys, random, statistics as S
import real_replay_stop as rr
from toy_graduation import ci_mean
lab = {int(r['market_id']): r['winner'] for r in rr.jl(sys.argv[2])['records']}
M = {m: v for m, v in rr.load(sys.argv[1], lab).items() if v['labelled']}; ids = sorted(M)
def run(mk, lo, hi, cap=300., tick=15.):
    b = rr.at(mk, 12.); F = 'UP' if (b['best_bid'] + b['best_ask']) / 2 >= .5 else 'DOWN'; sh = 0.; net = 0.; flipped = False; t = 12.
    while t < 290:
        a = rr.at(mk, t); mid = (a['best_bid'] + a['best_ask']) / 2; fm = mid if F == 'UP' else 1 - mid
        if t > 12 and fm <= .4: flipped = True
        if flipped and lo <= fm <= hi and sh < cap:
            x = rr.at(mk, t + 1.) or a; sh += tick; net += tick * min(.99, rr.quotes(x, F)[0])
        t += 2.
    return (sh if mk['win'] == F else 0.) - net, net
rng = random.Random(4)
for lo, hi in ((.25, .40), (.30, .40), (.20, .40)):
    rows = [(m, *run(M[m], lo, hi)) for m in ids]; act = [r for r in rows if r[2] > 0]
    pn = [r[1] for r in rows]; a_ = [r[1] for r in act]; aw = S.fmean([x for x in a_ if x > 0]); cs = sum(r[2] for r in act)
    h = len(ids) // 2; f = lambda rs: S.fmean(r[1] for r in rs)
    lo_, hi_ = ci_mean(a_, rng, 1000)
    print('band %.2f-%.2f | traded markets %d/%d | mean per traded market %6.1f [%6.1f,%6.1f] | ROI on cost %.1f%% | worst %.1f = %.2fx avgwin | first half %.1f (n=%d) second half %.1f (n=%d)' % (lo, hi, len(act), len(rows),
          S.fmean(a_), lo_, hi_, 100 * sum(a_) / cs, min(a_), -min(a_) / aw, f([r for r in act if r[0] in ids[:h]]), sum(r[0] in ids[:h] for r in act), f([r for r in act if r[0] in ids[h:]]), sum(r[0] in ids[h:] for r in act)))
