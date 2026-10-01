"""POST-HOC exploration (declared as such): pre-flip favourite harvest FREEZE(.60-.80 / .55-.65) + post-flip DIP buy of the original favourite in .25-.40 (dip_buy_check.run).
Evaluated with the fixed graduation criteria on all 519, first/second half by id, and class means.  Because the combination was chosen after seeing the halves, only the
second look (new data) can confirm it."""
import sys, random, statistics as S
import real_replay_stop as rr
from flip_hedge_scaled import sim
from dip_buy_check import run as dip, M as Mm, ids
from toy_graduation import ci_mean
def g(mk):
    def f(t):
        a = rr.at(mk, t); b = rr.at(mk, t + 1.) or a; return (a['best_bid'] + a['best_ask']) / 2, {s: rr.quotes(b, s) for s in ('UP', 'DOWN')}
    return f
rng = random.Random(6); cls = {m: rr.classify(Mm[m]) for m in ids}
dp = {m: dip(Mm[m], .25, .40)[0] for m in ids}
def rep(name, P, sub):
    pn = [P[m] for m in sub]; g_ = {k: [P[m] for m in sub if cls[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; nf, ff, tf = (S.fmean(g_[k]) for k in g_)
    aw = S.fmean([x for x in pn if x > 0]); lo, hi = ci_mean(pn, rng, 1000); nlo, _ = ci_mean(g_['NO_FLIP'], rng, 1000); rev = g_['FALSE_FLIP'] + g_['TRUE_FLIP']
    ok = (lo > 0, nlo > 0, S.fmean(rev) >= -.5 * nf, -min(pn) < 3 * aw, 4 * nf + 3 * ff + 3 * tf > 0)
    print('%-26s n=%3d noflip %6.1f false %6.1f true %6.1f | overall %5.1f [%5.1f,%5.1f] | worst %.2fx | G1 %s G2 %s D1 %s W %s B %s' % (name, len(sub), nf, ff, tf, S.fmean(pn), lo, hi, -min(pn) / aw, *('Y' if x else '.' for x in ok)))
h = len(ids) // 2
for nm, lo, hi in (('FREEZE .60-.80', .60, .80), ('FREEZE .55-.65', .55, .65)):
    base = {m: sim(g(Mm[m]), Mm[m]['win'], 'FREEZE', lo=lo, hi=hi) for m in ids}; combo = {m: base[m] + dp[m] for m in ids}
    for tag, sub in (('ALL519', ids), ('1st half', ids[:h]), ('2nd half', ids[h:])):
        rep('%s %s' % (nm, tag), base, sub); rep('  + DIP %s' % tag, combo, sub)
