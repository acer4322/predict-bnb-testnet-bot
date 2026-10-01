"""TOY v2: V8-style two-sided passive manager + PADD + CG1 freeze + ratcheting post-FLIP risk floor, calibrated by moment matching
to the 217 collected CG1AT paths (targets in TARGETS). Still a fair-price world: no edge, optional passive adverse selection (--adv).
Mechanism screening only; never evidence of real profitability.

  python tools/toy_floor_sim2.py calibrate [--n 1000]      # small grid, prints residuals vs TARGETS
  python tools/toy_floor_sim2.py run --noise .04 --ar .9 --pfill .1 [--n 3000] [--adv 0.0] [--floor ON|OFF|variant]
"""
import argparse, itertools, math, random, statistics as S
from toy_floor_sim import phi

MARGIN = {'MARGIN50': 50., 'MARGIN100': 100.}
TARGETS = dict(flip_share=.64, flip_t_med=51.5, cost_flip_med=338., cost_flip_p90=1103., cost_noflip_hc_med=948.,
               cost_at_flip_med=218., worst_at_flip_med=-91.5, best_at_flip_med=83.6, heavy_share=.86, first_block_med=1.2,
               lowconv_share=.55, nfl0=.36, nfl1=.30, nfl2=.12, nfl3p=.22)


def make_path(rng, T=300, noise=.04, ar=.9):
    x, e, out = 0., 0., []
    sd = 1 / math.sqrt(T)
    for t in range(T):
        x += rng.gauss(0, sd)
        p = phi(x / math.sqrt(max(T - 1 - t, 1) / T)) if t < T - 1 else (1. if x > 0 else 0.)
        e = ar * e + rng.gauss(0, noise * math.sqrt(1 - ar * ar))
        out.append(min(.99, max(.01, p + e)))
    return out, x > 0


def run(path, up_wins, floor, pfill, draws, half=.01, amp=.64, flipd=.10, ticket=15., th=.56, adv=0., wk=.3, opening_skew=1.):
    """floor: 'OFF' | 'ON' (code-faithful ratchet) | 'ONCE' (floor fixed at the first-FLIP worst branch, no ratchet) | 'MARGIN50/100' (ratchet, allowed to sit that far below). Note: RESET-at-FLIP and share-crediting variants were tried and are identical to ON by construction (the binding branch is always the one paying the cost)."""
    u, bias = draws; bias *= opening_skew
    inv = {'UP': 0., 'DOWN': 0.}; cost = 0.; fl = None; strong = None; flips = 0; frozen = False
    att = blk = h0 = 0; first_block = first_flip = None; at_flip = None; m0 = None; dec_t = None

    def pay(s): return inv[s] - cost

    def buy(side, price, q, post):
        nonlocal cost, att, blk, h0, first_block
        c = price * q
        if post and floor != 'OFF':
            att += 1; other = 'DOWN' if side == 'UP' else 'UP'
            after = {s: pay(s) for s in inv}; after[other] -= c
            if min(after.values()) < fl - MARGIN.get(floor, 0.) - 1e-8:
                blk += 1; first_block = t if first_block is None else first_block
                if pay(other) - fl <= 1e-8: h0 += 1
                return False
        elif post:
            att += 1
        inv[side] += q; cost += c
        return True

    for t, mid in enumerate(path):
        if t >= 290: break
        ask = {'UP': min(.99, mid + half), 'DOWN': min(.99, 1 - mid + half)}
        bid = {'UP': max(.01, mid - half), 'DOWN': max(.01, 1 - mid - half)}
        if flips and fl is not None and floor != 'ONCE':
            fl = max(fl, min(pay('UP'), pay('DOWN')))
        gross = inv['UP'] + inv['DOWN']
        if strong is None:
            if gross >= 300:
                strong = 'UP' if mid >= .5 else 'DOWN'; m0 = mid if strong == 'UP' else 1 - mid; dec_t = t
                frozen = m0 < th
            else:
                for s in ('UP', 'DOWN'):
                    if u[t][0 if s == 'UP' else 1] < (.55 + bias if s == 'UP' else .55 - bias): buy(s, ask[s], ticket, False)
                continue
        smid = mid if strong == 'UP' else 1 - mid
        if smid <= .5 - flipd + 1e-9:
            strong = 'DOWN' if strong == 'UP' else 'UP'; flips += 1; frozen = False
            if fl is None:
                fl = min(pay('UP'), pay('DOWN'))
            if first_flip is None:
                first_flip = t; at_flip = (cost, min(pay('UP'), pay('DOWN')), max(pay('UP'), pay('DOWN')))
        post = flips > 0
        if frozen: continue
        weak = 'DOWN' if strong == 'UP' else 'UP'
        G = math.exp(6.8 + 2 * t / 300)
        des = {strong: G * (1 + amp) / 2, weak: G * (1 - amp) / 2}
        for s in (strong, weak):
            if inv[s] < des[s] and u[t][0 if s == 'UP' else 1] < pfill * (wk if s == weak else 1.):
                p = min(.99, bid[s] + adv)  # passive fill; adv = adverse selection, effective price paid above the bid
                buy(s, p, ticket, post)
        if t % 2 == 0 and pay(strong) < 0 and ask[strong] <= .8:
            buy(strong, ask[strong], ticket, post)
    pnl = (inv['UP'] if up_wins else inv['DOWN']) - cost
    grp = 'NO_DECIDE' if strong is None and dec_t is None else ('NO_FLIP' if flips == 0 else 'FLIP')
    return dict(pnl=pnl, cost=cost, flips=flips, att=att, blk=blk, h0=h0, first_block=first_block, first_flip=first_flip, at_flip=at_flip,
                low=(m0 is not None and m0 < th), dec=dec_t, grp=grp, m0=m0, ft=(first_flip - dec_t) if first_flip is not None and dec_t is not None else None)


