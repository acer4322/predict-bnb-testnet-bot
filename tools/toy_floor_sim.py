"""TOY mechanism simulation of a ratcheting post-FLIP risk floor. NOT the real engine, NOT a strategy evaluation.

World: fair-price market (UP price = P(UP wins) is a martingale: P_t = Phi(X_t / (sigma*sqrt(T-t))), X a Brownian motion), optional
AR(1) mid noise, buys fill at ask = mid + half-spread. No adverse selection, no queue, no edge: expected value of any buying rule
is ~ -spread*qty. So only the *mechanics* (how often the floor freezes trading, tail/variance effects) are meaningful.
Policy (stylised CG1AT): open balanced tickets to GROSS, DECIDE favourite, EXPAND buys of the favourite to a gross cap, PADD buys of
the favourite while its branch is negative (ask<=0.8), FLIP when favourite mid <= 0.5-FLIP, no new orders from 290 s.
Floor (copy of risk_floor.decide): after the first FLIP floor = max(floor, worst branch); a buy of side s for cost c is allowed iff
min(payoff[UP], payoff[DOWN] - c if s=='UP' else ...) >= floor, i.e. the OTHER branch pays the cost; shares are not credited.
"""
import argparse, math, random, statistics as S


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def make_path(rng, T=300, noise=0.0, ar=0.9):
    x, e, out = 0.0, 0.0, []
    sd = 1.0 / math.sqrt(T)
    for t in range(T):
        x += rng.gauss(0, sd)
        rem = max(T - 1 - t, 1)
        p = phi(x / math.sqrt(rem / T)) if t < T - 1 else (1.0 if x > 0 else 0.0)
        e = ar * e + rng.gauss(0, noise * math.sqrt(1 - ar * ar)) if noise else 0.0
        out.append(min(0.99, max(0.01, p + e)))
    return out, x > 0


def run(path, up_wins, floor_on, ticket, half, gross_open=300., cap=1400., flip=0.10):
    inv = {'UP': 0., 'DOWN': 0.}; cost = 0.; floor = None; strong = None; flips = 0
    att = blk = 0; first_block = None; first_flip = None; headroom0 = 0
    decided_side = None

    def pay(s): return inv[s] - cost

    def buy(side, ask, q, post_flip):
        nonlocal cost, att, blk, floor, first_block, headroom0
        c = ask * q
        if post_flip and floor_on:
            att += 1
            other = 'DOWN' if side == 'UP' else 'UP'
            after = {s: pay(s) for s in inv}; after[other] -= c
            if min(after.values()) < floor - 1e-8:
                blk += 1
                if first_block is None: first_block = t
                if pay(other) - floor <= 1e-8: headroom0 += 1
                return False
        elif post_flip:
            att += 1
        inv[side] += q; cost += c
        return True

    for t, mid in enumerate(path):
        if t >= 290: break
        up_ask = min(0.99, mid + half); dn_ask = min(0.99, 1 - mid + half)
        ask = {'UP': up_ask, 'DOWN': dn_ask}
        post = flips > 0
        if post and floor is not None:
            floor = max(floor, min(pay('UP'), pay('DOWN')))
        if strong is None:
            if inv['UP'] + inv['DOWN'] >= gross_open:
                strong = 'UP' if mid >= 0.5 else 'DOWN'; decided_side = strong
            else:
                for s in ('UP', 'DOWN'): buy(s, ask[s], ticket, False)
                continue
        smid = mid if strong == 'UP' else 1 - mid
        if smid <= 0.5 - flip + 1e-9:
            strong = 'DOWN' if strong == 'UP' else 'UP'; flips += 1
            if floor is None:
                floor = min(pay('UP'), pay('DOWN')); first_flip = t
            post = True
        if inv['UP'] + inv['DOWN'] < cap:
            buy(strong, ask[strong], ticket, post)
        if t % 2 == 0 and pay(strong) < 0 and ask[strong] <= 0.8:
            buy(strong, ask[strong], ticket, post)
    pnl = (inv['UP'] if up_wins else inv['DOWN']) - cost
    grp = 'NO_DECIDE' if decided_side is None else ('NO_FLIP' if flips == 0 else ('FALSE_FLIP' if (decided_side == 'UP') == up_wins else 'TRUE_FLIP'))
    return dict(pnl=pnl, cost=cost, flips=flips, att=att, blk=blk, grp=grp, first_block=first_block, first_flip=first_flip, h0=headroom0)


def cvar(xs, q=0.05):
    xs = sorted(xs); k = max(1, int(len(xs) * q)); return S.fmean(xs[:k])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=4000); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--noise', type=float, default=0.03); ap.add_argument('--half', type=float, default=0.01)
    ap.add_argument('--tickets', type=float, nargs='*', default=[15., 5., 1.])
    a = ap.parse_args()
    for ticket in a.tickets:
        rng = random.Random(a.seed)
        paths = [make_path(rng, noise=a.noise) for _ in range(a.n)]
        on = [run(p, w, True, ticket, a.half) for p, w in paths]
        off = [run(p, w, False, ticket, a.half) for p, w in paths]
        fl = [r for r in on if r['flips'] > 0 and r['att'] > 0]
        heavy = [r for r in fl if r['blk'] / r['att'] > .5]
        print('\n== ticket %g | noise %.2f | half-spread %.2f | n=%d' % (ticket, a.noise, a.half, a.n))
        print(' flipped %.0f%% | of flipped: heavy-blocked(>50%% of post-flip attempts) %.0f%%, any block %.0f%%, median first block %s s after flip' % (
            100 * sum(r['flips'] > 0 for r in on) / a.n, 100 * len(heavy) / max(1, len(fl)), 100 * sum(r['blk'] > 0 for r in fl) / max(1, len(fl)),
            S.median([r['first_block'] - r['first_flip'] for r in fl if r['first_block'] is not None] or [float('nan')])))
        print(' %-11s %5s | %-30s | %-30s' % ('group', 'n', 'floor ON  mean / cvar5 / cost', 'floor OFF mean / cvar5 / cost'))
        for g in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP', 'ALL'):
            idx = [i for i, r in enumerate(on) if g == 'ALL' or r['grp'] == g]
            if not idx: continue
            f = lambda rs: '%7.1f / %7.1f / %6.0f' % (S.fmean(r['pnl'] for r in rs), cvar([r['pnl'] for r in rs]), S.fmean(r['cost'] for r in rs))
            print(' %-11s %5d | %-30s | %-30s' % (g, len(idx), f([on[i] for i in idx]), f([off[i] for i in idx])))


if __name__ == '__main__':
    main()
