"""Is there a CONDITION under which Target's maker fills in the NEW era (105 markets, ids 2648950-2685217, joined to public books) have positive +5 s value?  Discovery = first 60% of markets, confirmation = last 40%;
a cell replicates only if the market-clustered bootstrap CI lower bound of the qty-weighted +5 s value is > 0 in BOTH.  Features at 1 s before the fill (second-granular fill times, so noisy): offset below best bid (ticks),
spread (1 tick vs wider), side-mid change over the previous 5 s, seconds since window start, fav/underdog side (side mid >= .5).   usage: python tools/target_newera_conditions.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, random, pathlib
import real_replay_stop as rr
rows = json.loads(gzip.open(sys.argv[1]).read()); M = rr.load(sys.argv[2], {}); rng = random.Random(5); WS = {}; out = []
for r in rows:
    if r['role'] != 'MAKER': continue
    m = int(r['market_id'])
    if m not in M: continue
    mk = M[m]
    if m not in WS: WS[m] = int(rr.jl(next(p for p in pathlib.Path(sys.argv[2]).rglob('public_%d.json.gz' % m) if 'parity' not in str(p)))['market']['window_start_ms'])
    t = (int(r['event_ms']) - WS[m]) / 1000.; b0, b5, bm = rr.at(mk, t - 1.), rr.at(mk, t + 5.), rr.at(mk, t - 6.)
    if None in (b0, b5, bm) or t < 6 or t + 5 > mk['ts'][-1]: continue
    up = r['side'] == 'UP'; mid = lambda b: (b['best_bid'] + b['best_ask']) / 2; sm = (lambda b: mid(b)) if up else (lambda b: 1 - mid(b)); px = float(r['price'])
    bid = b0['best_bid'] if up else 1 - b0['best_ask']
    out.append(dict(m=m, q=float(r['shares']), off=round((bid - px) / .01), v5=sm(b5) - px, sp=round(b0['best_ask'] - b0['best_bid'], 2), dm=sm(b0) - sm(bm), t=t, lvl=sm(b0)))
wm = lambda rs: sum(r['q'] * r['v5'] for r in rs) / sum(r['q'] for r in rs)
def ci(rs, nb=250):
    by = collections.defaultdict(list)
    for r in rs: by[r['m']].append(r)
    ids = list(by); ms = sorted(wm([r for i in (rng.choice(ids) for _ in ids) for r in by[i]]) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
mk_ = sorted({r['m'] for r in out}); cut = mk_[int(.6 * len(mk_))]; D = [r for r in out if r['m'] < cut]; T = [r for r in out if r['m'] >= cut]
F = {'offset': lambda r: '<=0' if r['off'] <= 0 else '1-2' if r['off'] <= 2 else '3-4' if r['off'] <= 4 else '>=5', 'spread': lambda r: '1tick' if r['sp'] <= .0101 else 'wider',
     'dm5(prev 5s)': lambda r: 'fell>=2c' if r['dm'] <= -.02 else 'rose>=2c' if r['dm'] >= .02 else 'flat', 'time': lambda r: '<60s' if r['t'] < 60 else '60-180' if r['t'] < 180 else '>=180s',
     'side': lambda r: 'fav(>=.5)' if r['lvl'] >= .5 else 'underdog'}
print('markets %d, fills %d (disc %d, conf %d); ALL disc %+.2fc conf %+.2fc' % (len(mk_), len(out), len(D), len(T), 100 * wm(D), 100 * wm(T)))
def cells(rs, keys): c = collections.defaultdict(list); [c[tuple(f(r) for f in keys)].append(r) for r in rs]; return c
def scan(names):
    keys = [F[n] for n in names]; cd, ct = cells(D, keys), cells(T, keys); hits = 0
    for k in sorted(cd):
        a, b = cd[k], ct.get(k, [])
        if len({r['m'] for r in a}) < 8 or len({r['m'] for r in b}) < 8: continue
        l1, _ = ci(a); l2, _ = ci(b)
        if l1 > 0 and l2 > 0: hits += 1; print('  REPLICATES %s %s: disc %+.2fc (n %d) conf %+.2fc (n %d)' % (names, k, 100 * wm(a), len(a), 100 * wm(b), len(b)))
    return hits
tot = 0
for n in F: tot += scan([n])
for a_, b_ in (('offset', 'spread'), ('offset', 'dm5(prev 5s)'), ('offset', 'time'), ('offset', 'side')): tot += scan([a_, b_])
print('replicating cells: %d' % tot)