def metrics(rs):
    d = [r for r in rs if r['dec'] is not None]; f = [r for r in d if r['flips']]
    fa = [r for r in f if r['att'] > 0]
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))]
    m = dict(flip_share=len(f) / len(d), flip_t_med=S.median([r['ft'] for r in f]),
             cost_flip_med=S.median([r['cost'] for r in f]), cost_flip_p90=q([r['cost'] for r in f], .9),
             cost_noflip_hc_med=S.median([r['cost'] for r in d if not r['flips'] and not r['low']] or [0]),
             cost_at_flip_med=S.median([r['at_flip'][0] for r in f]), worst_at_flip_med=S.median([r['at_flip'][1] for r in f]),
             best_at_flip_med=S.median([r['at_flip'][2] for r in f]),
             heavy_share=sum(r['blk'] / r['att'] > .5 for r in fa) / max(1, len(fa)),
             first_block_med=S.median([r['first_block'] - r['first_flip'] for r in fa if r['first_block'] is not None] or [float('nan')]),
             lowconv_share=sum(r['low'] for r in d) / len(d))
    nf = [min(r['flips'], 3) for r in d]
    for k, key in enumerate(('nfl0', 'nfl1', 'nfl2', 'nfl3p')): m[key] = sum(x == k for x in nf) / len(nf)
    return m


def sim(n, seed, noise, ar, pfill, floor='ON', adv=0., wk=.3):
    return [run(p, w, floor, pfill, d, adv=adv, wk=wk) for p, w, d in paths(n, seed, noise, ar)]


def paths(n, seed, noise, ar):
    rng = random.Random(seed); out = []
    for _ in range(n):
        p, w = make_path(rng, noise=noise, ar=ar)
        out.append((p, w, ([[rng.random(), rng.random()] for _ in range(300)], rng.uniform(-.35, .35))))
    return out


def cvar(xs, q=.05):
    xs = sorted(xs); return S.fmean(xs[:max(1, int(len(xs) * q))])


def compare(a):
    P = paths(a.n, a.seed, a.noise, a.ar)
    for adv in (0., .015, .025):
        res = {v: [run(p, w, v, a.pfill, d, adv=adv, wk=a.wk) for p, w, d in P] for v in ('OFF', 'ON', 'ONCE', 'MARGIN50', 'MARGIN100')}
        base = res['OFF']
        print('\n== adv %.3f (passive fills pay bid+adv) | n=%d paired vs floor OFF' % (adv, a.n))
        print(' %-6s %8s %9s | %-22s | %-22s | %-22s | %6s' % ('floor', 'mean', 'cvar5', 'FLIP only mean (paired d)', 'NO_FLIP mean', 'worst path', 'cost'))
        for v, rs in res.items():
            d = [r['pnl'] - b['pnl'] for r, b in zip(rs, base)]
            se = S.pstdev(d) / math.sqrt(len(d))
            fl = [i for i, r in enumerate(rs) if r['grp'] == 'FLIP']
            dfl = [d[i] for i in fl]
            print(' %-6s %8.1f %9.1f | %8.1f (%+6.1f +-%4.1f)  | %8.1f              | %8.1f              | %6.0f' % (
                v, S.fmean(r['pnl'] for r in rs), cvar([r['pnl'] for r in rs]), S.fmean(rs[i]['pnl'] for i in fl), S.fmean(dfl), 2 * S.pstdev(dfl) / math.sqrt(len(dfl)),
                S.fmean(r['pnl'] for r in rs if r['grp'] == 'NO_FLIP'), min(r['pnl'] for r in rs), S.fmean(r['cost'] for r in rs)))
            if v != 'OFF':
                print('        paired diff vs OFF, all paths: %+.2f +- %.2f (2 SE)' % (S.fmean(d), 2 * se))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('mode', choices=['calibrate', 'run', 'compare'])
    ap.add_argument('--n', type=int, default=1000); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--noise', type=float, default=.04); ap.add_argument('--ar', type=float, default=.9)
    ap.add_argument('--pfill', type=float, default=.1); ap.add_argument('--wk', type=float, default=.3); ap.add_argument('--adv', type=float, default=0.)
    a = ap.parse_args()
    if a.mode == 'calibrate':
        keys = list(TARGETS); best = []
        for noise, ar, pf, wk in itertools.product((.02, .03), (.9,), (.2, .4, .8), (.3, .6)):
            m = metrics(sim(a.n, a.seed, noise, ar, pf, wk=wk))
            scale = {k: (abs(TARGETS[k]) if abs(TARGETS[k]) > 1.5 else 1.) for k in keys}
            err = S.fmean(abs(m[k] - TARGETS[k]) / (scale[k] if scale[k] != 1. else max(.1, abs(TARGETS[k]))) for k in keys)
            best.append((err, noise, ar, (pf, wk), m)); print('noise %.2f ar %.2f pfill %.2f wk %.1f -> mean rel err %.3f' % (noise, ar, pf, wk, err), flush=True)
        best.sort(key=lambda x: x[0])
        for err, noise, ar, pf, m in best[:2]:
            print('\nBEST noise %.2f ar %.2f (pfill,wk) %s err %.3f' % (noise, ar, pf, err))
            for k in keys: print('  %-20s sim %9.2f  real %9.2f' % (k, m[k], TARGETS[k]))
    elif a.mode == 'compare':
        compare(a)
    else:
        m = metrics(sim(a.n, a.seed, a.noise, a.ar, a.pfill, adv=a.adv, wk=a.wk))
        for k in TARGETS: print('  %-20s sim %9.2f  real %9.2f' % (k, m[k], TARGETS[k]))


if __name__ == '__main__':
    main()
