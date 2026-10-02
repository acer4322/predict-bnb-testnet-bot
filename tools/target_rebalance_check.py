"""Hypothesis (user, 2026-10-02): the target does not predict reversals, it keeps re-tuning inventory by a formula.
Test a smooth, prediction-free rebalancing rule: every 2 s hold  e(mid)=Q*clip((mid-m0)/(m1-m0),0,1)  shares of the CURRENT favourite
(nothing above HI beyond what is held), buying at the ask and selling at the bid(-gap); trade only when |target-held|>deadband.
Pre-declared grid: m0 in {.50,.55,.60}, m1 in {.65,.70,.80}, deadband in {15,45}; Q=300; trading stops at 290 s.  Reference rows: BASE harvest (buy-only), SELL on flip.
Reports group means, D1 (rev-mean >= -.5*noflip), 4/3/3 book, worst loss / avg win, G1 (CI lower>0).  Toy (edge 1.0/.6) and real replay."""
import random, statistics as S, sys
from toy_graduation import build_g, make_edge, make_path, classify, ci_mean

Q, HI, TICK = 300., .80, 15.

def rebalance(get, win, m0, m1, band, gap=.01, dec_t=12):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; t = dec_t
    while t < 290:
        mid, q = get(t)               # q[side]=(ask,bid)
        cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid; oth = 'DOWN' if cur == 'UP' else 'UP'
        tgt = Q * min(1., max(0., (cm - m0) / (m1 - m0))) if cm <= HI else inv[cur]
        for s_ in (oth,):   # the non-favourite side is unwound whenever held (flip handled by symmetry)
            if inv[s_] > band * 0 + 1e-9 and (cm > .5):
                net -= inv[s_] * max(.01, q[s_][1] - gap); inv[s_] = 0.
        d = tgt - inv[cur]
        if d > band: inv[cur] += d; net += d * min(.99, q[cur][0])
        elif d < -band: net -= -d * max(.01, q[cur][1] - gap); inv[cur] += d
        t += 2
    return (inv['UP'] if win == 'UP' else inv['DOWN']) - net

def toy_get(path):
    def get(t):
        mid = path[t]; return mid, {'UP': (min(.99, mid + .01), max(.01, mid - .01)), 'DOWN': (min(.99, 1 - mid + .01), max(.01, 1 - mid - .01))}
    return get

def stats(name, pn, grp, rng):
    g = {k: [x for x, q in zip(pn, grp) if q == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
    nf, ff, tf = (S.fmean(g[k]) for k in g); rev = g['FALSE_FLIP'] + g['TRUE_FLIP']; aw = S.fmean([x for x in pn if x > 0]); lo, _ = ci_mean(pn, rng, 200)
    print('%-26s noflip %6.1f false %6.1f true %6.1f | overall %5.1f G1 %s | D1 %s | book %6.1f | worst %6.1f (%.2fx avgwin)' % (name, nf, ff, tf, S.fmean(pn), 'Y' if lo > 0 else '.',
          'Y' if S.fmean(rev) >= -.5 * nf else '.', 4 * nf + 3 * ff + 3 * tf, min(pn), -min(pn) / aw))

GRID = [(m0, m1, b) for m0 in (.50, .55, .60) for m1 in (.65, .70, .80) if m1 > m0 for b in (15., 45.)]

def toy():
    rng = random.Random(11)
    for sc in (1.0, .6):
        g = build_g(make_edge(sc)); P = []
        for sd in (900,):
            r = random.Random(sd); P += [make_path(r, g, noise=.02) for _ in range(6000)]
        grp = [classify(p, w) for p, w in P]; print('\n=== toy edge %.1f, %d paths, mix %s ===' % (sc, len(P), {k: grp.count(k) for k in set(grp)}))
        from flip_response_check import run as fr
        for pol in ('BASE', 'SELL'): stats('ref ' + pol, [fr(p, w, pol) for p, w in P], grp, rng)
        for m0, m1, b in GRID: stats('ramp %.2f-%.2f band %d' % (m0, m1, b), [rebalance(toy_get(p), 'UP' if w else 'DOWN', m0, m1, b) for p, w in P], grp, rng)

def real(root, labf):
    import real_replay_stop as rr
    from flip_response_real import run_policy
    rng = random.Random(12)
    lab = {int(r['market_id']): r['winner'] for r in rr.jl(labf).get('records', [])}
    M = {m: v for m, v in rr.load(root, lab).items() if v['labelled']}; ms = sorted(M); grp = [rr.classify(M[m]) for m in ms]
    print('\n=== REAL labelled n=%d mix %s ===' % (len(ms), {k: grp.count(k) for k in set(grp)}))
    for pol in ('BASE', 'SELL'): stats('ref ' + pol, [run_policy(M[m], pol) for m in ms], grp, rng)
    def getter(mk):
        def get(t):
            b = rr.at(mk, t + 1.) or rr.at(mk, t); mid = (rr.at(mk, t)['best_bid'] + rr.at(mk, t)['best_ask']) / 2
            return mid, {'UP': rr.quotes(b, 'UP'), 'DOWN': rr.quotes(b, 'DOWN')}
        return get
    for m0, m1, b in GRID: stats('ramp %.2f-%.2f band %d' % (m0, m1, b), [rebalance(getter(M[m]), M[m]['win'], m0, m1, b) for m in ms], grp, rng)

if __name__ == '__main__':
    toy()
    if len(sys.argv) > 2: real(sys.argv[1], sys.argv[2])
