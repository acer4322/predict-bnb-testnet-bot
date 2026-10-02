"""Target turned from -96/market (first half by id) to +127/market (second half).  Did its BEHAVIOUR change, or only the market?  Side-by-side per half:
class mix & per-class PnL and pair cost; volume, maker share; fill price vs side mid (cheapness) by role; share of $ on side with mid<.5 (underdog) by time bin; |I|/T path; buys after side dips; fill size distribution; market oscillation (mid path length).
usage: python tools/target_half_compare.py target_fills.json.gz PUBLIC_ROOT"""
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
def smid(m, t, side):
    b = rr.at(M[m], t)
    if not b: return None
    x = (b['best_bid'] + b['best_ask']) / 2; return x if side == 'UP' else 1 - x
ids = sorted(M); H = {'first': ids[:len(ids) // 2], 'second': ids[len(ids) // 2:]}
for nm, sub in H.items():
    print('\n######## %s half (%d markets, ids %d-%d) ########' % (nm, len(sub), sub[0], sub[-1]))
    for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
        s = [m for m in sub if cls[m] == k]; pcs = []
        for m in s:
            sh = collections.Counter(); c = collections.Counter()
            for r in by[m]: sh[r['side']] += r['shares']; c[r['side']] += r['shares'] * r['price']
            if sh['UP'] and sh['DOWN']: pcs.append(c['UP'] / sh['UP'] + c['DOWN'] / sh['DOWN'])
        print('  %-10s n=%2d PnL %+7.1f pair cost %.3f' % (k, len(s), S.fmean(by[m][0]['net_pnl_usdt'] for m in s), S.fmean(pcs)))
    vol = [sum(r['shares'] for r in by[m]) for m in sub]; print('  shares/mkt %.0f  fills/mkt %.0f  median fill size %.1f  maker share %.0f%%' % (S.fmean(vol), S.fmean(len(by[m]) for m in sub), S.median(r['shares'] for m in sub for r in by[m]), 100 * sum(r['shares'] for m in sub for r in by[m] if r['role'] == 'MAKER') / sum(vol)))
    ch = collections.defaultdict(lambda: [0., 0.]); ud = collections.defaultdict(lambda: [0., 0.]); dip = collections.defaultdict(lambda: [0., 0.]); path = []
    for m in sub:
        mids = [smid(m, float(t), 'UP') for t in range(12, 291, 2)]; mids = [x for x in mids if x is not None]; path.append(sum(abs(a - b) for a, b in zip(mids, mids[1:])))
        for r in by[m]:
            t = (r['event_ms'] - WS[m]) / 1000.; x = smid(m, t, r['side']); x10 = smid(m, t - 10, r['side'])
            if x is None: continue
            ch[r['role']][0] += r['shares'] * (x - r['price']); ch[r['role']][1] += r['shares']
            tb = min(int(t // 60), 4); ud[tb][0] += r['shares'] * r['price'] * (x < .5); ud[tb][1] += r['shares'] * r['price']
            if x10 is not None: dip[r['role']][0] += r['shares'] * (x - x10); dip[r['role']][1] += r['shares']
    print('  side mid - fill price (cheapness, +=bought below mid): ' + '  '.join('%s %+.4f' % (k, v[0] / v[1]) for k, v in sorted(ch.items())))
    print('  side mid change over 10 s before fill (dip buying): ' + '  '.join('%s %+.4f' % (k, v[0] / v[1]) for k, v in sorted(dip.items())))
    print('  share of $ on the side with mid<.5 by minute: ' + '  '.join('%d:%.2f' % (k, v[0] / v[1]) for k, v in sorted(ud.items())))
    print('  market mid path length (oscillation) mean %.2f' % S.fmean(path))
