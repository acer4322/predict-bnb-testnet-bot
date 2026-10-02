"""Walk-forward: choose among the pre-declared policy x band candidates on the first 60% of true-labelled markets (by id), evaluate the choice on the last 40%.
Selection rule fixed before running: highest discovery overall mean among candidates whose discovery single worst loss < 3x avg win.  No fee (f=0), delay 1 s."""
import sys, random, statistics as S
import real_replay_stop as rr
from flip_hedge_scaled import sim
from flip_response_real import run_policy
from toy_graduation import ci_mean
lab = {int(r['market_id']): r['winner'] for r in rr.jl(sys.argv[2])['records']}
M = {m: v for m, v in rr.load(sys.argv[1], lab).items() if v['labelled']}; ids = sorted(M); cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]
def get(mk):
    def g(t):
        a = rr.at(mk, t); b = rr.at(mk, t + 1.) or a; return (a['best_bid'] + a['best_ask']) / 2, {s: rr.quotes(b, s) for s in ('UP', 'DOWN')}
    return g
def pnl(m, pol, lo, hi): return sim(get(M[m]), M[m]['win'], pol, lo=lo, hi=hi)
C = [(p, lo, hi) for p in ('BASE', 'FREEZE', 'SELL', 'HEDGE1.0') for lo, hi in ((.60, .80), (.60, .70), (.65, .80), (.70, .85), (.55, .65))]
rng = random.Random(8); res = {}
for c in C:
    d = [pnl(m, *c) for m in D]; aw = S.fmean([x for x in d if x > 0]); res[c] = (S.fmean(d), -min(d) / aw)
    print('discovery %-9s %.2f-%.2f overall %6.1f worst %.2fx' % (c[0], c[1], c[2], *res[c]))
ok = [c for c in C if res[c][1] < 3]; best = max(ok, key=lambda c: res[c][0]); print('\nselected on discovery:', best)
for tag, c in (('selected', best), ('BASE .60-.80', ('BASE', .60, .80)), ('HEDGE1.0 .60-.80', ('HEDGE1.0', .60, .80)), ('FREEZE .60-.80', ('FREEZE', .60, .80))):
    t = [pnl(m, *c) for m in T]; aw = S.fmean([x for x in t if x > 0]); lo, hi = ci_mean(t, rng, 1000)
    print('TEST %-18s n=%d overall %6.1f [%6.1f,%6.1f] worst %.2fx' % (tag, len(t), S.fmean(t), lo, hi, -min(t) / aw))
