"""User's 'huge single loss' definition (2026-10-02): a market whose loss >= 3 x (average one-market win, e.g. 100 -> 300) is 'huge'.
Reports, per band: avg win (PnL>0 markets), no-flip mean, overall mean, worst loss, worst/avg-win, share of markets with loss >= 3x avg-win.
Toy worlds only unless a real-replay root is given.  Same favourite-harvest mechanism as toy_graduation3 / loss_budget_check."""
import random, statistics as S, sys
from toy_graduation import build_g, make_edge, make_path, classify
from loss_budget_check import run_sim


def rep(name, pn, grp):
    wins = [x for x in pn if x > 0]; aw = S.fmean(wins); nf = [x for x, g in zip(pn, grp) if g == 'NO_FLIP']
    worst = min(pn); big = sum(x <= -3 * aw for x in pn)
    print('%-18s avgwin %6.1f noflip %6.1f overall %6.1f | worst %7.1f = %.2fx avgwin | markets with loss>=3x avgwin: %d/%d (%.2f%%) | worst/overall-mean %.1fx' % (
        name, aw, S.fmean(nf), S.fmean(pn), worst, -worst / aw, big, len(pn), 100 * big / len(pn), -worst / S.fmean(pn) if S.fmean(pn) > 0 else float('nan')))


def main():
    for sc in (1.0, .6, .3):
        g = build_g(make_edge(sc)); P = []
        for sd in (700, 701, 702, 703):
            r = random.Random(sd); P += [make_path(r, g, noise=.02) for _ in range(5000)]
        grp = [classify(p, w) for p, w in P]; print('-- edge scale %.1f, %d paths' % (sc, len(P)))
        for lo, hi in ((.60, .80), (.60, .70), (.55, .65), (.55, .60)):
            rep('band %.2f-%.2f' % (lo, hi), [run_sim(p, w, None, lo=lo, hi=hi) for p, w in P], grp)


if __name__ == '__main__':
    main()
