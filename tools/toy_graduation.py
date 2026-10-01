"""TOY: does ANY pre-declared direction-free mechanism 'graduate' (G1-G4) in the calibrated fair-price + favourite-underpricing world?
World: true win prob P_t is a martingale (Phi model); the QUOTED mid is g(P) with g defined so that P_true = mid + edge(mid), edge = measured
favourite underpricing profile (5m late-Sept ~ +8.5pp at .5-.7, tapering to 0 at the tails; 'recent' = 30% of it). Taker buys pay ask = mid + half.
Market groups are strategy-independent (price path only).  Graduation tests are defined in the chat/spec before running:
 G1 overall mean>0 (CI lower>0) | G2 no-flip mean>0 (CI lower>0) | G3 vs BASE: no-flip retention>=80% AND flip-market mean loss<=70% of BASE and worst-5% (flip markets)<=70% of BASE
 | G4 overall mean>0 for any true-flip share w in [0.4,0.8] (group-mean reweighting).
Mechanism screening only; not evidence of real profitability.
"""
import argparse, bisect, math, random, statistics as S

EDGE5 = [(.5, 0.), (.6, .085), (.7, .085), (.8, .06), (.9, .03), (.99, 0.)]


def phi(x): return .5 * (1 + math.erf(x / math.sqrt(2)))


def make_edge(scale):
    def e(m):
        xs = [a for a, _ in EDGE5]; i = bisect.bisect_right(xs, m) - 1
        if m <= .5: return 0.
        i = min(i, len(EDGE5) - 2); (x0, y0), (x1, y1) = EDGE5[i], EDGE5[i + 1]
        return scale * (y0 + (y1 - y0) * (m - x0) / (x1 - x0))
    return e


def build_g(edge):
    # g: P -> mid, monotone inverse of mid -> mid + edge(mid) on the favourite side, symmetric below .5
    grid = [i / 2000 for i in range(1000, 1996)]; Ps = [m + edge(m) for m in grid]
    def g(P):
        if P == .5: return .5
        q = max(P, 1 - P); j = bisect.bisect_left(Ps, q)
        m = grid[-1] if j >= len(grid) else (grid[0] if j == 0 else grid[j - 1] + (q - Ps[j - 1]) / (Ps[j] - Ps[j - 1]) * (grid[j] - grid[j - 1]))
        return m if P > .5 else 1 - m
    return g


def make_path(rng, g, T=300, noise=.02, ar=.9):
    x = e = 0.; out = []; sd = 1 / math.sqrt(T)
    for t in range(T):
        x += rng.gauss(0, sd); p = phi(x / math.sqrt(max(T - 1 - t, 1) / T)) if t < T - 1 else (1. if x > 0 else 0.)
        e = ar * e + rng.gauss(0, noise * math.sqrt(1 - ar * ar)); out.append(min(.99, max(.01, g(p) + e)))
    return out, x > 0


def run(path, up_wins, strat, half=.01, tick=15., lo=.60, hi=.80, cap=300., dec_t=12, scale_size=False):
    inv = {'UP': 0., 'DOWN': 0.}; cost = 0.; fav = None; flipped = False
    for t, mid in enumerate(path):
        if t >= 290: break
        if t == dec_t: fav = 'UP' if mid >= .5 else 'DOWN'
        if t < dec_t or t % 2: continue
        cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if not (lo <= cm <= hi) or inv['UP'] + inv['DOWN'] >= cap: continue
        q = tick * (min(1., (cm - .5) / .3) if scale_size else 1.)
        ask = min(.99, cm + half); inv[cur] += q; cost += q * ask
    return (inv['UP'] if up_wins else inv['DOWN']) - cost, cost


def classify(path, up_wins, dec_t=12):
    fav = 'UP' if path[dec_t] >= .5 else 'DOWN'
    flip = any((m if fav == 'UP' else 1 - m) <= .4 for m in path[dec_t:290])
    if not flip: return 'NO_FLIP'
    return 'FALSE_FLIP' if (fav == 'UP') == up_wins else 'TRUE_FLIP'


