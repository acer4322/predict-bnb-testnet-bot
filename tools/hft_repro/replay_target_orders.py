"""Direct simulator calibration on Target's own orders: for the 15 Target-lifecycle markets in the real event files, replay each inferred Target maker order (side, price, filled_qty as order qty,
submitted at placement_first_ms; GTX; never cancelled before the horizon) through the engine and report how much of it fills within resting_ms+250 ms (the engine already adds 250/250 ms latency),
within +1 s, +5 s and +30 s of placement.  Target's orders are KNOWN to have filled (survivor sample), so the engine's fill share is a recall rate: ~100% would mean the engine is as generous as reality."""
import sys, json, gzip, collections
from pathlib import Path
import numpy as np
R, FX, FEAT = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717'); sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717/src')
import tools.hftbacktest_execution_shift_audit_v0 as ex
from hftbacktest import GTX, LIMIT
feat = collections.defaultdict(list)
for r in json.load(gzip.open(FEAT)): feat[int(r['market_id'])].append(r)
res = []
for p in sorted(Path(FX).iterdir()):
    mid = int(p.name)
    if mid not in feat: continue
    rs = sorted(feat[mid], key=lambda r: r['placement_first_ms']); ev = np.load(p / 'events.npz')['data']
    bt = ex.new_bt(ev, entry_latency_ms=int(__import__('os').environ.get('LAT','250')), response_latency_ms=int(__import__('os').environ.get('LAT','250')), queue_model=__import__('os').environ.get('QM','risk')); ex.initialize_bt(bt)
    horizon = 30_000; live = []; n = 0; i = 0; t_end = int(ev['exch_ts'].max()) // 1_000_000; mids = {}; tcur = int(rs[0]['placement_first_ms']) // 1000 * 1000
    def rec_until(tt):
        global tcur
        while tcur <= tt:
            ex.advance_to(bt, tcur); d = bt.depth(0)
            if d.best_bid == d.best_bid and d.best_ask == d.best_ask: mids[tcur // 1000] = (float(d.best_bid) + float(d.best_ask)) / 2
            tcur += 1000
    for r in rs:
        t = int(r['placement_first_ms'])
        if t > t_end - horizon: break
        rec_until(t); ex.advance_to(bt, t)
        n += 1; ns, npx = ex.native_order(r['side'], r['price']); f = bt.submit_buy_order if ns == 'BUY' else bt.submit_sell_order
        f(0, n, npx, float(r['filled_qty']), GTX, LIMIT, False); live.append((n, r, t))
        # snapshot fills of earlier orders at checkpoints lazily below
    rec_until(int(rs[-1]['placement_first_ms']) + horizon + 1000)
    # engine exposes final fills only; to get time profile, rerun per-order checkpoints cheaply by re-simulating once per horizon is costly -> record final share and a coarse profile using exchange_ts
    for (nn, r, t) in live:
        s = ex.order_snapshot(bt, nn); q = float(r['filled_qty']); cum = float(s['cumExecQty'] or 0)
        fsec = (int(s['exchangeTs']) // 1_000_000) // 1000 if cum > 0 and s.get('exchangeTs') else None
        sm = (lambda x: mids.get(x) if r['side'] == 'UP' else (1 - mids[x] if x in mids else None))
        m0 = sm(fsec) if fsec else None; m5 = sm(fsec + 5) if fsec else None
        tf = (t + int(r['resting_ms'])) // 1000; mt0 = sm(tf); mt5 = sm(tf + 5)
        res.append(dict(market=mid, q=q, cum=cum, tv5=(mt5 - r['price']) if (mt5 is not None) else None, tinst=(mt0 - r['price']) if mt0 is not None else None, trec5=(r['mid_side_at_fill_plus5s'] - r['price']) if r['mid_side_at_fill_plus5s'] is not None else None, trecinst=(r['mid_side_at_fill'] - r['price']) if r['mid_side_at_fill'] is not None else None, inst=(m0 - r['price']) if m0 is not None else None, v5=(m5 - r['price']) if m5 is not None else None, share=cum / q, ftime=((int(s['exchangeTs']) // 1_000_000 - t) / 1000. if cum > 0 and s.get('exchangeTs') else None), rest=r['resting_ms'] / 1000., off=round((r['best_bid_at_placement'] - r['price']) / .01)))
print('orders replayed', len(res), 'markets', len({r['market'] for r in res}))
tq = sum(r['q'] for r in res); print('engine fills %.1f%% of Target quantity within 30 s of placement (cancel never)' % (100 * sum(r['q'] * r['share'] for r in res) / tq))
for lo, hi in ((0, 1), (1, 2), (2, 3), (3, 5), (5, 99)):
    rs_ = [r for r in res if lo <= r['off'] < hi] if hi < 99 else [r for r in res if r['off'] >= lo]
    if rs_: print('offset %d-%s ticks: n=%4d engine fill share %.1f%%' % (lo, hi - 1 if hi < 99 else '+', len(rs_), 100 * sum(r['q'] * r['share'] for r in rs_) / sum(r['q'] for r in rs_)))
fast = [r for r in res if r['share'] >= .999]; print('fully filled orders: %d of %d (%.1f%%)' % (len(fast), len(res), 100 * len(fast) / len(res)))
ft = sorted(r['ftime'] for r in res if r['ftime'] is not None); rt = sorted(r['rest'] for r in res)
import statistics as S
print('median time-to-last-fill in engine (when filled): %.1f s | Target observed resting median %.2f s (p90 %.2f)' % (S.median(ft), S.median(rt), rt[int(.9 * len(rt))]))

vv = [r for r in res if r['v5'] is not None and r['cum'] > 0]
w = sum(r['cum'] for r in vv); print('ENGINE value of the replayed Target orders (qty = engine fills): instant %+.2fc  +5s %+.2fc  (n=%d)' % (100 * sum(r['cum'] * r['inst'] for r in vv) / w, 100 * sum(r['cum'] * r['v5'] for r in vv) / w, len(vv)))
for lo, hi in ((0, 1), (1, 3), (3, 5), (5, 99)):
    z = [r for r in vv if lo <= r['off'] < hi]
    if z: ww = sum(r['cum'] for r in z); print('  offset %d-%d: n=%4d +5s %+.2fc' % (lo, hi - 1, len(z), 100 * sum(r['cum'] * r['v5'] for r in z) / ww))

zz = [r for r in res if r['tv5'] is not None and r['trec5'] is not None and r['tinst'] is not None]
w = sum(r['q'] for r in zz)
print('SAME ORDERS, Target fill time (placement+resting): book-path instant %+.2fc +5s %+.2fc | Target recorded instant %+.2fc +5s %+.2fc (n=%d)' % (100 * sum(r['q'] * r['tinst'] for r in zz) / w, 100 * sum(r['q'] * r['tv5'] for r in zz) / w, 100 * sum(r['q'] * r['trecinst'] for r in zz) / w, 100 * sum(r['q'] * r['trec5'] for r in zz) / w, len(zz)))
