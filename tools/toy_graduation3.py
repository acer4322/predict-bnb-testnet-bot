"""TOY (round 3): smoothed one-and-done stop-loss (trigger on the median of the last K one-second mids of the held side), swept over price noise,
favourite-edge scale and exit slippage. Pre-declared grid; fresh seeds 300-303. Graduation tests as before; G3 strict = retention>=.8 & mean flip
loss<=70% & worst-5% flip loss<=70%; G3-tail = retention>=.8 & worst-5% flip loss<=70% (mean-loss clause dropped).  Mechanism screening only."""
import random, statistics as S
from collections import deque
from toy_graduation import build_g, make_edge, make_path, classify, ci_mean


def run(path, up_wins, exit_lvl, K, gap, half=.01, tick=15., lo=.60, hi=.80, cap=300., dec_t=12):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; done = False; hist = {'UP': deque(maxlen=K), 'DOWN': deque(maxlen=K)}
    for t, mid in enumerate(path):
        if t >= 290: break
        hist['UP'].append(mid); hist['DOWN'].append(1 - mid)
        if exit_lvl is not None and not done:
            for s in ('UP', 'DOWN'):
                if inv[s] > 0 and len(hist[s]) == K and S.median(hist[s]) <= exit_lvl:
                    sm = mid if s == 'UP' else 1 - mid; net -= inv[s] * max(.01, sm - half - gap); inv[s] = 0.; done = True
        if done or t < dec_t or t % 2: continue
        cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if not (lo <= cm <= hi) or inv['UP'] + inv['DOWN'] >= cap: continue
        inv[cur] += tick; net += tick * min(.99, cm + half)
    return (inv['UP'] if up_wins else inv['DOWN']) - net


def tail(xs, q=.05): s = sorted(xs); return S.fmean(s[:max(1, int(len(s) * q))])


def main():
    rng = random.Random(21); rows = []
    print('%-5s %-5s %-4s %-3s | %7s %7s %7s | %4s %4s %4s | G1 G2 G3 G3t G4' % ('scale', 'noise', 'gap', 'K', 'overall', 'noflip', 'flip', 'R', 'Lm', 'Lt'))
    for sc in (1.0, .6, .3):
        g = build_g(make_edge(sc))
        for nz in (0., .02, .04):
            P = []
            for sd in (300, 301, 302, 303):
                r = random.Random(sd); P += [make_path(r, g, noise=nz) for _ in range(3000)]
            grp = [classify(p, w) for p, w in P]; N = len(P); f = {k: sum(x == k for x in grp) / N for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
            parts = lambda pn: ([x for x, q in zip(pn, grp) if q == 'NO_FLIP'], [x for x, q in zip(pn, grp) if q != 'NO_FLIP'], [x for x, q in zip(pn, grp) if q == 'TRUE_FLIP'], [x for x, q in zip(pn, grp) if q == 'FALSE_FLIP'])
            base = [run(p, w, None, 1, 0.) for p, w in P]; bnf, bfl, _, _ = parts(base)
            for gap in (0., .02):
                for K in (1, 3, 5):
                    pn = [run(p, w, .44, K, gap) for p, w in P]; nf, fl, tr, fa = parts(pn); lo_, _ = ci_mean(pn, rng, 150); l2, _ = ci_mean(nf, rng, 150)
                    R = S.fmean(nf) / S.fmean(bnf); Lm = S.fmean(fl) / S.fmean(bfl); Lt = tail(fl) / tail(bfl)
                    G1 = lo_ > 0; G2 = l2 > 0; G3 = R >= .8 and Lm <= .7 and Lt <= .7; G3t = R >= .8 and Lt <= .7
                    G4 = all(f['NO_FLIP'] * S.fmean(nf) + (f['TRUE_FLIP'] + f['FALSE_FLIP']) * (w * S.fmean(tr) + (1 - w) * S.fmean(fa)) > 0 for w in (.4, .5, .6, .7, .8))
                    y = lambda b: 'Y' if b else '.'
                    print('%-5.1f %-5.2f %-4.2f %-3d | %7.1f %7.1f %7.1f | %4.2f %4.2f %4.2f | %2s %2s %2s %3s %2s' % (sc, nz, gap, K, S.fmean(pn), S.fmean(nf), S.fmean(fl), R, Lm, Lt, y(G1), y(G2), y(G3), y(G3t), y(G4)))
                    rows.append((sc, nz, gap, K, G1 and G2 and G3 and G4, G1 and G2 and G3t and G4))
    print('\nfull graduation (G1-G4 incl. strict G3):  %d of %d cells' % (sum(r[4] for r in rows), len(rows)))
    print('graduation with tail-only G3:             %d of %d cells' % (sum(r[5] for r in rows), len(rows)))
    for K in (1, 3, 5): print('  K=%d: strict %d/%d, tail-only %d/%d' % (K, sum(r[4] for r in rows if r[3] == K), sum(1 for r in rows if r[3] == K), sum(r[5] for r in rows if r[3] == K), sum(1 for r in rows if r[3] == K)))


if __name__ == '__main__':
    main()
