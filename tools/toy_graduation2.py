"""TOY (follow-up to toy_graduation.py): one-and-done stop-loss exit ('stop_all': sell the held side at bid when its mid <= EXIT, then no more
trading in that market). Pooled over seeds, with slippage ('gap': extra price lost on the exit fill, in price units) and edge-scale worlds.
Graduation tests G1-G4 exactly as in toy_graduation.py (defined before running). Mechanism screening only: assumes selling is possible at bid,
zero fee, no latency beyond the gap parameter. Not evidence of real profitability."""
import argparse, random, statistics as S
from toy_graduation import build_g, make_edge, make_path, classify, ci_mean


def run(path, up_wins, exit_lvl, gap, half=.01, tick=15., lo=.60, hi=.80, cap=300., dec_t=12):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; done = False
    for t, mid in enumerate(path):
        if t >= 290: break
        if exit_lvl is not None and not done:
            for s in ('UP', 'DOWN'):
                if inv[s] > 0:
                    sm = mid if s == 'UP' else 1 - mid
                    if sm <= exit_lvl:
                        net -= inv[s] * max(.01, sm - half - gap); inv[s] = 0.; done = True
        if done or t < dec_t or t % 2: continue
        cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if not (lo <= cm <= hi) or inv['UP'] + inv['DOWN'] >= cap: continue
        inv[cur] += tick; net += tick * min(.99, cm + half)
    return (inv['UP'] if up_wins else inv['DOWN']) - net


def tail(xs, q=.05): s = sorted(xs); return S.fmean(s[:max(1, int(len(s) * q))])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--seeds', type=int, default=4); ap.add_argument('--n', type=int, default=4000)
    ap.add_argument('--scales', type=float, nargs='*', default=[1.0, .6, .3]); ap.add_argument('--exits', type=float, nargs='*', default=[.45, .42])
    ap.add_argument('--gaps', type=float, nargs='*', default=[0., .02, .04]); a = ap.parse_args(); rng = random.Random(7)
    for sc in a.scales:
        g = build_g(make_edge(sc)); P = []
        for sd in range(a.seeds):
            r = random.Random(100 + sd); P += [make_path(r, g) for _ in range(a.n)]
        grp = [classify(p, w) for p, w in P]; N = len(P); f = {k: sum(x == k for x in grp) / N for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
        parts = lambda pn: ([x for x, q in zip(pn, grp) if q == 'NO_FLIP'], [x for x, q in zip(pn, grp) if q != 'NO_FLIP'], [x for x, q in zip(pn, grp) if q == 'TRUE_FLIP'], [x for x, q in zip(pn, grp) if q == 'FALSE_FLIP'])
        base = [run(p, w, None, 0.) for p, w in P]; bnf, bfl, _, _ = parts(base)
        print('\n=== edge scale %.1f | paths %d | mix no-flip %.0f%% false %.0f%% true %.0f%% | BASE overall %.1f noflip %.1f flip %.1f flip-w5%% %.0f' % (sc, N, 100 * f['NO_FLIP'], 100 * f['FALSE_FLIP'], 100 * f['TRUE_FLIP'], S.fmean(base), S.fmean(bnf), S.fmean(bfl), tail(bfl)))
        print('%-8s %-5s | %8s %7s %7s %8s | %5s %5s %5s | G1 G2 G3 G4   (R lower 95%% bound)' % ('exit', 'gap', 'overall', 'noflip', 'flip', 'flip w5%', 'R', 'Lm', 'Lt'))
        for ex in a.exits:
            for gap in a.gaps:
                pn = [run(p, w, ex, gap) for p, w in P]; nf, fl, tr, fa = parts(pn)
                lo_, _ = ci_mean(pn, rng, 200); l2, _ = ci_mean(nf, rng, 200)
                R = S.fmean(nf) / S.fmean(bnf); Lm = S.fmean(fl) / S.fmean(bfl); Lt = tail(fl) / tail(bfl)
                rb = sorted(S.fmean(rng.choice(range(len(nf))) and [nf[rng.randrange(len(nf))] for _ in nf]) / S.fmean(bnf) for _ in range(100))[2]
                G1 = lo_ > 0; G2 = l2 > 0; G3 = R >= .8 and Lm <= .7 and Lt <= .7
                G4 = all(f['NO_FLIP'] * S.fmean(nf) + (f['TRUE_FLIP'] + f['FALSE_FLIP']) * (w * S.fmean(tr) + (1 - w) * S.fmean(fa)) > 0 for w in (.4, .5, .6, .7, .8))
                print('%-8.3f %-5.2f | %8.1f %7.1f %7.1f %8.1f | %5.2f %5.2f %5.2f | %2s %2s %2s %2s   (R_lo %.2f)' % (ex, gap, S.fmean(pn), S.fmean(nf), S.fmean(fl), tail(fl), R, Lm, Lt, 'Y' if G1 else '.', 'Y' if G2 else '.', 'Y' if G3 else '.', 'Y' if G4 else '.', rb))


if __name__ == '__main__':
    main()
