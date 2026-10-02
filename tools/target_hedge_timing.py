"""Shares end balanced (sd .043) though the path is imbalanced: WHEN and HOW does the Target close the share gap?
Per market & time bin: |I|/T at bin end, volume, share of volume on the short side, role mix, avg price of short-side buys vs long-side buys; per class.
usage: python tools/target_hedge_timing.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, statistics as S, pathlib
import real_replay_stop as rr
exp, root = sys.argv[1:3]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; WS = {}; cls = {}
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms'])
for m, mk in M.items():
    b = rr.at(mk, 12.); F = 'UP' if (b['best_bid'] + b['best_ask']) / 2 >= .5 else 'DOWN'; ft = None
    for t, x in zip(mk['ts'], mk['bs']):
        if t >= 12 and ((x['best_bid'] + x['best_ask']) / 2 if F == 'UP' else 1 - (x['best_bid'] + x['best_ask']) / 2) <= .4: ft = t; break
    cls[m] = 'NO_FLIP' if ft is None else 'FALSE_FLIP' if by[m][0]['winner'] == F else 'TRUE_FLIP'
BINS = [(-1e9, 60), (60, 120), (120, 180), (180, 240), (240, 270), (270, 290), (290, 1e9)]
for k in ('ALL', 'NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
    agg = collections.defaultdict(lambda: dict(vol=0., short=0., taker=0., ps=0., pl=0., vs=0., vl=0., imb=[]))
    for m in M:
        if k != 'ALL' and cls[m] != k: continue
        sh = {'UP': 0., 'DOWN': 0.}; fs = sorted(by[m], key=lambda r: r['event_ms']); bi = 0
        for r in fs:
            t = (r['event_ms'] - WS[m]) / 1000.; j = next(i for i, (a, b) in enumerate(BINS) if a <= t < b)
            I = sh['UP'] - sh['DOWN']; short = 'DOWN' if I > 0 else 'UP' if I < 0 else None; g = agg[j]
            g['vol'] += r['shares']; g['taker'] += r['shares'] * (r['role'] == 'TAKER')
            if r['side'] == short: g['short'] += r['shares']; g['ps'] += r['shares'] * r['price']; g['vs'] += r['shares']
            elif short: g['pl'] += r['shares'] * r['price']; g['vl'] += r['shares']
            sh[r['side']] += r['shares']
        # imbalance at end of each bin
        sh = {'UP': 0., 'DOWN': 0.}; it = iter(fs); cur = next(it, None)
        for j, (a, b) in enumerate(BINS):
            while cur and (cur['event_ms'] - WS[m]) / 1000. < b: sh[cur['side']] += cur['shares']; cur = next(it, None)
            T = sh['UP'] + sh['DOWN']
            if T: agg[j]['imb'].append(abs(sh['UP'] - sh['DOWN']) / T)
    print('\n== %s ==  bin | vol/mkt share | short-side share | taker share | avg px short-side buys | avg px long-side buys | |I|/T at bin end (median)' % k)
    n = len([m for m in M if k == 'ALL' or cls[m] == k])
    for j, (a, b) in enumerate(BINS):
        g = agg[j]
        if not g['vol']: continue
        print('  %-9s %6.0f | %.2f | %.2f | %.3f | %.3f | %.3f' % ('%s-%s' % (max(a, 0) if a > -1e8 else 0, b if b < 1e8 else 'end'), g['vol'] / n, g['short'] / g['vol'], g['taker'] / g['vol'], g['ps'] / max(g['vs'], 1), g['pl'] / max(g['vl'], 1), S.median(g['imb']) if g['imb'] else float('nan')))