def tail(xs, q=.05):
    s = sorted(xs); return S.fmean(s[:max(1, int(len(s) * q))])


def ci_mean(xs, rng, n=400):
    if len(xs) < 5: return (None, None)
    ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(n)); return ms[int(.025 * n)], ms[int(.975 * n)]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--n', type=int, default=6000); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--scale', type=float, default=1.0, help='favourite-underpricing scale: 1=late-Sept 5m profile, .3=recent'); ap.add_argument('--noise', type=float, default=.02)
    a = ap.parse_args(); rng = random.Random(a.seed); g = build_g(make_edge(a.scale))
    P = [make_path(rng, g, noise=a.noise) for _ in range(a.n)]; grp = [classify(p, w) for p, w in P]
    cfgs = [('BASE FAV .60-.80 cap300', dict(lo=.60, hi=.80, cap=300.))]
    for lo in (.65, .70, .75, .80, .85):
        for cap in (300., 600.): cfgs.append(('FAV lo=%.2f hi=.95 cap%d' % (lo, cap), dict(lo=lo, hi=.95, cap=cap)))
    cfgs.append(('FAV lo=.70 scaled-size cap600', dict(lo=.70, hi=.95, cap=600., scale_size=True)))
    res = {}
    for name, kw in cfgs:
        out = [run(p, w, 'FAV', **kw) for p, w in P]; res[name] = out
    base = res[cfgs[0][0]]
    f = {g_: sum(x == g_ for x in grp) / len(grp) for g_ in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    print('world: edge scale %.2f | n=%d | market mix: no-flip %.0f%% false-flip %.0f%% true-flip %.0f%% (true share of flips %.0f%%)' % (a.scale, a.n, 100 * f['NO_FLIP'], 100 * f['FALSE_FLIP'], 100 * f['TRUE_FLIP'], 100 * f['TRUE_FLIP'] / (f['TRUE_FLIP'] + f['FALSE_FLIP'])))
    print('%-30s %7s | %8s %8s %8s | %8s %9s | G1 G2 G3 G4' % ('strategy', 'cost', 'overall', 'noflip', 'flip', 'flip w5%', 'overall CI'))
    def stats(out):
        pn = [o[0] for o in out]; nf = [x for x, g_ in zip(pn, grp) if g_ == 'NO_FLIP']; fl = [x for x, g_ in zip(pn, grp) if g_ != 'NO_FLIP']
        tr = [x for x, g_ in zip(pn, grp) if g_ == 'TRUE_FLIP']; fa = [x for x, g_ in zip(pn, grp) if g_ == 'FALSE_FLIP']
        return pn, nf, fl, tr, fa
    bp, bnf, bfl, btr, bfa = stats(base)
    for name, kw in cfgs:
        out = res[name]; pn, nf, fl, tr, fa = stats(out); lo_, hi_ = ci_mean(pn, rng); l2, _ = ci_mean(nf, rng)
        G1 = lo_ is not None and lo_ > 0; G2 = l2 is not None and l2 > 0
        G3 = (S.fmean(nf) >= .8 * S.fmean(bnf)) and (S.fmean(fl) >= .7 * S.fmean(bfl) if S.fmean(bfl) < 0 else False) and (tail(fl) >= .7 * tail(bfl) if tail(bfl) < 0 else False) if name != cfgs[0][0] else False
        G4 = all(f['NO_FLIP'] * S.fmean(nf) + (f['TRUE_FLIP'] + f['FALSE_FLIP']) * (w * S.fmean(tr) + (1 - w) * S.fmean(fa)) > 0 for w in (.4, .5, .6, .7, .8))
        print('%-30s %7.0f | %8.1f %8.1f %8.1f | %8.1f [%5.1f,%5.1f] | %2s %2s %2s %2s' % (name, S.fmean(o[1] for o in out), S.fmean(pn), S.fmean(nf), S.fmean(fl), tail(fl), lo_, hi_, 'Y' if G1 else '.', 'Y' if G2 else '.', 'Y' if G3 else '.', 'Y' if G4 else '.'))


if __name__ == '__main__':
    main()
