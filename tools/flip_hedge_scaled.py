"""Pre-declared prediction-free flip responses that scale with how far the decided favourite F has fallen (fm = F mid, after t=12 s):
 RATCHET  hedge target h(fm)=clip((.5-fm)/.2,0,1) x (F inventory), only ever increased (buy opposite at ask), no new F buys once fm<.5
 TRACK    same target but also reduced when fm recovers (sell opposite at bid-gap), F buys resume when fm>=.55
 BASE / SELL / FREEZE / HEDGE1.0 as in flip_response_check.  Toy (edge 1.0, .6) and real replay (labelled 100; all 220 with inferred winners, flagged)."""
import random, statistics as S, sys
from toy_graduation import build_g, make_edge, make_path, classify, ci_mean

def sim(get, win, pol, gap=.01, tick=15., lo=.60, hi=.80, cap=300., dec_t=12):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; fav = None; t = dec_t; stopped = False; flipped = False
    while t < 290:
        mid, q = get(t)
        if fav is None: fav = 'UP' if mid >= .5 else 'DOWN'
        opp = 'DOWN' if fav == 'UP' else 'UP'; fm = mid if fav == 'UP' else 1 - mid
        if pol in ('RATCHET', 'TRACK'):
            h = min(1., max(0., (.5 - fm) / .2)) * inv[fav]
            if h > inv[opp] + 1e-9: net += (h - inv[opp]) * min(.99, q[opp][0]); inv[opp] = h
            elif pol == 'TRACK' and h < inv[opp] - 1e-9: net -= (inv[opp] - h) * max(.01, q[opp][1] - gap); inv[opp] = h
            can = fm >= (.55 if pol == 'TRACK' else .5) and not (pol == 'RATCHET' and inv[opp] > 0)
            cur = fav
        else:
            if not flipped and t > dec_t and fm <= .4:
                flipped = True
                if pol == 'FREEZE': stopped = True
                elif pol == 'SELL':
                    for s in ('UP', 'DOWN'): net -= inv[s] * max(.01, q[s][1] - gap); inv[s] = 0.
                    stopped = True
                elif pol == 'HEDGE1.0': tot = inv['UP'] + inv['DOWN']; inv[opp] += tot; net += tot * min(.99, q[opp][0]); stopped = True
            cur = 'UP' if mid >= .5 else 'DOWN'; can = not stopped
        cm = mid if cur == 'UP' else 1 - mid
        if pol in ('RATCHET', 'TRACK'): cm = fm
        if can and lo <= cm <= hi and inv['UP'] + inv['DOWN'] < cap: inv[cur] += tick; net += tick * min(.99, q[cur][0])
        t += 2
    return inv[win] - net

def stats(name, pn, grp, rng):
    g = {k: [x for x, q in zip(pn, grp) if q == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; nf, ff, tf = (S.fmean(g[k]) for k in g); rev = g['FALSE_FLIP'] + g['TRUE_FLIP']
    aw = S.fmean([x for x in pn if x > 0]); lo, hi = ci_mean(pn, rng, 300)
    print('%-9s noflip %6.1f false %6.1f true %6.1f | overall %5.1f [%5.1f,%5.1f] | D1 %s book %6.1f | worst %6.1f = %.2fx avgwin' % (name, nf, ff, tf, S.fmean(pn), lo, hi,
          'Y' if S.fmean(rev) >= -.5 * nf else '.', 4 * nf + 3 * ff + 3 * tf, min(pn), -min(pn) / aw))

POLS = ('BASE', 'FREEZE', 'SELL', 'HEDGE1.0', 'RATCHET', 'TRACK')
def main():
    rng = random.Random(31)
    for sc in (1.0, .6):
        g = build_g(make_edge(sc)); r = random.Random(950); P = [make_path(r, g, noise=.02) for _ in range(6000)]; grp = [classify(p, w) for p, w in P]
        print('\n=== toy edge %.1f %d paths ===' % (sc, len(P)))
        def tg(p):
            return lambda t: (p[t], {'UP': (min(.99, p[t] + .01), max(.01, p[t] - .01)), 'DOWN': (min(.99, 1 - p[t] + .01), max(.01, 1 - p[t] - .01))})
        for pol in POLS: stats(pol, [sim(tg(p), 'UP' if w else 'DOWN', pol) for p, w in P], grp, rng)
    import real_replay_stop as rr
    lab = {int(x['market_id']): x['winner'] for x in rr.jl(sys.argv[2]).get('records', [])}; M = rr.load(sys.argv[1], lab)
    def rg(mk):
        def get(t):
            b = rr.at(mk, t + 1.) or rr.at(mk, t); a = rr.at(mk, t); return (a['best_bid'] + a['best_ask']) / 2, {'UP': rr.quotes(b, 'UP'), 'DOWN': rr.quotes(b, 'DOWN')}
        return get
    for nm, sub in (('REAL labelled', {m: v for m, v in M.items() if v['labelled']}), ('REAL all 220 (winner inferred for unlabelled)', M)):
        ms = sorted(sub); grp = [rr.classify(sub[m]) for m in ms]; print('\n=== %s n=%d %s ===' % (nm, len(ms), {k: grp.count(k) for k in set(grp)}))
        for pol in POLS: stats(pol, [sim(rg(sub[m]), sub[m]['win'], pol) for m in ms], grp, rng)

if __name__ == '__main__': main()
