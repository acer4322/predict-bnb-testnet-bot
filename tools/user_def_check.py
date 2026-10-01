"""Check the USER'S definition of 'reduced reversal risk' (2026-10-02):
 D1: mean PnL of reversal markets >= -0.5 * mean PnL of non-reversal ('winning') markets  (also per reversal type, and with 'winners' = all PnL>0 markets)
 D2: in a 10-market book of 4 no-flip + 3 false-flip + 3 true-flip, total > 0 (expectation, and share of random 4/3/3 books that are positive).
 plus G1 (overall mean>0, CI lower>0) and G2 (no-flip mean>0).  Toy worlds use the measured favourite-edge profile; real rows replay historical books.
Mechanisms: BASE favourite harvest, and one-and-done stop (K-sample median, exit level).  Not evidence of live profitability."""
import random, statistics as S
from toy_graduation import build_g, make_edge, make_path, classify, ci_mean
from toy_graduation3 import run as run_stop


def report(name, pn, grp, rng, books=4000):
    g = {k: [x for x, q in zip(pn, grp) if q == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    if min(len(v) for v in g.values()) < 5: print('%-34s too few markets in a group' % name); return None
    mnf, mff, mtf = (S.fmean(g[k]) for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')); rev = g['FALSE_FLIP'] + g['TRUE_FLIP']; mrev = S.fmean(rev)
    wins = [x for x in pn if x > 0]; mwin = S.fmean(wins)
    D1 = mrev >= -.5 * mnf; D1w = mrev >= -.5 * mwin; D1t = mff >= -.5 * mnf and mtf >= -.5 * mnf
    tot = 4 * mnf + 3 * mff + 3 * mtf; D2 = tot > 0
    pos = sum((sum(rng.choice(g['NO_FLIP']) for _ in range(4)) + sum(rng.choice(g['FALSE_FLIP']) for _ in range(3)) + sum(rng.choice(g['TRUE_FLIP']) for _ in range(3))) > 0 for _ in range(books)) / books
    lo, _ = ci_mean(pn, rng, 200); l2, _ = ci_mean(g['NO_FLIP'], rng, 200); y = lambda b: 'Y' if b else '.'
    print('%-34s noflip %6.1f | false %6.1f true %6.1f | rev-mean %6.1f (limit %6.1f) | 4/3/3 book %7.1f P(book>0)=%3.0f%% | G1 %s G2 %s D1 %s D1(per-type) %s D1(winners=pnl>0) %s D2 %s' % (
        name, mnf, mff, mtf, mrev, -.5 * mnf, tot, 100 * pos, y(lo > 0), y(l2 > 0), y(D1), y(D1t), y(D1w), y(D2)))
    return D1 and D2 and lo > 0 and l2 > 0


def main():
    rng = random.Random(5)
    print('=== TOY worlds (noise .02, seeds 400-403, 16000 paths each) ===')
    for sc in (1.0, .6, .3, .0):
        g = build_g(make_edge(sc)); P = []
        for sd in (400, 401, 402, 403):
            r = random.Random(sd); P += [make_path(r, g, noise=.02) for _ in range(4000)]
        grp = [classify(p, w) for p, w in P]; print('-- edge scale %.1f' % sc)
        for nm, ex, K, gap in (('BASE favourite harvest', None, 1, 0.), ('stop .44 K=3 gap .01', .44, 3, .01), ('stop .44 K=5 gap .02', .44, 5, .02)):
            report(nm, [run_stop(p, w, ex, K, gap) for p, w in P], grp, rng)
    import real_replay_stop as rr
    from pathlib import Path
    import sys
    if len(sys.argv) > 1:
        lab = {int(r['market_id']): r['winner'] for r in rr.jl(sys.argv[2]).get('records', [])} if len(sys.argv) > 2 else {}
        M = rr.load(sys.argv[1], lab)
        for nm, sub in (('real LABELLED', {m: v for m, v in M.items() if v['labelled']}), ('real ALL (winner inferred)', M)):
            ms = sorted(sub); grp = [rr.classify(sub[m]) for m in ms]; print('=== %s n=%d ===' % (nm, len(ms)))
            for nm2, ex in (('BASE favourite harvest', None), ('stop .44', .44), ('stop .45', .45)):
                report(nm2, [rr.run(sub[m], ex, 1.0)[0] for m in ms], grp, rng)


if __name__ == '__main__':
    main()
