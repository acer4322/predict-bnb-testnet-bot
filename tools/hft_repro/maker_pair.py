"""Balanced passive pair maker driven through the Linux-built V49 engine (risk queue, 250/250 ms, zero fee) on the 3 fixture markets.
Rule set (pre-declared grid): post up to K passive GTX bids of 15 sh per side at the side's best bid; cancel/repost when the best bid moved (age>=2 s) or age>=TTL(30 s);
do not post on the side whose inventory leads by more than D shares; stop posting at 270 s; hold to settlement (no taker repair).  Decisions every 1 s (engine time)."""
import sys, json, itertools
import numpy as np
R = sys.argv[1]
sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717'); sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717/src')
import tools.hftbacktest_execution_shift_audit_v0 as ex
WIN = {2671717: None, 2671719: None, 2671768: None}
lab = {int(r['market_id']): r['winner'] for r in json.load(open(sys.argv[2]))['records']}

def run(mid, K, D, ttl=30., tick=15., stop=270., dec_ms=1000):
    ev = np.load(f'{R}/fixtures/{mid}/events.npz')['data']; bt = ex.new_bt(ev, entry_latency_ms=250, response_latency_ms=250, queue_model='risk'); ex.initialize_bt(bt)
    fr = json.load(open(f'{R}/fixtures/{mid}/FIXTURE.json'))['conversion_info']['firstReceivedMs']; t0 = int(fr); t_end = int(ev['exch_ts'].max()) // 1_000_000; start = t0 - t0 % 300_000; n = 0
    orders = {}; inv = {'UP': 0., 'DOWN': 0.}; cost = {'UP': 0., 'DOWN': 0.}; fills = []; t = t0 + 2000
    while t < min(t_end, start + 300_000):
        ex.advance_to(bt, t)
        for o in orders.values():  # collect fills
            s = ex.order_snapshot(bt, o['n']); cum = float(s['cumExecQty'] or 0); inc = cum - o['cum']
            if inc > 1e-9:
                inv[o['side']] += inc; cost[o['side']] += inc * o['price']; fills.append((t, o['side'], inc, o['price'])); o['cum'] = cum
            o['status'] = s['status']; o['live'] = s['status'] in ('NEW', 'PARTIALLY_FILLED')
        d = bt.depth(0); bb, ba = round(float(d.best_bid), 2), round(float(d.best_ask), 2)
        want = {'UP': bb, 'DOWN': round(1. - ba, 2)}
        if not (0 < bb < ba < 1): t += dec_ms; continue
        for side in ('UP', 'DOWN'):
            live = [o for o in orders.values() if o['side'] == side and o['live']]
            for o in live:
                if (t - o['t'] >= 2000 and o['price'] != want[side]) or t - o['t'] >= ttl * 1000:
                    cur = bt.orders(0).get(o['n'])
                    if cur is not None and bool(cur.cancellable): bt.cancel(0, o['n'], False)
            live = [o for o in orders.values() if o['side'] == side and o['live'] and (t - o['t'] < ttl * 1000) and o['price'] == want[side]]
            opp = 'DOWN' if side == 'UP' else 'UP'
            lead = inv[side] - inv[opp]
            if (t - start) / 1000. < stop and lead <= D and len(live) < K and want[side] >= .02:
                n += 1; ex.submit_native(bt, n, side, want[side], tick); orders[n] = dict(n=n, side=side, price=want[side], t=t, cum=0., live=True)
        t += dec_ms
    ex.advance_to(bt, t_end + 1000)
    for o in orders.values():
        s = ex.order_snapshot(bt, o['n']); cum = float(s['cumExecQty'] or 0); inc = cum - o['cum']
        if inc > 1e-9: inv[o['side']] += inc; cost[o['side']] += inc * o['price']; fills.append((t_end, o['side'], inc, o['price']))
    w = lab[mid]; tot_cost = cost['UP'] + cost['DOWN']; pnl = inv[w] - tot_cost
    m = min(inv.values()); avgu = cost['UP'] / inv['UP'] if inv['UP'] else 0; avgd = cost['DOWN'] / inv['DOWN'] if inv['DOWN'] else 0
    return dict(mid=mid, win=w, up=inv['UP'], dn=inv['DOWN'], cost=tot_cost, pnl=pnl, pair_cost=(avgu + avgd) if m > 0 else None, locked=m * (1 - avgu - avgd) if m > 0 else 0., fills=len(fills))

if __name__ == '__main__':
    print('%-3s %-6s | %-7s | %-5s | %6s %6s %7s %7s | pair cost | locked | fills' % ('K', 'D', 'market', 'win', 'UP sh', 'DN sh', 'cost', 'PnL'))
    for K, D in itertools.product((1, 2), (45., 90., 1e9)):
        tot = 0.
        for mid in WIN:
            r = run(mid, K, D); tot += r['pnl']
            print('%-3d %-6s | %-7d | %-5s | %6.0f %6.0f %7.1f %7.1f | %9s | %6.1f | %d' % (K, 'inf' if D > 1e8 else int(D), mid, r['win'], r['up'], r['dn'], r['cost'], r['pnl'], '%.4f' % r['pair_cost'] if r['pair_cost'] else '-', r['locked'], r['fills']))
        print('   sum PnL over 3 markets: %.1f' % tot)
