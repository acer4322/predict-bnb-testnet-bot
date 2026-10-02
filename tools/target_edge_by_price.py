"""Which of the Target's buys earn / lose?  Per fill: edge = 1[side wins] - price (per share, fee-free).  Aggregated by buy price bin, role (MAKER/TAKER) and flip class; plus counterfactual: remove Target's fills outside a price band -> PnL by class (diagnostic; fills cannot be copied).
usage: python tools/target_edge_by_price.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, statistics as S
import real_replay_stop as rr
exp, root = sys.argv[1:3]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; cls = {}
for m, mk in M.items():
    b = rr.at(mk, 12.); F = 'UP' if (b['best_bid'] + b['best_ask']) / 2 >= .5 else 'DOWN'; ft = None
    for t, x in zip(mk['ts'], mk['bs']):
        if t >= 12 and ((x['best_bid'] + x['best_ask']) / 2 if F == 'UP' else 1 - (x['best_bid'] + x['best_ask']) / 2) <= .4: ft = t; break
    cls[m] = 'NO_FLIP' if ft is None else 'FALSE_FLIP' if by[m][0]['winner'] == F else 'TRUE_FLIP'
BINS = [(0, .2), (.2, .35), (.35, .5), (.5, .65), (.65, .8), (.8, 1.01)]
def bi(p): return next(i for i, (a, b) in enumerate(BINS) if a <= p < b)
agg = collections.defaultdict(lambda: [0., 0.])
for m in M:
    for r in by[m]:
        e = r['shares'] * ((1. if r['side'] == r['winner'] else 0.) - r['price'])
        for k in (('ALL', bi(r['price'])), (r['role'], bi(r['price'])), (cls[m], bi(r['price']))): agg[k][0] += e; agg[k][1] += r['shares']
print('edge per share by buy-price bin (sum edge $ / shares); n shares in parentheses')
for key in ('ALL', 'MAKER', 'TAKER', 'NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
    print('%-10s' % key, ' | '.join('%s %+.3f (%d)' % ('%.2f-%.2f' % BINS[i], agg[(key, i)][0] / agg[(key, i)][1], agg[(key, i)][1]) if agg[(key, i)][1] else '%.2f-%.2f  --' % BINS[i] for i in range(len(BINS))))
print('\ncounterfactual: keep only fills with price in [lo,hi]; mean PnL/market by class (n=34/22/49)')
for lo, hi in ((0, 1.01), (.2, .8), (.25, .75), (0.3, .7), (0, .8), (.2, 1.01)):
    o = {}
    for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
        o[k] = S.fmean(sum(r['shares'] * ((1. if r['side'] == r['winner'] else 0.) - r['price']) for r in by[m] if lo <= r['price'] <= hi) for m in M if cls[m] == k)
    print('  [%.2f,%.2f] %s overall %.1f' % (lo, hi, {a: round(b, 1) for a, b in o.items()}, S.fmean(sum(r['shares'] * ((1. if r['side'] == r['winner'] else 0.) - r['price']) for r in by[m] if lo <= r['price'] <= hi) for m in M)))
print('\nsplit-half (by market id) check of band filters, mean PnL/market by class and overall')
ids = sorted(M); h = [ids[:len(ids) // 2], ids[len(ids) // 2:]]
for lo, hi in ((0, 1.01), (.3, .7), (.25, .75)):
    for nm, sub in zip(('first half', 'second half'), h):
        o = {k: [sum(r['shares'] * ((1. if r['side'] == r['winner'] else 0.) - r['price']) for r in by[m] if lo <= r['price'] <= hi) for m in sub if cls[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
        print('  [%.2f,%.2f] %-11s n=%s %s overall %.1f' % (lo, hi, nm, {k: len(v) for k, v in o.items()}, {k: round(S.fmean(v), 1) if v else None for k, v in o.items()}, S.fmean(x for v in o.values() for x in v)))
