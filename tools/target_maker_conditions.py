"""Where is Target's real maker-fill value positive?  Discovery = first 60% of markets (by id), confirmation = last 40%; a cell 'replicates' only if the market-clustered
bootstrap 95% CI lower bound of the +5 s value is > 0 in BOTH.  Features (all knowable at placement unless marked *): offset ticks below best bid, side-mid level bin at fill (price level),
best-bid move between placement and fill* (adverse selection signal: price fell through), resting time*, qty.   usage: python tools/target_maker_conditions.py target_lifecycle_features.json.gz"""
import sys, gzip, json, collections, random
d = json.load(gzip.open(sys.argv[1])); rng = random.Random(7)
ok = [r for r in d if None not in (r['mid_side_at_fill_plus5s'], r['best_bid_at_placement'], r['mid_side_at_fill'], r['best_bid_at_fill'])]
mk = sorted({r['market_id'] for r in ok}); cut = mk[int(.6 * len(mk))]; D = [r for r in ok if r['market_id'] < cut]; T = [r for r in ok if r['market_id'] >= cut]
wm = lambda rs: sum(r['filled_qty'] * (r['mid_side_at_fill_plus5s'] - r['price']) for r in rs) / sum(r['filled_qty'] for r in rs)
def ci(rs, nb=300):
    by = collections.defaultdict(list)
    for r in rs: by[r['market_id']].append(r)
    ids = list(by); ms = sorted(wm([r for i in (rng.choice(ids) for _ in ids) for r in by[i]]) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
off = lambda r: round((r['best_bid_at_placement'] - r['price']) / .01)
mv = lambda r: round((r['best_bid_at_fill'] - r['best_bid_at_placement']) / .01)
F = {'offset': lambda r: '0' if off(r) == 0 else '1-2' if off(r) <= 2 else '3-4' if off(r) <= 4 else '>=5',
     'level(sidemid)': lambda r: '<.3' if r['mid_side_at_fill'] < .3 else '.3-.5' if r['mid_side_at_fill'] < .5 else '.5-.7' if r['mid_side_at_fill'] < .7 else '>=.7',
     'bid_move*': lambda r: 'fell>=2t' if mv(r) <= -2 else 'fell1t' if mv(r) == -1 else 'same' if mv(r) == 0 else 'rose',
     'resting*': lambda r: '<300ms' if r['resting_ms'] < 300 else '<1s' if r['resting_ms'] < 1000 else '>=1s'}
print('markets %d (discovery <%d: %d fills, confirm: %d fills); ALL: disc %+.2fc conf %+.2fc' % (len(mk), cut, len(D), len(T), 100 * wm(D), 100 * wm(T)))
print('%-15s %-9s | %5s %7s %17s | %5s %7s %17s | repl' % ('feature', 'bin', 'd_n', 'd mean', 'd 95% CI', 't_n', 't mean', 't 95% CI'))
def cells(rs): c = collections.defaultdict(list); [c[(k, f(r))].append(r) for r in rs for k, f in F.items()]; return c
cd, ct = cells(D), cells(T)
for key in sorted(cd):
    a, b = cd[key], ct.get(key, [])
    if len({r['market_id'] for r in a}) < 10 or len({r['market_id'] for r in b}) < 10: continue
    l1, h1 = ci(a); l2, h2 = ci(b)
    print('%-15s %-9s | %5d %+7.2f [%+6.2f,%+6.2f] | %5d %+7.2f [%+6.2f,%+6.2f] | %s' % (key[0], key[1], len(a), 100 * wm(a), 100 * l1, 100 * h1, len(b), 100 * wm(b), 100 * l2, 100 * h2, 'YES' if l1 > 0 and l2 > 0 else ''))
# the two-way cell that matters for copying: offset x level
print('\noffset x level (replicates only):')
G = lambda r: (F['offset'](r), F['level(sidemid)'](r)); c1, c2 = collections.defaultdict(list), collections.defaultdict(list)
for r in D: c1[G(r)].append(r)
for r in T: c2[G(r)].append(r)
for k in sorted(c1):
    a, b = c1[k], c2.get(k, [])
    if len({r['market_id'] for r in a}) < 10 or len({r['market_id'] for r in b}) < 10: continue
    l1, _ = ci(a); l2, _ = ci(b)
    print('%-8s %-6s d %+6.2f (n %d) t %+6.2f (n %d) %s' % (k[0], k[1], 100 * wm(a), len(a), 100 * wm(b), len(b), 'YES' if l1 > 0 and l2 > 0 else ''))
