"""Flip-response policies for the favourite harvest (toy world, calibrated edge profile).  Question (user, 2026-10-02): no-flip profit is easy;
what happens in FALSE_FLIP and TRUE_FLIP markets under different responses at the moment the decided favourite's mid <= .4 (t>=12 s)?
Policies (pre-declared): BASE follow current favourite; FREEZE no buys after flip, hold; SELL sell everything at bid then stop;
SELLHALF sell half then freeze; HEDGE buy opposite side at ask for H x inventory then freeze (H=.5, 1.0); SELL_REBUY sell, then resume buying
only after the ORIGINAL favourite is back >= .6 (false-flip recovery)."""
import random, statistics as S, sys
from toy_graduation import build_g, make_edge, make_path, classify, ci_mean

def run(path, up_wins, pol, half=.01, gap=.01, tick=15., lo=.60, hi=.80, cap=300., dec_t=12):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; fav = None; flipped = False; stopped = False; peak = 0.
    for t, mid in enumerate(path):
        if t >= 290: break
        bid = {'UP': max(.01, mid - half - gap), 'DOWN': max(.01, 1 - mid - half - gap)}
        ask = {'UP': min(.99, mid + half), 'DOWN': min(.99, 1 - mid + half)}
        if t == dec_t: fav = 'UP' if mid >= .5 else 'DOWN'
        fm = None if fav is None else (mid if fav == 'UP' else 1 - mid)
        if fav is not None and not flipped and t > dec_t and fm <= .4:
            flipped = True
            if pol == 'FREEZE': stopped = True
            elif pol in ('SELL', 'SELL_REBUY', 'SELLHALF'):
                f = .5 if pol == 'SELLHALF' else 1.
                for s in ('UP', 'DOWN'): net -= f * inv[s] * bid[s]; inv[s] *= 1 - f
                stopped = True
            elif pol.startswith('HEDGE'):
                h = float(pol[5:]); tot = inv['UP'] + inv['DOWN']; opp = 'DOWN' if fav == 'UP' else 'UP'
                inv[opp] += h * tot; net += h * tot * ask[opp]; stopped = True
        if pol == 'SELL_REBUY' and stopped and fm is not None and fm >= .6 and flipped:
            stopped = False; flipped = False
        if stopped or t < dec_t or t % 2: continue
        cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if not (lo <= cm <= hi) or inv['UP'] + inv['DOWN'] >= cap: continue
        inv[cur] += tick; net += tick * ask[cur]
    return (inv['UP'] if up_wins else inv['DOWN']) - net

def main():
    rng = random.Random(9)
    for sc in (1.0, .6):
        g = build_g(make_edge(sc)); P = []
        for sd in (800, 801):
            r = random.Random(sd); P += [make_path(r, g, noise=.02) for _ in range(5000)]
        grp = [classify(p, w) for p, w in P]; n = {k: grp.count(k) for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
        print('\n=== toy edge %.1f  paths %d  mix %s ===' % (sc, len(P), n))
        print('%-11s | %7s %7s %7s | %7s | rev-mean vs -.5*noflip | 4/3/3 book | worst5%% of TRUE | G1' % ('policy', 'noflip', 'false', 'true', 'overall'))
        for pol in ('BASE', 'FREEZE', 'SELL', 'SELLHALF', 'SELL_REBUY', 'HEDGE0.5', 'HEDGE1.0'):
            pn = [run(p, w, pol) for p, w in P]; m = {k: S.fmean([x for x, q in zip(pn, grp) if q == k]) for k in n}
            rev = [x for x, q in zip(pn, grp) if q != 'NO_FLIP']; tr = sorted(x for x, q in zip(pn, grp) if q == 'TRUE_FLIP')
            lo, _ = ci_mean(pn, rng, 200)
            print('%-11s | %7.1f %7.1f %7.1f | %7.1f | %7.1f vs %7.1f %s | %8.1f | %8.1f | %s' % (pol, m['NO_FLIP'], m['FALSE_FLIP'], m['TRUE_FLIP'], S.fmean(pn),
                  S.fmean(rev), -.5 * m['NO_FLIP'], 'Y' if S.fmean(rev) >= -.5 * m['NO_FLIP'] else '.', 4 * m['NO_FLIP'] + 3 * m['FALSE_FLIP'] + 3 * m['TRUE_FLIP'],
                  S.fmean(tr[:max(1, len(tr) // 20)]), 'Y' if lo > 0 else '.'))
main()
