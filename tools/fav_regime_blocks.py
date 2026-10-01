"""Pattern: is the favourite overpriced in the NEWER markets?  6 id-ordered blocks of the 519 true-labelled markets.  For each block: (a) per-share edge of buying the current favourite at the ask when its mid >= .70
(win - ask, equal weight per market, market bootstrap CI), (b) win rate vs ask, (c) UNDERDOG harvest PnL per market: every 2 s buy 15 sh of the side that is NOT the current favourite while the favourite mid >= .75
(cap 300 sh, stop 290 s, taker at ask), with its class means (NO_FLIP / FALSE_FLIP / TRUE_FLIP by the fixed rule) and the market-bootstrap CI of the mean."""
import sys, random, statistics as S
sys.argv = sys.argv[:3]
import entry_timing_wf as E
P, M, ids, CL = E.P, E.M, E.ids, E.CL
rng = random.Random(3)
def ci(xs, nb=300):
    ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
def fav_edge(m, lo=.70):
    A = P[m]; w = M[m]['win']; es = []; ws = []; ak = []
    for t in range(12, 290, 2):
        mid = A[t][0]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if cm >= lo: ask = min(.99, A[t][1][0] if cur == 'UP' else A[t][2][0]); es.append((1. if cur == w else 0.) - ask); ws.append(1. if cur == w else 0.); ak.append(ask)
    return (S.fmean(es), S.fmean(ws), S.fmean(ak)) if es else None
def underdog(m, lo=.75, cap=300., tick=15.):
    A = P[m]; w = M[m]['win']; n = 0.; pnl = 0.
    for t in range(12, 290, 2):
        if n >= cap: break
        mid = A[t][0]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if cm >= lo:
            ud = 'DOWN' if cur == 'UP' else 'UP'; ask = min(.99, A[t][2][0] if ud == 'DOWN' else A[t][1][0]); n += tick; pnl += tick * ((1. if ud == w else 0.) - ask)
    return pnl if n else None
k = 6; sz = len(ids) // k
print('block | ids | n | FAV(mid>=.70) edge c [CI] | win vs ask | UNDERDOG(fav>=.75) mean PnL [CI] | by class: noflip / false / true (n traded)')
for b in range(k):
    sub = ids[b * sz:(b + 1) * sz if b < k - 1 else None]; fe = [x for x in (fav_edge(m) for m in sub) if x]; ud = {m: underdog(m) for m in sub}; ud = {m: v for m, v in ud.items() if v is not None}
    l, h = ci([x[0] for x in fe]); ul, uh = ci(list(ud.values())); g = {c: [v for m, v in ud.items() if CL[m] == c] for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    print('%d | %d-%d | %d | %+.1f [%+.1f,%+.1f] | %.1f%% vs %.1f%% | %+.1f [%+.1f,%+.1f] | %+.1f / %+.1f / %+.1f (%d/%d/%d)' % (b + 1, sub[0], sub[-1], len(sub), 100 * S.fmean(x[0] for x in fe), 100 * l, 100 * h, 100 * S.fmean(x[1] for x in fe), 100 * S.fmean(x[2] for x in fe),
          S.fmean(ud.values()), ul, uh, S.fmean(g['NO_FLIP']) if g['NO_FLIP'] else 0, S.fmean(g['FALSE_FLIP']) if g['FALSE_FLIP'] else 0, S.fmean(g['TRUE_FLIP']) if g['TRUE_FLIP'] else 0, *[len(g[c]) for c in g]))
