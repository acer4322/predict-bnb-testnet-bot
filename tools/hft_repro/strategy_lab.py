"""Strategy lab on the reproduced V49 engine (risk queue, 250/250 ms, zero fee).  usage:
  python strategy_lab.py REPRO_ROOT FIXTURES_DIR LABELS.json [--only STRAT[,STRAT]] [--out results.json]
FIXTURES_DIR contains <market_id>/events.npz + FIXTURE.json (as in the repro package).  PnL comes from engine state: total cost = DN_shares - balance (native balance), payout from labelled winner.
Strategies (pre-declared; every parameter fixed here): FAV_TAKER / FAV_PASSIVE (buy the decided favourite F every 2 s from 12 s while F mid in [.55,.70], 15-share clips, cap 300, FREEZE at flip F mid<=.4,
cancel live orders at flip; passive = GTX at F best bid, TTL 10 s; taker = marketable GTC at F ask), MAKER_PAIR_K1D45 / MAKER_PAIR_K2INF (two-sided passive best-bid pair maker, see maker_pair.py).
Graduation criteria (same as the cloud study): G1 overall CI lower>0, G2 no-flip CI lower>0, D1 rev-mean >= -.5*noflip, worst loss < 3x avg win, 4/3/3 book > 0; plus true-flip mean and worst-5%."""
import sys, json, random, statistics as S, argparse
from pathlib import Path
import numpy as np
ap = argparse.ArgumentParser(); ap.add_argument('repro'); ap.add_argument('fixtures'); ap.add_argument('labels'); ap.add_argument('--only', default=''); ap.add_argument('--out', default='')
a = ap.parse_args(); R = a.repro
sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717'); sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717/src')
import tools.hftbacktest_execution_shift_audit_v0 as ex
from hftbacktest import GTC, GTX, LIMIT
lab = {int(r['market_id']): r['winner'] for r in json.load(open(a.labels))['records']}

def submit(bt, n, side, price, qty, gtx):
    ns, npx = ex.native_order(side, price); f = bt.submit_buy_order if ns == 'BUY' else bt.submit_sell_order
    return f(0, int(n), npx, float(qty), GTX if gtx else GTC, LIMIT, False)

