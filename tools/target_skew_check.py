"""Does the Target skew its quotes by inventory, or is its inventory driven by which side's price is falling?
For each fill with |I|/T known: long/short side, side mid change over previous 10 s (book), side price level, maker offset below bid (book 1 s before; second-granular -> noisy).
usage: python tools/target_skew_check.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, statistics as S, pathlib
import real_replay_stop as rr
exp, root = sys.argv[1:3]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; WS = {}
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms'])
def smid(m, t, side):
    b = rr.at(M[m], t)
    if not b: return None
    x = (b['best_bid'] + b['best_ask']) / 2; return x if side == 'UP' else 1 - x
def sbid(m, t, side):
    b = rr.at(M[m], t)
    if not b: return None
    return b['best_bid'] if side == 'UP' else round(1 - b['best_ask'], 2)
A = collections.defaultdict(list)
for m in M:
    sh = {'UP': 0., 'DOWN': 0.}
    for r in sorted(by[m], key=lambda r: r['event_ms']):
        t = (r['event_ms'] - WS[m]) / 1000.; I = sh['UP'] - sh['DOWN']; T = sh['UP'] + sh['DOWN']
        if T > 0:
            rb = abs(I) / T; ib = 'bal<15%' if rb < .15 else 'imb>=30%' if rb >= .3 else 'mid'
            long_ = 'UP' if I > 0 else 'DOWN'; pos = 'LONG' if r['side'] == long_ else 'SHORT'
            a, b = smid(m, t - 10, r['side']), smid(m, t, r['side']); bb = sbid(m, t - 1, r['side'])
            if a is not None and b is not None and bb is not None:
                A[(ib, r['role'], pos)].append((r['shares'], b - a, b, round((bb - r['price']) * 100)))
        sh[r['side']] += r['shares']
print('group (imbalance, role, side-vs-inventory) | volume | vol-weighted side mid change last 10 s | side mid level | maker offset ticks below bid (median)')
for k in sorted(A):
    v = A[k]; w = sum(x[0] for x in v)
    print('  %-9s %-5s %-5s | %7.0f | %+.4f | %.3f | %s' % (*k, w, sum(x[0] * x[1] for x in v) / w, sum(x[0] * x[2] for x in v) / w, S.median(x[3] for x in v)))
# Is the long side the falling / cheap side?
L = [x for k, v in A.items() if k[0] == 'imb>=30%' and k[2] == 'LONG' for x in v]
print('\nwhen imbalance>=30%%: fills on LONG side have side mid %.3f (vol-wtd); share of LONG-side volume where side mid < .5: %.0f%%' % (sum(x[0] * x[2] for x in L) / sum(x[0] for x in L), 100 * sum(x[0] for x in L if x[2] < .5) / sum(x[0] for x in L)))
