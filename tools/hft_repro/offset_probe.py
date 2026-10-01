"""Engine probe: passive GTX buy orders (15 sh) at offsets 0..5 ticks below the side's best bid, one live order per (side, offset), repost every decision step when none is live, TTL 10 s,
decision step 1 s, no inventory logic (pure measurement).  For every fill: price, side-mid at fill, side-mid at fill+5 s (from the 1 Hz mid path), offset, resting time.
Output rows are compared with Target's real maker fills by offset (tools/target_maker_value.py)."""
import sys, json
from pathlib import Path
import numpy as np
R, FX, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717'); sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717/src')
import tools.hftbacktest_execution_shift_audit_v0 as ex
from hftbacktest import GTX, LIMIT
rows = []
for p in sorted(Path(FX).iterdir()):
    if not (p / 'events.npz').exists(): continue
    mid = int(p.name); ev = np.load(p / 'events.npz')['data']; bt = ex.new_bt(ev, entry_latency_ms=250, response_latency_ms=250, queue_model=__import__('os').environ.get('QM','risk')); ex.initialize_bt(bt)
    first = int(json.load(open(p / 'FIXTURE.json'))['conversion_info']['firstReceivedMs']); start = first - first % 300_000; t_end = int(ev['exch_ts'].max()) // 1_000_000
    orders = {}; n = 0; mids = {}; fills = []; t = first + 2000
    while t < min(t_end, start + 290_000):
        ex.advance_to(bt, t); d = bt.depth(0); bb, ba = round(float(d.best_bid), 2), round(float(d.best_ask), 2)
        if not (0 < bb < ba < 1): t += 1000; continue
        sec = (t - start) / 1000.; mids[int(sec)] = (bb + ba) / 2
        for o in orders.values():
            s = ex.order_snapshot(bt, o['n']); cum = float(s['cumExecQty'] or 0)
            if cum - o['cum'] > 1e-9: fx_ms = int(s['exchangeTs']) // 1_000_000 if s.get('exchangeTs') else t; fills.append(dict(market=mid, side=o['side'], off=o['off'], price=o['price'], qty=cum - o['cum'], sec=(fx_ms - start) / 1000., rest=(fx_ms - o['t']) / 1000.)); o['cum'] = cum
            o['live'] = s['status'] in ('NEW', 'PARTIALLY_FILLED')
            if o['live'] and t - o['t'] >= 10_000:
                cur = bt.orders(0).get(o['n'])
                if cur is not None and bool(cur.cancellable): bt.cancel(0, o['n'], False)
        for side in ('UP', 'DOWN'):
            best = bb if side == 'UP' else round(1 - ba, 2)
            for off in range(6):
                if any(o['live'] and o['side'] == side and o['off'] == off for o in orders.values()): continue
                px = round(best - .01 * off, 2)
                if px < .02: continue
                n += 1; ns, npx = ex.native_order(side, px); f = bt.submit_buy_order if ns == 'BUY' else bt.submit_sell_order
                f(0, n, npx, 15., GTX, LIMIT, False); orders[n] = dict(n=n, side=side, off=off, price=px, t=t, cum=0., live=True)
        t += 1000
    for f in fills:
        sm = lambda s_: (mids.get(int(s_)) if f['side'] == 'UP' else (1 - mids[int(s_)] if mids.get(int(s_)) is not None else None))
        m0, m5 = sm(f['sec']), sm(f['sec'] + 5)
        if m0 is not None and m5 is not None: rows.append(dict(f, inst=m0 - f['price'], v5=m5 - f['price']))
Path(OUT).write_text(json.dumps(rows)); print(len(rows), 'fills')
