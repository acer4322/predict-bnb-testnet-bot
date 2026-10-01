"""Same measurement as target_maker_value.py but on the Target export of the NEWER era (105 markets, id 2648950-2685217; fills have second-granular timestamps) joined to the public books:
offset = (side best bid 1 s before the fill - fill price)/tick, value = side mid at fill+5 s - price (qty-weighted, market-clustered bootstrap).  usage: python tools/target_maker_value_new_era.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, random, pathlib
WS = {}
import real_replay_stop as rr
rows = json.loads(gzip.open(sys.argv[1]).read()); M = rr.load(sys.argv[2], {}); rng = random.Random(3); out = []
for r in rows:
    if r['role'] != 'MAKER': continue
    m = int(r['market_id'])
    if m not in M: continue
    mk = M[m]
    if m not in WS: WS[m] = int(rr.jl(next(p for p in pathlib.Path(sys.argv[2]).rglob('public_%d.json.gz' % m) if 'parity' not in str(p)))['market']['window_start_ms'])
    ws = WS[m]
    t = (int(r['event_ms']) - ws) / 1000.
    b0, b1, b5 = rr.at(mk, t - 1.), rr.at(mk, t), rr.at(mk, t + 5.)
    if None in (b0, b1, b5) or t < 0 or t + 5 > mk['ts'][-1]: continue
    up = r['side'] == 'UP'; sm = (lambda b: (b['best_bid'] + b['best_ask']) / 2) if up else (lambda b: 1 - (b['best_bid'] + b['best_ask']) / 2)
    bid = b0['best_bid'] if up else 1 - b0['best_ask']; px = float(r['price'])
    out.append(dict(m=m, q=float(r['shares']), off=round((bid - px) / .01), inst=sm(b1) - px, v5=sm(b5) - px))
wm = lambda rs, k: sum(r['q'] * r[k] for r in rs) / sum(r['q'] for r in rs)
def ci(rs, nb=300):
    by = collections.defaultdict(list)
    for r in rs: by[r['m']].append(r)
    ids = list(by); ms = sorted(wm([r for i in (rng.choice(ids) for _ in ids) for r in by[i]], 'v5') for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
print('Target maker fills in the new era: n=%d, markets %d' % (len(out), len({r['m'] for r in out})))
for tag, f in (('ALL', lambda r: True), ('offset<=0 (at/above best bid)', lambda r: r['off'] <= 0), ('offset 1-2', lambda r: 1 <= r['off'] <= 2), ('offset 3-4', lambda r: 3 <= r['off'] <= 4), ('offset >=5', lambda r: r['off'] >= 5)):
    rs = [r for r in out if f(r)]
    if len(rs) > 30: print('%-30s n=%5d instant %+.2fc | +5s %+.2fc [%+.2f,%+.2f]' % (tag, len(rs), 100 * wm(rs, 'inst'), 100 * wm(rs, 'v5'), *[100 * x for x in ci(rs)]))
