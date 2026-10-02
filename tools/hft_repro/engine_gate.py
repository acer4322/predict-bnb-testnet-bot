"""Engine-level acceptance gate: replay the GOLDEN native actions (clock_trace.native_actions) through the Linux-built patched hftbacktest on the fixture events.npz
and compare final per-order fills with the golden execution_clock carriers/receipts.  Orders: NEW = passive GTX; NEW_ACTIVE = GTC (README: ACTIVE is GTC LIMIT)."""
import sys, json, gzip
import numpy as np
R = sys.argv[1]; MID = sys.argv[2]; ARM = sys.argv[3] if len(sys.argv) > 3 else 'CG1AT'
sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717'); sys.path.insert(0, R + '/strategy/runtime_scratch_CG1AT_2671717/src')
import tools.hftbacktest_execution_shift_audit_v0 as ex
from hftbacktest import GTC, GTX, LIMIT
fx = f'{R}/fixtures/{MID}'; z = np.load(fx + '/events.npz'); events = z['data']; golden = f'{fx}/golden/{ARM}'
tr = json.load(gzip.open(golden + '/clock_trace.json.gz')); ec = json.load(open(golden + '/execution_clock.json'))
bt = ex.new_bt(events, entry_latency_ms=250, response_latency_ms=250, queue_model='risk'); ex.initialize_bt(bt)
acts = [a for a in tr['native_actions'] if 'key' not in a or a['kind'] != 'CANCEL' or True]
seen_cancel = set(); orders = {}
for a in sorted(tr['native_actions'], key=lambda a: a['t']):
    ex.advance_to(bt, a['t'])
    if a['kind'] in ('NEW', 'NEW_ACTIVE'):
        if a['kind'] == 'NEW': ex.submit_native(bt, a['n'], a['side'], a['price'], a['qty'])
        else:
            ns, np_ = ex.native_order(a['side'], a['price'])
            (bt.submit_buy_order if ns == 'BUY' else bt.submit_sell_order)(0, int(a['n']), np_, float(a['qty']), GTC, LIMIT, False)
        orders[a['n']] = a
    elif a['kind'] == 'CANCEL' and 'key' not in a:
        cur = bt.orders(0).get(int(a['n']))
        if cur is not None and bool(cur.cancellable):
            bt.cancel(0, int(a['n']), False)
last = int(events['exch_ts'].max()) // 1_000_000 + 1000; ex.advance_to(bt, last)
mine = {n: ex.order_snapshot(bt, n) for n in orders}
car = {int(k.rsplit('_', 1)[1]): v for k, v in ec['carriers'].items()}
ok = 0; bad = []
for n, s in mine.items():
    g = car.get(n); gf = g['filled'] if g else None
    if g is not None and abs(float(s['cumExecQty']) - float(gf)) < 1e-6: ok += 1
    else: bad.append((n, orders[n]['side'], orders[n]['price'], orders[n]['qty'], s['cumExecQty'], gf))
print('market', MID, ARM, 'orders', len(mine), 'matching filled qty', ok, 'mismatch', len(bad))
sv = bt.state_values(0); print('my native: position %.4f balance %.4f volume %.4f value %.4f trades %s fee %.4f' % (sv.position, sv.balance, sv.trading_volume, sv.trading_value, sv.num_trades, sv.fee))
print('golden native:', ec['receipt_native'])
for b in bad[:12]: print('  mismatch', b)
