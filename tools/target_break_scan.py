"""Is the Target's first->second half change a BEHAVIOUR change (abrupt break in its own fill statistics) or a market change (gradual / explained by oscillation)?
Per market in id order: maker-fill side-mid change in the 10 s before the fill (knife-catching), maker +10 s markout (mid(t+10) - price), $ share on side with mid<.5 after 120 s, fills/market, PnL, market path length.  Printed in blocks of 7 markets plus a single-break scan (max |t-stat| of mean difference) for each metric.
usage: python tools/target_break_scan.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, statistics as S, pathlib, math
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
ids = sorted(M); X = collections.defaultdict(list)
for m in ids:
    a = [0., 0.]; mo = [0., 0.]; ud = [0., 0.]
    for r in by[m]:
        t = (r['event_ms'] - WS[m]) / 1000.; x = smid(m, t, r['side']); x10 = smid(m, t - 10, r['side']); xp = smid(m, t + 10, r['side'])
        if r['role'] == 'MAKER' and x is not None and x10 is not None: a[0] += r['shares'] * (x - x10); a[1] += r['shares']
        if r['role'] == 'MAKER' and xp is not None: mo[0] += r['shares'] * (xp - r['price']); mo[1] += r['shares']
        if t >= 120 and x is not None: ud[0] += r['shares'] * r['price'] * (x < .5); ud[1] += r['shares'] * r['price']
    mids = [smid(m, float(t), 'UP') for t in range(12, 291, 2)]; mids = [x for x in mids if x is not None]
    X['knife'].append(a[0] / a[1] if a[1] else 0.); X['markout10'].append(mo[0] / mo[1] if mo[1] else 0.); X['ud_late'].append(ud[0] / ud[1] if ud[1] else .5)
    X['fills'].append(len(by[m])); X['pnl'].append(by[m][0]['net_pnl_usdt']); X['path'].append(sum(abs(p - q) for p, q in zip(mids, mids[1:])))
print('block (7 mkts) |', ' | '.join('%9s' % k for k in X))
for i in range(0, len(ids), 7):
    print('%2d %d |' % (i // 7, ids[i]), ' | '.join('%+9.4f' % S.fmean(v[i:i + 7]) if k not in ('fills', 'pnl') else '%9.1f' % S.fmean(v[i:i + 7]) for k, v in X.items()))
print('\nsingle-break scan (split index with max |t| for the difference of means; split must leave >=15 each side):')
for k, v in X.items():
    best = max(((abs((S.fmean(v[s:]) - S.fmean(v[:s])) / math.sqrt(S.pvariance(v[:s]) / s + S.pvariance(v[s:]) / (len(v) - s) + 1e-12)), s) for s in range(15, len(v) - 15)))
    s = best[1]; print('  %-9s max|t| %.2f at index %d (id %d): before %+.4f after %+.4f' % (k, best[0], s, ids[s], S.fmean(v[:s]), S.fmean(v[s:])))
