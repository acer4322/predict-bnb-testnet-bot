"""Are the Target's maker fills less adversely selected by Binance spot than a mechanical ladder's fills?  For each fill: BTCUSDT 1 s closes; signed (toward the bought side: UP +, DOWN -) log return
over the 5 s BEFORE the fill and the 5 s / 30 s AFTER, in bps, qty-weighted.  Groups: Target maker (new-era export, second-granular), Target taker (export), Target maker (old-era lifecycle, ms), ladder front-bound fills.
usage: python tools/maker_spot_selection.py target_fills.json.gz target_lifecycle.json.gz ladder_fills.json spot1s.json.gz [more spot files]"""
import sys, json, gzip, math, collections
ex_f, lc_f, ld_f, *spf = sys.argv[1:]
sp = {}
for f in spf: sp.update({int(k): v[0] for k, v in json.load(gzip.open(f, 'rt')).items()})
def ret(a, b):
    x, y = sp.get(a), sp.get(b); return math.log(y / x) * 1e4 if x and y else None
def stats(tag, items):  # items: (ms, side, qty)
    acc = collections.defaultdict(lambda: [0., 0.]); n = 0
    for ms, side, q in items:
        s = ms // 1000; sg = 1 if side == 'UP' else -1; vals = {'prev5': ret(s - 6, s - 1), 'next5': ret(s - 1, s + 5), 'next30': ret(s - 1, s + 30)}
        if any(v is None for v in vals.values()): continue
        n += 1
        for k, v in vals.items(): acc[k][0] += q * sg * v; acc[k][1] += q
    print('%-34s n=%6d | signed spot bps (toward bought side): prev5 %+.2f  next5 %+.2f  next30 %+.2f' % (tag, n, *(acc[k][0] / acc[k][1] for k in ('prev5', 'next5', 'next30'))))
ex = json.loads(gzip.open(ex_f).read())
stats('Target MAKER (new era, sec)', [(r['event_ms'], r['side'], r['shares']) for r in ex if r['role'] == 'MAKER'])
stats('Target TAKER (new era, sec)', [(r['event_ms'], r['side'], r['shares']) for r in ex if r['role'] == 'TAKER'])
lc = json.load(gzip.open(lc_f))
stats('Target MAKER (old era, ms)', [(int(r['placement_first_ms']) + int(r['resting_ms']), r['side'], r['filled_qty']) for r in lc if r['placement_first_ms'] and r['resting_ms'] is not None])
ld = json.load(open(ld_f))
stats('Ladder front-bound fills (ours)', [(ms, side, q) for m, ms, side, q, px, rest in ld])
stats('  ladder fills on old-era ids <2e6', [(ms, side, q) for m, ms, side, q, px, rest in ld if m < 2_000_000])
stats('  ladder fills on fresh ids >2.8e6', [(ms, side, q) for m, ms, side, q, px, rest in ld if m > 2_800_000])
