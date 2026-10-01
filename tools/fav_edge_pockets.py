"""Pattern mining on the 519 true-labelled markets: per-share edge of buying the CURRENT favourite at the ask (quotes at t+1 s) by price bin x time bin.  edge = 1[favourite wins] - ask.
Samples every 2 s from 12 s to 288 s; each market contributes its mean edge per cell (equal weight per market), qty-equal.  Discovery = first half of markets (by id), confirmation = second half;
a cell is 'robust' if the market-bootstrap 95% CI lower bound > 0 in BOTH halves (>=25 markets each).  Also prints the per-cell win rate vs average ask, i.e. the underpricing, and the flip rate of favourites that enter at that cell."""
import sys, random, statistics as S, collections
sys.argv = sys.argv[:3]
import entry_timing_wf as E
P, M, ids, CL = E.P, E.M, E.ids, E.CL
PB = [(.50, .55), (.55, .60), (.60, .65), (.65, .70), (.70, .75), (.75, .80), (.80, .90)]; TB = [(12, 60), (60, 120), (120, 180), (180, 240), (240, 290)]
cell = collections.defaultdict(lambda: collections.defaultdict(list))  # (pb,tb) -> market -> [(edge, ask)]
for m in ids:
    A = P[m]; w = M[m]['win']
    for t in range(12, 290, 2):
        mid = A[t][0]; cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid; ask = A[t][1][0] if cur == 'UP' else A[t][2][0]
        for i, (a, b) in enumerate(PB):
            if a <= cm < b:
                for j, (c, d) in enumerate(TB):
                    if c <= t < d: cell[(i, j)][m].append(((1. if cur == w else 0.) - min(.99, ask), min(.99, ask), 1. if cur == w else 0.))
rng = random.Random(5); half = len(ids) // 2; Dm, Tm = set(ids[:half]), set(ids[half:])
def agg(c, subset):
    xs = [(S.fmean(e for e, _, _ in v), S.fmean(a for _, a, _ in v), S.fmean(wn for _, _, wn in v), len(v)) for m, v in c.items() if m in subset and v]; return xs
def ci(xs, nb=300):
    ms = sorted(S.fmean(rng.choice(xs)[0] for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
print('edge in cents per share (win - ask); D = first half, C = second half; markets with samples in the cell')
print('%-11s %-9s | %4s %7s %17s | %4s %7s %17s | winrate vs ask (all)' % ('price', 'time', 'nD', 'D', 'D 95% CI', 'nC', 'C', 'C 95% CI'))
rob = []
for i, pbin in enumerate(PB):
    for j, tbin in enumerate(TB):
        c = cell.get((i, j));
        if not c: continue
        d, t = agg(c, Dm), agg(c, Tm)
        if len(d) < 25 or len(t) < 25: continue
        (l1, h1), (l2, h2) = ci(d), ci(t); allx = agg(c, Dm | Tm)
        flag = 'ROBUST+' if l1 > 0 and l2 > 0 else 'ROBUST-' if h1 < 0 and h2 < 0 else ''
        if flag == 'ROBUST+': rob.append((i, j))
        print('%.2f-%.2f  %3d-%-4d | %4d %+7.2f [%+6.2f,%+6.2f] | %4d %+7.2f [%+6.2f,%+6.2f] | %.1f%% vs %.1f%% %s' % (*pbin, *tbin, len(d), 100 * S.fmean(x[0] for x in d), 100 * l1, 100 * h1, len(t), 100 * S.fmean(x[0] for x in t), 100 * l2, 100 * h2,
              100 * S.fmean(x[2] for x in allx), 100 * S.fmean(x[1] for x in allx), flag))
print('robust positive cells:', rob)
