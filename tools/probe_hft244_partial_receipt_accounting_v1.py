"""Six synthetic receipt fixtures; no market data, policy or backend mutation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

EXPECTED = '74af885fe5bab4873e4672422befb0c3c5acf513ec8da0e3f80562a0c56505c6'


def run_fixture(h, np, side, route):
    from hftbacktest.order import NEW, PARTIALLY_FILLED, FILLED, CANCELED
    price = .74 if side == 'BUY' else .26
    evs = []

    def event(kind, ms, px, qty):
        row = np.zeros(1, dtype=h.event_dtype)[0]
        row['ev'] = kind | h.EXCH_EVENT | h.LOCAL_EVENT
        row['exch_ts'] = row['local_ts'] = ms * 1_000_000
        row['px'], row['qty'] = px, qty
        evs.append(row)

    event(h.DEPTH_SNAPSHOT_EVENT | h.BUY_EVENT, 1000, price-.01, 100)
    event(h.DEPTH_SNAPSHOT_EVENT | h.SELL_EVENT, 1000, price+.01, 100)
    trade_side = h.SELL_EVENT if side == 'BUY' else h.BUY_EVENT
    event(h.TRADE_EVENT | trade_side, 2000, price, 1.35 if route == 'FULL' else 1.)
    if route == 'PARTIAL_FULL':
        event(h.TRADE_EVENT | trade_side, 3000, price, .35)
    event(h.DEPTH_EVENT | h.BUY_EVENT, 5000, price-.01, 100)
    arr = np.asarray(evs, dtype=h.event_dtype)
    asset = (h.BacktestAsset().data(arr).linear_asset(1.)
             .constant_order_latency(250_000_000, 250_000_000)
             .partial_fill_exchange().risk_adverse_queue_model().tick_size(.01).lot_size(.01))
    bt = h.HashMapMarketDepthBacktest([asset])
    snapshots = []

    def advance(ms):
        assert bt.elapse(ms*1_000_000-int(bt.current_timestamp)) == 0

    def snapshot(label):
        o, s = bt.orders(0).get(1), bt.state_values(0)
        r = dict(label=label, nowNs=int(bt.current_timestamp),
                 order={k:float(getattr(o,k)) for k in
                        ['qty','leaves_qty','exec_qty','exec_price','exch_timestamp','local_timestamp']},
                 status=int(o.status), req=int(o.req),
                 native={k:float(getattr(s,k)) for k in
                         ['position','balance','trading_volume','trading_value','num_trades','fee']})
        r['cum'] = r['order']['qty']-r['order']['leaves_qty']
        sign = 1 if side == 'BUY' else -1
        expected = dict(position=sign*r['cum'], balance=-sign*price*r['cum'],
                        trading_volume=r['cum'], trading_value=price*r['cum'])
        r['accountErrors'] = {k:r['native'][k]-v for k,v in expected.items()}
        r['accountPass'] = all(abs(v)<1e-8 for v in r['accountErrors'].values())
        snapshots.append(r)
        return r

    try:
        assert bt.wait_next_feed(False, 1) in (0, 2)
        advance(1100)
        submit = bt.submit_buy_order if side == 'BUY' else bt.submit_sell_order
        assert submit(0, 1, price, 1.35, h.GTX, h.LIMIT, False) == 0
        advance(1700)
        ack = snapshot('ACK')
        assert ack['status'] == NEW and abs(ack['cum']) < 1e-8
        advance(2300)
        first = snapshot('FIRST_FILL_RESPONSE')
        expected_cum = 1.35 if route == 'FULL' else 1.
        assert first['status'] == (FILLED if route == 'FULL' else PARTIALLY_FILLED)
        assert abs(first['cum']-expected_cum)<1e-8
        if route == 'PARTIAL_CANCEL':
            advance(2400)
            assert bt.cancel(0, 1, False) == 0
        advance(3400)
        final = snapshot('TERMINAL_RESPONSE')
        assert final['status'] == (CANCELED if route == 'PARTIAL_CANCEL' else FILLED)
        assert abs(final['cum']-(1. if route == 'PARTIAL_CANCEL' else 1.35))<1e-8
        advance(4000)
        late = snapshot('LATE_READ')
        assert final['native'] == late['native'] and final['order'] == late['order']
        return dict(side=side, route=route, exercised=True, snapshots=snapshots)
    finally:
        bt.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--backend', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    assert not a.output.exists(), 'immutable result already exists'
    native = a.backend/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    with native.open('rb') as f:
        sha = hashlib.file_digest(f,'sha256').hexdigest()
    assert sha == EXPECTED, sha
    sys.path.insert(0,str(a.backend.resolve()))
    import numpy as np
    import hftbacktest as h
    assert Path(h.__file__).resolve().is_relative_to(a.backend.resolve())
    start = time.time(); rows = []
    result = dict(marketBE=0, maxSyntheticEngines=6, nativeSha256=sha,
                  modelsTrained=0, freshUsed=0, backendModified=False)
    try:
        for side in ['BUY','SELL']:
            for route in ['FULL','PARTIAL_CANCEL','PARTIAL_FULL']:
                rows.append(run_fixture(h,np,side,route))
        controls = all(all(s['accountPass'] for s in r['snapshots']) for r in rows if r['route']=='FULL')
        partial = [r for r in rows if r['route']!='FULL']
        assert controls, 'full-fill control accounting failed'
        if all(not r['snapshots'][1]['accountPass'] and not r['snapshots'][-1]['accountPass'] for r in partial):
            verdict = 'PARTIAL_LOCAL_ACCOUNTING_OMISSION_REPRODUCED'
        elif all(all(s['accountPass'] for s in r['snapshots']) for r in rows):
            verdict = 'PARTIAL_LOCAL_ACCOUNTING_OMISSION_FALSIFIED'
        else:
            verdict = 'MIXED_ACCOUNTING_DIAGNOSTIC'
        result.update(verdict=verdict, controlsPass=controls)
    except Exception as e:
        result.update(verdict='CORRECTNESS_OR_NOT_EXERCISED_STOP',error=type(e).__name__+': '+str(e))
    result.update(rows=rows,completedSyntheticEngines=len(rows),elapsedSeconds=time.time()-start)
    blob = json.dumps(result,indent=2).encode()
    assert len(blob)<=64*1024
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
    if result['verdict']=='CORRECTNESS_OR_NOT_EXERCISED_STOP':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
