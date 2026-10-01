"""Target's REAL maker fills (research_pack/target/target_lifecycle_features.json.gz: 11,084 inferred maker orders, 150 markets, ids 1704510-1764475):
value at +5 s (side mid at fill+5 s minus fill price, qty-weighted, market-clustered bootstrap CI), by placement offset below the best bid, resting time and post action.
usage: python tools/target_maker_value.py target_lifecycle_features.json.gz"""
import sys, gzip, json, collections, random, statistics as S
d = json.load(gzip.open(sys.argv[1]))
ok = [r for r in d if None not in (r['mid_side_at_fill_plus5s'], r['best_bid_at_placement'], r['mid_side_at_fill'])]
wm = lambda rs, k: (sum(r['filled_qty'] * k(r) for r in rs) / sum(r['filled_qty'] for r in rs)) if rs else float('nan')
v5 = lambda r: r['mid_side_at_fill_plus5s'] - r['price']; inst = lambda r: r['mid_side_at_fill'] - r['price']; rng = random.Random(1)
def ci(rs, k, nb=300):
    by = collections.defaultdict(list)
    for r in rs: by[r['market_id']].append(r)
    ids = list(by); ms = sorted(wm([r for i in (rng.choice(ids) for _ in ids) for r in by[i]], k) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
def row(tag, rs): print('%-34s n=%5d qty %7.0f | instant %+.2fc | +5s %+.2fc [%+.2f,%+.2f]' % (tag, len(rs), sum(r['filled_qty'] for r in rs), 100 * wm(rs, inst), 100 * wm(rs, v5), *[100 * x for x in ci(rs, v5)]))
row('ALL', ok)
off = lambda r: round((r['best_bid_at_placement'] - r['price']) / .01)
for o in (0, 1, 2, 3, 4):
    rs = [r for r in ok if off(r) == o]; row('offset %d tick(s) below best bid' % o, rs) if len(rs) > 100 else None
rs = [r for r in ok if off(r) >= 5]; row('offset >=5 ticks', rs) if len(rs) > 100 else None
for lo, hi in ((0, 300), (300, 1000), (1000, 5000)): row('resting %d-%d ms' % (lo, hi), [r for r in ok if lo <= r['resting_ms'] < hi])
for k in sorted({r['post_action'] for r in ok}): row(k[:34], [r for r in ok if r['post_action'] == k])
