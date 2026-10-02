"""Decompose Target PnL per market (export, buys only, fee-free): paired = min(sh_UP, sh_DOWN) shares each side -> locked = paired*(1 - avgpx_UP - avgpx_DOWN);
residual = excess shares on one side, pays 1 if that side wins: excess*(win)-excess*avgpx_side.  By class (flip class from public books as in target_flip_response).
usage: python tools/target_pnl_decomp.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, statistics as S
import real_replay_stop as rr
exp, root = sys.argv[1:3]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list); win = {}; pnl = {}
for r in rows: by[int(r['market_id'])].append(r); win[int(r['market_id'])] = r['winner']; pnl[int(r['market_id'])] = r['net_pnl_usdt']
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; out = collections.defaultdict(list)
for m, mk in M.items():
    b = rr.at(mk, 12.); F = 'UP' if (b['best_bid'] + b['best_ask']) / 2 >= .5 else 'DOWN'; ft = None
    for t, x in zip(mk['ts'], mk['bs']):
        if t >= 12 and ((x['best_bid'] + x['best_ask']) / 2 if F == 'UP' else 1 - (x['best_bid'] + x['best_ask']) / 2) <= .4: ft = t; break
    cls = 'NO_FLIP' if ft is None else 'FALSE_FLIP' if win[m] == F else 'TRUE_FLIP'
    sh = {'UP': 0., 'DOWN': 0.}; cost = {'UP': 0., 'DOWN': 0.}
    for r in by[m]: sh[r['side']] += r['shares']; cost[r['side']] += r['shares'] * r['price']
    ap = {s: cost[s] / sh[s] if sh[s] else 0. for s in sh}; pair = min(sh.values()); locked = pair * (1 - ap['UP'] - ap['DOWN']) if pair > 0 else 0.
    big = 'UP' if sh['UP'] >= sh['DOWN'] else 'DOWN'; ex = sh[big] - pair; resid = ex * ((1. if win[m] == big else 0.) - ap[big])
    out[cls].append(dict(total=sum(cost.values()), pair=pair, locked=locked, resid=resid, exF=(big == F), pc=ap['UP'] + ap['DOWN'], exshare=ex / max(1., sum(sh.values())), pnl=pnl[m], calc=locked + resid, excess_wins=(win[m] == big)))
print('class n | mean cost | paired sh | pair cost(avgUP+avgDOWN) | LOCKED | RESIDUAL(directional) | sum | export PnL | excess-side share | excess side wins % | excess on F %')
for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
    v = out[k]; f = lambda key: S.fmean(x[key] for x in v)
    print('%-10s %2d | %6.0f | %6.0f | %.3f | %6.1f | %6.1f | %6.1f | %6.1f | %.2f | %.0f%% | %.0f%%' % (k, len(v), f('total'), f('pair'), f('pc'), f('locked'), f('resid'), f('calc'), f('pnl'), f('exshare'), 100 * S.fmean(x['excess_wins'] for x in v), 100 * S.fmean(x['exF'] for x in v)))
al = [x for v in out.values() for x in v]; print('ALL %d: locked %.1f residual %.1f export pnl %.1f ; pair cost<1 in %.0f%% of markets' % (len(al), S.fmean(x['locked'] for x in al), S.fmean(x['resid'] for x in al), S.fmean(x['pnl'] for x in al), 100 * S.fmean(x['pc'] < 1 for x in al)))
