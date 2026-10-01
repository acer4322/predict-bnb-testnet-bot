"""Risk control = no single market with a huge loss. Compares BASE favourite harvest, price stop (.44) and LOSS-BUDGET stop (liquidate and stop trading
when unrealised loss at the bid reaches B; B = f * baseline mean no-flip profit, f in {.5,.75,1,1.5}).  Reports the user's D1/D2, G1/G2 and the
single-market tail: worst loss, worst-1% mean, and worst/avg-win ratio (scale-free).  Toy worlds + real historical-book replay.  Not live evidence."""
import random, statistics as S, sys
from collections import deque
from toy_graduation import build_g, make_edge, make_path, classify, ci_mean
import real_replay_stop as rr


def run_sim(path, up_wins, B, price_exit=None, K=1, gap=.01, half=.01, tick=15., lo=.60, hi=.80, cap=300., dec_t=12):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; done = False; hist = {'UP': deque(maxlen=K), 'DOWN': deque(maxlen=K)}
    for t, mid in enumerate(path):
        if t >= 290: break
        bid = {'UP': max(.01, mid - half - gap), 'DOWN': max(.01, 1 - mid - half - gap)}
        hist['UP'].append(mid); hist['DOWN'].append(1 - mid)
        if not done and (inv['UP'] > 0 or inv['DOWN'] > 0):
            liq = False
            if B is not None and inv['UP'] * bid['UP'] + inv['DOWN'] * bid['DOWN'] - net <= -B: liq = True
            if price_exit is not None:
                for s in ('UP', 'DOWN'):
                    if inv[s] > 0 and len(hist[s]) == K and S.median(hist[s]) <= price_exit: liq = True
            if liq:
                net -= inv['UP'] * bid['UP'] + inv['DOWN'] * bid['DOWN']; inv = {'UP': 0., 'DOWN': 0.}; done = True
        if done or t < dec_t or t % 2: continue
        cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
        if not (lo <= cm <= hi) or inv['UP'] + inv['DOWN'] >= cap: continue
        inv[cur] += tick; net += tick * min(.99, cm + half)
    return (inv['UP'] if up_wins else inv['DOWN']) - net


def run_real(mk, B, price_exit, delay=1.0, lo=.60, hi=.80, cap=300., tick=15., dec_t=12.):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; done = False; t = dec_t
    while t < 290.:
        b = rr.at(mk, t)
        if b is None: t += 2; continue
        mid = (b['best_bid'] + b['best_ask']) / 2
        if not done and (inv['UP'] > 0 or inv['DOWN'] > 0):
            bx = rr.at(mk, t + delay) or b; bid = {s: max(.01, rr.quotes(bx, s)[1]) for s in ('UP', 'DOWN')}; liq = False
            if B is not None and inv['UP'] * bid['UP'] + inv['DOWN'] * bid['DOWN'] - net <= -B: liq = True
            if price_exit is not None:
                for s in ('UP', 'DOWN'):
                    if inv[s] > 0 and (mid if s == 'UP' else 1 - mid) <= price_exit: liq = True
            if liq: net -= inv['UP'] * bid['UP'] + inv['DOWN'] * bid['DOWN']; inv = {'UP': 0., 'DOWN': 0.}; done = True
        if not done:
            cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
            if lo <= cm <= hi and inv['UP'] + inv['DOWN'] < cap:
                bx = rr.at(mk, t + delay) or b; inv[cur] += tick; net += tick * min(.99, rr.quotes(bx, cur)[0])
        t += 2.
    return inv[mk['win']] - net


def report(name, pn, grp, rng, W0):
    g = {k: [x for x, q in zip(pn, grp) if q == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mnf = S.fmean(g['NO_FLIP']); rev = g['FALSE_FLIP'] + g['TRUE_FLIP']
    mrev = S.fmean(rev); tot = 4 * mnf + 3 * S.fmean(g['FALSE_FLIP']) + 3 * S.fmean(g['TRUE_FLIP']); worst = min(pn); s = sorted(pn); w1 = S.fmean(s[:max(1, len(s) // 100)])
    lo, _ = ci_mean(pn, rng, 200); y = lambda b: 'Y' if b else '.'; ratio = -worst / mnf if mnf > 0 else float('nan')
    print('%-24s noflip %6.1f rev-mean %6.1f | worst %7.1f worst1%% %7.1f ratio %4.2fx | G1 %s D1 %s D2 %s | worst<=1x %s <=1.5x %s <=2x %s' % (name, mnf, mrev, worst, w1, ratio, y(lo > 0), y(mrev >= -.5 * mnf), y(tot > 0), y(ratio <= 1.), y(ratio <= 1.5), y(ratio <= 2.)))


def main():
    rng = random.Random(4)
    for sc in (1.0, .6, .3):
        g = build_g(make_edge(sc)); P = []
        for sd in (500, 501):
            r = random.Random(sd); P += [make_path(r, g, noise=.02) for _ in range(4000)]
        grp = [classify(p, w) for p, w in P]; W0 = S.fmean(x for x, q in zip([run_sim(p, w, None) for p, w in P], grp) if False) if False else None
        base = [run_sim(p, w, None) for p, w in P]; W0 = S.fmean([x for x, q in zip(base, grp) if q == 'NO_FLIP'])
        print('\n=== TOY edge scale %.1f (baseline avg win %.1f) ===' % (sc, W0))
        report('BASE', base, grp, rng, W0)
        report('price stop .44 K3', [run_sim(p, w, None, .44, 3) for p, w in P], grp, rng, W0)
        for f in (1.5, 1.0, .75, .5): report('loss budget %.2fx avg win' % f, [run_sim(p, w, f * W0) for p, w in P], grp, rng, W0)
    if len(sys.argv) > 1:
        lab = {int(r['market_id']): r['winner'] for r in rr.jl(sys.argv[2]).get('records', [])} if len(sys.argv) > 2 else {}
        M = rr.load(sys.argv[1], lab)
        for nm, sub in (('REAL labelled', {m: v for m, v in M.items() if v['labelled']}), ('REAL all (winner inferred)', M)):
            ms = sorted(sub); grp = [rr.classify(sub[m]) for m in ms]; base = [run_real(sub[m], None, None) for m in ms]
            W0 = S.fmean([x for x, q in zip(base, grp) if q == 'NO_FLIP']); print('\n=== %s n=%d (baseline avg win %.1f) ===' % (nm, len(ms), W0))
            report('BASE', base, grp, rng, W0); report('price stop .44', [run_real(sub[m], None, .44) for m in ms], grp, rng, W0)
            for f in (1.5, 1.0, .75, .5): report('loss budget %.2fx avg win' % f, [run_real(sub[m], f * W0, None) for m in ms], grp, rng, W0)


if __name__ == '__main__':
    main()
