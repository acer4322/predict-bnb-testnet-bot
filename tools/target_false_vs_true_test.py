"""Does the Target act differently in FALSE_FLIP vs TRUE_FLIP markets?  Per market, share fraction on the decided favourite F (shares_F / total) and total shares, in windows relative to the flip time (pre-flip, 0-30 s, 30-120 s, >=120 s after flip), buys only.
Permutation test (20000 shuffles of class labels among flipped markets) for the difference FALSE-TRUE of per-market means.  Also: pre-flip final inventory F/O, and net PnL by class.
usage: python tools/target_false_vs_true_test.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, random, statistics as S
import real_replay_stop as rr
exp, root = sys.argv[1:3]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list); win = {}; pnl = {}
for r in rows: by[int(r['market_id'])].append(r); win[int(r['market_id'])] = r['winner']; pnl[int(r['market_id'])] = r['net_pnl_usdt']
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; info = {}; ws = {}
import pathlib
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in ws: ws[m] = int(rr.jl(p)['market']['window_start_ms'])
for m, mk in M.items():
    b = rr.at(mk, 12.); mid = (b['best_bid'] + b['best_ask']) / 2; F = 'UP' if mid >= .5 else 'DOWN'; ft = None
    for t, x in zip(mk['ts'], mk['bs']):
        if t < 12: continue
        xm = (x['best_bid'] + x['best_ask']) / 2
        if (xm if F == 'UP' else 1 - xm) <= .4: ft = t; break
    info[m] = (F, ft, 'NO_FLIP' if ft is None else 'FALSE_FLIP' if win[m] == F else 'TRUE_FLIP')
W = {'pre-flip': (-1e9, 0), '0-30s': (0, 30), '30-120s': (30, 120), '>=120s': (120, 1e9)}
def stat(m, lo, hi):
    F, ft, c = info[m]; f = o = 0.
    for r in by[m]:
        t = (r['event_ms'] - ws[m]) / 1000. - ft
        if lo <= t < hi:
            if r['side'] == F: f += r['shares']
            else: o += r['shares']
    return f, o
fl = [m for m in M if info[m][1] is not None]; rng = random.Random(1)
print('flipped markets: FALSE %d, TRUE %d' % (sum(info[m][2] == 'FALSE_FLIP' for m in fl), sum(info[m][2] == 'TRUE_FLIP' for m in fl)))
for nm, (lo, hi) in W.items():
    v = {}
    for m in fl:
        f, o = stat(m, lo, hi)
        if f + o > 0: v[m] = (f / (f + o), f + o)
    for what, ix in (('F-share fraction', 0), ('total shares', 1)):
        a = [v[m][ix] for m in v if info[m][2] == 'FALSE_FLIP']; b = [v[m][ix] for m in v if info[m][2] == 'TRUE_FLIP']
        if len(a) < 5 or len(b) < 5: continue
        d = S.fmean(a) - S.fmean(b); allv = a + b; cnt = 0
        for _ in range(20000):
            rng.shuffle(allv); cnt += abs(S.fmean(allv[:len(a)]) - S.fmean(allv[len(a):])) >= abs(d)
        print('%-9s %-17s FALSE %.3f (n=%d) TRUE %.3f (n=%d) diff %+.3f perm p=%.3f' % (nm, what, S.fmean(a), len(a), S.fmean(b), len(b), d, cnt / 20000))
print('\nPnL by class (export, fee-free): ', {k: round(S.fmean([pnl[m] for m in M if info[m][2] == k]), 1) for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')})
# net end inventory
for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
    fr = []
    for m in M:
        if info[m][2] != k: continue
        F = info[m][0]; f = sum(r['shares'] for r in by[m] if r['side'] == F); o = sum(r['shares'] for r in by[m] if r['side'] != F); fr.append(f / (f + o) if f + o else .5)
    print('%-10s end-of-market F share fraction mean %.3f (n=%d)' % (k, S.fmean(fr), len(fr)))