def run(fx, mid, strat, dec_ms=1000, tick=15., cap=300., stop=270.):
    ev = np.load(f'{fx}/{mid}/events.npz')['data']; bt = ex.new_bt(ev, entry_latency_ms=250, response_latency_ms=250, queue_model='risk'); ex.initialize_bt(bt)
    first = int(json.load(open(f'{fx}/{mid}/FIXTURE.json'))['conversion_info']['firstReceivedMs']); start = first - first % 300_000; t_end = int(ev['exch_ts'].max()) // 1_000_000
    orders = {}; n = 0; sh = {'UP': 0., 'DOWN': 0.}; mids = {}; frozen = False; fav = None; t = first + 2000
    while t < min(t_end, start + 300_000):
        ex.advance_to(bt, t)
        for o in orders.values():
            s = ex.order_snapshot(bt, o['n']); cum = float(s['cumExecQty'] or 0)
            if cum - o['cum'] > 1e-9: sh[o['side']] += cum - o['cum']; o['cum'] = cum
            o['live'] = s['status'] in ('NEW', 'PARTIALLY_FILLED')
        d = bt.depth(0); bb, ba = round(float(d.best_bid), 2), round(float(d.best_ask), 2)
        if not (0 < bb < ba < 1): t += dec_ms; continue
        sec = (t - start) / 1000.; m = (bb + ba) / 2; mids[int(sec)] = m
        def cancel_all(pred=lambda o: True):
            for o in orders.values():
                if o['live'] and pred(o):
                    cur = bt.orders(0).get(o['n'])
                    if cur is not None and bool(cur.cancellable): bt.cancel(0, o['n'], False)
        if strat.startswith('FAV'):
            if sec >= 12 and fav is None: fav = 'UP' if m >= .5 else 'DOWN'
            if fav is not None:
                fm = m if fav == 'UP' else 1 - m
                if not frozen and sec > 12 and fm <= .4: frozen = True; cancel_all()
                if frozen or sec >= stop: pass
                elif int(sec) % 2 == 0 and .55 <= fm <= .70 and sh['UP'] + sh['DOWN'] + sum(o['qty'] - o['cum'] for o in orders.values() if o['live']) < cap:
                    cur = 'UP' if m >= .5 else 'DOWN'; cm = m if cur == 'UP' else 1 - m
                    if .55 <= cm <= .70:
                        px = (bb if cur == 'UP' else round(1 - ba, 2)) if strat == 'FAV_PASSIVE' else (ba if cur == 'UP' else round(1 - bb, 2))
                        n += 1; submit(bt, n, cur, px, tick, strat == 'FAV_PASSIVE'); orders[n] = dict(n=n, side=cur, qty=tick, cum=0., live=True, t=t)
                if strat == 'FAV_PASSIVE': cancel_all(lambda o: t - o['t'] >= 10_000)
        else:
            K, D = (1, 45.) if strat == 'MAKER_PAIR_K1D45' else (2, 1e9)
            want = {'UP': bb, 'DOWN': round(1. - ba, 2)}
            for side in ('UP', 'DOWN'):
                for o in orders.values():
                    if o['live'] and o['side'] == side and ((t - o['t'] >= 2000 and o['px'] != want[side]) or t - o['t'] >= 30_000):
                        cur = bt.orders(0).get(o['n'])
                        if cur is not None and bool(cur.cancellable): bt.cancel(0, o['n'], False)
                live = [o for o in orders.values() if o['live'] and o['side'] == side and t - o['t'] < 30_000 and o['px'] == want[side]]
                opp = 'DOWN' if side == 'UP' else 'UP'
                if sec < stop and sh[side] - sh[opp] <= D and len(live) < K and want[side] >= .02:
                    n += 1; submit(bt, n, side, want[side], tick, True); orders[n] = dict(n=n, side=side, qty=tick, cum=0., live=True, t=t, px=want[side])
        t += dec_ms
    ex.advance_to(bt, t_end + 1000)
    for o in orders.values():
        s = ex.order_snapshot(bt, o['n']); cum = float(s['cumExecQty'] or 0)
        if cum - o['cum'] > 1e-9: sh[o['side']] += cum - o['cum']
    sv = bt.state_values(0); cost = sh['DOWN'] - sv.balance; w = lab[mid]
    pnl = sh[w] - cost
    # classification from the 1 Hz mid path
    f0 = 'UP' if mids.get(12, mids[min(mids)]) >= .5 else 'DOWN'; flip = any((mm if f0 == 'UP' else 1 - mm) <= .4 for s_, mm in mids.items() if 12 < s_ < 290)
    cls = 'NO_FLIP' if not flip else ('FALSE_FLIP' if f0 == w else 'TRUE_FLIP')
    return dict(market=mid, pnl=pnl, cost=cost, up=sh['UP'], dn=sh['DOWN'], cls=cls, win=w)

def ci(xs, rng, nb=1000):
    ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
if __name__ == '__main__':
    fx = a.fixtures; mids = sorted(int(p.name) for p in Path(fx).iterdir() if p.is_dir() and (p / 'events.npz').exists() and int(p.name) in lab)
    strats = a.only.split(',') if a.only else ['FAV_TAKER', 'FAV_PASSIVE', 'MAKER_PAIR_K1D45', 'MAKER_PAIR_K2INF']; rng = random.Random(1); out = {}
    print('markets', len(mids), '(CIs are only meaningful for n>=30)')
    for st in strats:
        rows = [run(fx, m, st) for m in mids]; out[st] = rows; pn = [r['pnl'] for r in rows]; g = {k: [r['pnl'] for r in rows if r['cls'] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
        mn = {k: (S.fmean(v) if v else float('nan')) for k, v in g.items()}; pos = [x for x in pn if x > 0]; aw = S.fmean(pos) if pos else float('nan'); lo, hi = ci(pn, rng) if len(pn) > 3 else (float('nan'),) * 2
        rev = g['FALSE_FLIP'] + g['TRUE_FLIP']; nlo = ci(g['NO_FLIP'], rng)[0] if len(g['NO_FLIP']) > 3 else float('nan')
        flags = ' '.join('%s%s' % (k, 'Y' if v else '.') for k, v in (('G1', lo > 0), ('G2', nlo > 0), ('D1', bool(rev) and mn['NO_FLIP'] == mn['NO_FLIP'] and S.fmean(rev) >= -.5 * mn['NO_FLIP']), ('W', bool(pos) and -min(pn) < 3 * aw)))
        print('%-18s n=%3d noflip %7.1f false %7.1f true %7.1f | overall %6.1f [%6.1f,%6.1f] | worst %7.1f | cost/mkt %6.0f | %s' % (st, len(rows), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], S.fmean(pn), lo, hi, min(pn), S.fmean(r['cost'] for r in rows), flags))
    if a.out: Path(a.out).write_text(json.dumps(out))
