"""Hypothesis H2: the Target balances DOLLARS (cost) per side, not shares.  Checks: (a) end-of-market cost share of UP vs share-count share of UP (dispersion: which is closer to 0.5?);
(b) per fill: does it buy the side with LESS cost (dollar-short) - by dollar imbalance bin, by role;  (c) under equal-dollar holding the PnL = C_w/avgpx_w - (C_UP+C_DOWN): profit iff the winner's avg price < C_total/(C_w)...;
check how well 'winner avg buy price' explains PnL per class.   usage: python tools/target_dollar_balance.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, statistics as S
import real_replay_stop as rr
exp, root = sys.argv[1:3]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; cls = {}
for m, mk in M.items():
    b = rr.at(mk, 12.); F = 'UP' if (b['best_bid'] + b['best_ask']) / 2 >= .5 else 'DOWN'; ft = None
    for t, x in zip(mk['ts'], mk['bs']):
        if t >= 12 and ((x['best_bid'] + x['best_ask']) / 2 if F == 'UP' else 1 - (x['best_bid'] + x['best_ask']) / 2) <= .4: ft = t; break
    cls[m] = 'NO_FLIP' if ft is None else 'FALSE_FLIP' if by[m][0]['winner'] == F else 'TRUE_FLIP'
cs, ss = [], []; st = collections.defaultdict(lambda: [0., 0.]); pathdev = {'cost': [], 'shares': []}; rowsC = collections.defaultdict(list)
for m in M:
    sh = {'UP': 0., 'DOWN': 0.}; c = {'UP': 0., 'DOWN': 0.}
    for r in sorted(by[m], key=lambda r: r['event_ms']):
        C = c['UP'] + c['DOWN']
        if C > 0:
            dI = (c['UP'] - c['DOWN']) / C; short = 'DOWN' if dI > 0 else 'UP'; b_ = '<5%' if abs(dI) < .05 else '5-15%' if abs(dI) < .15 else '15-30%' if abs(dI) < .3 else '>=30%'
            st[(r['role'], b_)][0] += r['shares'] * r['price'] * (r['side'] == short); st[(r['role'], b_)][1] += r['shares'] * r['price']
            pathdev['cost'].append(abs(dI)); T = sh['UP'] + sh['DOWN']; pathdev['shares'].append(abs(sh['UP'] - sh['DOWN']) / T)
        sh[r['side']] += r['shares']; c[r['side']] += r['shares'] * r['price']
    cs.append(c['UP'] / (c['UP'] + c['DOWN'])); ss.append(sh['UP'] / (sh['UP'] + sh['DOWN']))
    w = by[m][0]['winner']; aw = c[w] / sh[w]; rowsC[cls[m]].append(dict(cw=c[w] / (c['UP'] + c['DOWN']), aw=aw, sh_w=sh[w] / (sh['UP'] + sh['DOWN']), pnl=by[m][0]['net_pnl_usdt'], C=c['UP'] + c['DOWN']))
print('(a) end-of-market UP fraction: by COST mean %.3f sd %.3f | by SHARES mean %.3f sd %.3f' % (S.fmean(cs), S.pstdev(cs), S.fmean(ss), S.pstdev(ss)))
q = lambda x, p: sorted(x)[int(p * (len(x) - 1))]
print('    path |imbalance|: cost-based q50 %.3f q90 %.3f | share-based q50 %.3f q90 %.3f' % (q(pathdev['cost'], .5), q(pathdev['cost'], .9), q(pathdev['shares'], .5), q(pathdev['shares'], .9)))
print('(b) share of fill DOLLARS that go to the dollar-SHORT side, by role and dollar imbalance before the fill')
for role in ('MAKER', 'TAKER'): print('  %-5s' % role, '  '.join('%s %.2f' % (b, st[(role, b)][0] / st[(role, b)][1]) for b in ('<5%', '5-15%', '15-30%', '>=30%') if st[(role, b)][1]))
print('(c) per class: winner cost share | winner avg buy price | winner share-count fraction | PnL/cost | PnL')
for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
    v = rowsC[k]; f = lambda key: S.fmean(x[key] for x in v)
    print('  %-10s %.3f | %.3f | %.3f | %+.3f | %+.1f' % (k, f('cw'), f('aw'), f('sh_w'), S.fmean(x['pnl'] / x['C'] for x in v), f('pnl')))
al = [x for v in rowsC.values() for x in v]
import math
xa = [x['aw'] for x in al]; ya = [x['pnl'] / x['C'] for x in al]; mx, my = S.fmean(xa), S.fmean(ya)
print('    corr(winner avg price, PnL/cost) over 105 markets = %.3f' % (sum((a - mx) * (b - my) for a, b in zip(xa, ya)) / math.sqrt(sum((a - mx) ** 2 for a in xa) * sum((b - my) ** 2 for b in ya))))
