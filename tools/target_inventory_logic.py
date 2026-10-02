"""Reverse-engineer the Target's inventory logic from its fills (export, 105 markets, buys only, second-granular times).
Per fill (time-ordered within market): running imbalance I = sh_UP - sh_DOWN BEFORE the fill, total held T.  Questions:
 (1) Do MAKER / TAKER fills buy the SHORT side (reduce |I|)?  as a function of |I|/T.
 (2) Imbalance trajectory: distribution of |I|/T over the market; max; how fast |I| shrinks after it grows.
 (3) Price paid by role relative to the 1 s-earlier book (bid/ask of that side): maker at/below bid?  taker at ask?
 (4) Seconds between consecutive fills; burst structure; share of taker fills that come within 2 s after a maker fill on the OTHER side (pair completion?).
usage: python tools/target_inventory_logic.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, statistics as S, pathlib
import real_replay_stop as rr
exp, root = sys.argv[1:3]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; WS = {}
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms'])
def book(m, t):
    b = rr.at(M[m], t)
    if not b: return None
    return {'UP': (b['best_bid'], b['best_ask']), 'DOWN': (round(1 - b['best_ask'], 2), round(1 - b['best_bid'], 2))}
st = collections.defaultdict(lambda: [0., 0.]); price_rel = collections.defaultdict(collections.Counter); imb_path = []; gaps = []; pair_after = [0, 0]
for m in M:
    f = sorted(by[m], key=lambda r: r['event_ms']); sh = {'UP': 0., 'DOWN': 0.}; last = None
    for r in f:
        I = sh['UP'] - sh['DOWN']; T = sh['UP'] + sh['DOWN']; t = (r['event_ms'] - WS[m]) / 1000.
        if T > 0 and abs(I) > 0:
            short = 'DOWN' if I > 0 else 'UP'; rb = abs(I) / T; bin_ = '<5%' if rb < .05 else '5-15%' if rb < .15 else '15-30%' if rb < .3 else '>=30%'
            st[(r['role'], bin_)][0] += r['shares'] * (r['side'] == short); st[(r['role'], bin_)][1] += r['shares']
        bk = book(m, t - 1.)
        if bk:
            bid, ask = bk[r['side']]; d = round((r['price'] - bid) * 100); rel = 'at bid' if d == 0 else ('below bid %d' % -d if d < 0 else ('at ask' if abs(r['price'] - ask) < 1e-9 else 'inside/above bid +%d' % d))
            if d < -3: rel = 'below bid >3'
            if d > 0 and r['price'] > ask + 1e-9: rel = 'above ask'
            price_rel[r['role']][rel] += r['shares']
        if last is not None: gaps.append(t - last[0])
        if r['role'] == 'TAKER' and last is not None and last[1] == 'MAKER' and last[2] != r['side'] and t - last[0] <= 2: pair_after[0] += r['shares']
        if r['role'] == 'TAKER': pair_after[1] += r['shares']
        sh[r['side']] += r['shares']; last = (t, r['role'], r['side'])
        if T > 0: imb_path.append(abs(I) / T)
print('(1) share of fill volume that buys the SHORT side, by role and imbalance |I|/T before the fill (0.5 = no preference)')
for role in ('MAKER', 'TAKER'):
    print('  %-5s' % role, '  '.join('%s %.2f (%.0f sh)' % (b, st[(role, b)][0] / st[(role, b)][1], st[(role, b)][1]) for b in ('<5%', '5-15%', '15-30%', '>=30%') if st[(role, b)][1]))
q = sorted(imb_path); print('(2) |I|/T over all fills: q50 %.3f q90 %.3f q99 %.3f max %.3f' % (q[len(q) // 2], q[int(.9 * len(q))], q[int(.99 * len(q))], q[-1]))
print('(3) fill price relative to that side\'s book 1 s earlier (share of volume):')
for role in ('MAKER', 'TAKER'):
    tot = sum(price_rel[role].values()); print('  %-5s' % role, {k: '%.0f%%' % (100 * v / tot) for k, v in price_rel[role].most_common(8)})
g = sorted(gaps); print('(4) seconds between consecutive fills: q25 %.1f q50 %.1f q75 %.1f ; same-second share %.0f%%' % (g[len(g) // 4], g[len(g) // 2], g[3 * len(g) // 4], 100 * sum(x == 0 for x in g) / len(g)))
print('    taker volume within 2 s after a MAKER fill on the OTHER side: %.0f%%' % (100 * pair_after[0] / pair_after[1]))
