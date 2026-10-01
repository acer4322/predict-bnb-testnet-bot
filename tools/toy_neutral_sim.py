"""TOY: direction-free strategy families in the calibrated fair-price+favourite-underpricing world (see toy_floor_sim2.py).
Compares strategies at EQUAL TAIL RISK (PnL scales linearly with size, so each strategy is rescaled so its worst-5% mean = -TAIL).
Parameters are the measured ones, fixed before running: adverse selection adv (passive fill pays bid+adv; 0.025 = measured -1.6c at +5s),
favourite underpricing shrink (0.286 = measured ~8pp at mid .65-.7). Mechanism screening only; not evidence of real profitability.

Strategies (5-minute market, 1-second steps, stop new orders at 290 s):
  FAV        buy the favourite at the ask every 2 s while its mid is in [lo,hi], up to CAP shares
  FAV_FLIPSTOP  same, but no new buys once the favourite has flipped (mid <= .4)  [post-flip stop, no direction view]
  FAV_ONCE   buy only until a share budget is used in the first SEG seconds after the decision (front-loaded, then hold)
  MAKER      neutral two-sided passive bids at the best bid (random fills with prob pf per side per step), hold; repair when
             |UP-DOWN| > D by taking the lacking side at the ask
  MAKER_NOREP  as MAKER without repair (pure inventory, no direction view)
"""
import argparse, math, random, statistics as S
from toy_floor_sim2 import make_path


def run(path, up_wins, strat, rng, adv, half=.01, tick=15., lo=.60, hi=.80, cap=300., pf=.15, D=60., dec_t=12, h=0.):
    inv = {'UP': 0., 'DOWN': 0.}; cost = 0.; flipped = False; fav = None; nbuy = 0
    for t, mid in enumerate(path):
        if t >= 290: break
        ask = {'UP': min(.99, mid + half), 'DOWN': min(.99, 1 - mid + half)}
        bid = {'UP': max(.01, mid - half), 'DOWN': max(.01, 1 - mid - half)}
        if t == dec_t: fav = 'UP' if mid >= .5 else 'DOWN'
        if fav is not None and not flipped:
            fm = mid if fav == 'UP' else 1 - mid
            if fm <= .4: flipped = True
        if strat.startswith('FAV'):
            if t < dec_t or t % 2: continue
            cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
            if strat == 'FAV_FLIPSTOP' and flipped: continue
            if not (lo <= cm <= hi): continue
            if inv['UP'] + inv['DOWN'] >= cap: continue
            inv[cur] += tick; cost += tick * ask[cur]; nbuy += 1
            if h > 0:  # insurance: also buy h*tick of the opposite side at its ask (costs the spread twice, cuts the reversal tail)
                opp = 'DOWN' if cur == 'UP' else 'UP'; inv[opp] += h * tick; cost += h * tick * ask[opp]
        else:
            for s in ('UP', 'DOWN'):
                if rng.random() < pf and inv[s] < 1500:
                    inv[s] += tick; cost += tick * min(.99, bid[s] + adv)
            if strat == 'MAKER' and abs(inv['UP'] - inv['DOWN']) > D:
                lack = 'UP' if inv['UP'] < inv['DOWN'] else 'DOWN'
                inv[lack] += tick; cost += tick * ask[lack]
    pnl = (inv['UP'] if up_wins else inv['DOWN']) - cost
    return pnl, cost, flipped, fav


def tail(xs, q=.05):
    s = sorted(xs); return S.fmean(s[:max(1, int(len(s) * q))])


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--n', type=int, default=4000); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--noise', type=float, default=.02); ap.add_argument('--adv', type=float, default=.025); ap.add_argument('--shrink', type=float, default=.286)
    ap.add_argument('--tail', type=float, default=100.)
    a = ap.parse_args(); rng = random.Random(a.seed)
    P = [make_path(rng, noise=a.noise, shrink=a.shrink) for _ in range(a.n)]
    cfgs = [('FAV cap300', 'FAV', dict(cap=300.)), ('FAV cap600', 'FAV', dict(cap=600.)), ('FAV_FLIPSTOP cap300', 'FAV_FLIPSTOP', dict(cap=300.)),
            ('FAV_FLIPSTOP cap600', 'FAV_FLIPSTOP', dict(cap=600.)), ('FAV .6-.7 cap300', 'FAV', dict(cap=300., lo=.6, hi=.7)),
            ('FAV hedge .25 cap300', 'FAV', dict(cap=300., h=.25)), ('FAV hedge .5 cap300', 'FAV', dict(cap=300., h=.5)), ('FAV hedge .5 cap600', 'FAV', dict(cap=600., h=.5)),
            ('MAKER pf.15 D60', 'MAKER', dict(pf=.15, D=60.)), ('MAKER pf.15 D150', 'MAKER', dict(pf=.15, D=150.)), ('MAKER_NOREP pf.15', 'MAKER_NOREP', dict(pf=.15))]
    print('world: shrink %.3f (fav underpriced), adv %.3f (passive adverse selection), n=%d paths; sizes rescaled so worst-5%% mean = -%.0f' % (a.shrink, a.adv, a.n, a.tail))
    print('%-22s %8s %9s %8s | %9s %9s | %8s | %-22s' % ('strategy', 'mean', 'worst5%', 'cost', 'mean@tail', 'ret/cost', 'P(loss)', 'mean: no-flip / flip'))
    for name, st, kw in cfgs:
        r2 = random.Random(a.seed + 7); out = [run(p, w, st, r2, a.adv, **kw) for p, w in P]
        pn = [o[0] for o in out]; m = S.fmean(pn); t5 = tail(pn)
        scale = a.tail / abs(t5) if t5 < 0 else float('nan')
        nf = [o[0] for o in out if not o[2]]; fl = [o[0] for o in out if o[2]]
        print('%-22s %8.1f %9.1f %8.0f | %9.1f %8.1f%% | %7.0f%% | %8.1f / %8.1f' % (name, m, t5, S.fmean(o[1] for o in out), m * scale,
              100 * m / S.fmean(o[1] for o in out), 100 * sum(x < 0 for x in pn) / len(pn), S.fmean(nf) if nf else float('nan'), S.fmean(fl) if fl else float('nan')))


if __name__ == '__main__':
    main()
