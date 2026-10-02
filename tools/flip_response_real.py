"""Real-book replay of the flip-response policies (flip_response_check.py) on a pre-declared 30-market stratified sample:
10 NO_FLIP + 10 FALSE_FLIP + 10 TRUE_FLIP from the LABELLED markets, evenly spaced by market id inside each class (no PnL look).
Book total uses the equal-stratum means, so the 'overall' mean is not reported (sample is stratified)."""
import sys, random, statistics as S
import real_replay_stop as rr


def run_policy(mk, pol, delay=1., lo=.60, hi=.80, cap=300., tick=15., dec_t=12.):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; fav = None; flipped = False; stopped = False; t = dec_t
    while t < 290.:
        b = rr.at(mk, t)
        if b is None: t += 2; continue
        mid = (b['best_bid'] + b['best_ask']) / 2; bx = rr.at(mk, t + delay) or b
        if fav is None: fav = 'UP' if mid >= .5 else 'DOWN'
        fm = mid if fav == 'UP' else 1 - mid
        if not flipped and t > dec_t and fm <= .4:
            flipped = True; opp = 'DOWN' if fav == 'UP' else 'UP'
            if pol == 'FREEZE': stopped = True
            elif pol in ('SELL', 'SELL_REBUY', 'SELLHALF'):
                f = .5 if pol == 'SELLHALF' else 1.
                for s_ in ('UP', 'DOWN'): net -= f * inv[s_] * max(.01, rr.quotes(bx, s_)[1]); inv[s_] *= 1 - f
                stopped = True
            elif pol.startswith('HEDGE'):
                tot = inv['UP'] + inv['DOWN']; h = float(pol[5:]); inv[opp] += h * tot; net += h * tot * min(.99, rr.quotes(bx, opp)[0]); stopped = True
        if pol == 'SELL_REBUY' and stopped and flipped and fm >= .6: stopped = False; flipped = False
        if not stopped:
            cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
            if lo <= cm <= hi and inv['UP'] + inv['DOWN'] < cap: inv[cur] += tick; net += tick * min(.99, rr.quotes(bx, cur)[0])
        t += 2.
    return inv[mk['win']] - net


def main():
    lab = {int(r['market_id']): r['winner'] for r in rr.jl(sys.argv[2]).get('records', [])}
    M = {m: v for m, v in rr.load(sys.argv[1], lab).items() if v['labelled']}
    cls = {m: rr.classify(v) for m, v in M.items()}; pick = {}
    for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
        ids = sorted(m for m in M if cls[m] == k); n = min(10, len(ids)); pick[k] = [ids[int(i * len(ids) / n)] for i in range(n)]
        print(k, 'available', len(ids), 'picked', n)
    rng = random.Random(3)
    print('%-11s | %8s %8s %8s | rev-mean vs limit | 4/3/3 book | worst TRUE | worst any' % ('policy', 'noflip', 'false', 'true'))
    for pol in ('BASE', 'FREEZE', 'SELL', 'SELLHALF', 'SELL_REBUY', 'HEDGE0.5', 'HEDGE1.0'):
        r = {k: [run_policy(M[m], pol) for m in pick[k]] for k in pick}; mn = {k: S.fmean(v) for k, v in r.items()}
        rev = r['FALSE_FLIP'] + r['TRUE_FLIP']
        bs = lambda k: sorted(S.fmean(rng.choice(r[k]) for _ in r[k]) for _ in range(400))
        bf, bt = bs('FALSE_FLIP'), bs('TRUE_FLIP')
        print('%-11s | %8.1f %8.1f %8.1f | %7.1f vs %7.1f %s | %8.1f | %8.1f | %8.1f   false CI [%.0f,%.0f] true CI [%.0f,%.0f]' % (pol, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], S.fmean(rev), -.5 * mn['NO_FLIP'],
              'Y' if S.fmean(rev) >= -.5 * mn['NO_FLIP'] else '.', 4 * mn['NO_FLIP'] + 3 * mn['FALSE_FLIP'] + 3 * mn['TRUE_FLIP'], min(r['TRUE_FLIP']), min(min(v) for v in r.values()),
              bf[10], bf[390], bt[10], bt[390]))


if __name__ == '__main__': main()
