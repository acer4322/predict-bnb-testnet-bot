import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path('C:/BTC5M-worker/.tmp/hft244_receipt_v2_env1_20260910')
PACKAGE=ROOT/'.tmp/hft244_accounting_build_v1/candidate_python'
NATIVE=PACKAGE/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
SHA='06e36073579d94218f7c39673a14b6a13796081331c22284de869cef3b2ba1a9'


def fixture(h,np,side,row):
    from hftbacktest.order import NEW
    buy=side=='BUY'; price=.74 if buy else .26; events=[]
    def event(kind,t,p,q):
        r=np.zeros(1,h.event_dtype)[0]; r['ev']=kind|h.EXCH_EVENT|h.LOCAL_EVENT
        r['exch_ts']=r['local_ts']=t*1_000_000; r['px']=p; r['qty']=q; events.append(r)
    event(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,price-.01,100)
    event(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,price+.01,100)
    event(h.TRADE_EVENT|(h.SELL_EVENT if buy else h.BUY_EVENT),2000,price,.6)
    event(h.DEPTH_EVENT|h.BUY_EVENT,5000,price-.02,100)
    arr=np.asarray(events,dtype=h.event_dtype); before=hashlib.sha256(arr.tobytes()).hexdigest()
    bt=h.HashMapMarketDepthBacktest([h.BacktestAsset().data(arr).linear_asset(1.)
        .constant_order_latency(250_000_000,250_000_000).partial_fill_exchange()
        .risk_adverse_queue_model().tick_size(.01).lot_size(.01)])
    row['snapshots']=[]
    def advance(t): assert bt.elapse(t*1_000_000-int(bt.current_timestamp))==0
    def snap(label):
        s=bt.state_values(0); orders=[]
        for oid in [1,2]:
            order=bt.orders(0).get(oid)
            orders.append(dict(id=oid,status=int(order.status),req=int(order.req),qty=float(order.qty),leaves=float(order.leaves_qty),cum=float(order.qty-order.leaves_qty)))
        result=dict(label=label,timeNs=int(bt.current_timestamp),orders=orders,
            native={k:float(getattr(s,k)) for k in ['position','balance','trading_volume','trading_value','num_trades','fee']})
        row['snapshots'].append(result); return result
    try:
        assert bt.wait_next_feed(False,1) in (0,2); advance(1100)
        depth=bt.depth(0); ahead=depth.bid_qty_at_tick(74) if buy else depth.ask_qty_at_tick(26)
        row['book']=dict(bestBid=float(depth.best_bid),bestAsk=float(depth.best_ask),externalQueueAhead=float(ahead))
        assert abs(depth.best_bid-(price-.01))<1e-8 and abs(depth.best_ask-(price+.01))<1e-8 and ahead==0
        submit=bt.submit_buy_order if buy else bt.submit_sell_order
        assert submit(0,1,price,1.,h.GTX,h.LIMIT,False)==0
        advance(1150); assert submit(0,2,price,1.,h.GTX,h.LIMIT,False)==0
        advance(1700); ack=snap('ACKS')
        assert all(order['status']==NEW and order['cum']==0 for order in ack['orders'])
        assert ack['native']['trading_volume']==0
        advance(2300); filled=snap('AFTER_SINGLE_PRINT')
        advance(3000); late=snap('LATE'); assert late['native']==filled['native']
        total=sum(order['cum'] for order in filled['orders'])
        assert abs(total-filled['native']['trading_volume'])<1e-8, 'receipt/ledger disagreement'
        row.update(exercised=True,printQty=.6,aggregateCum=total,overallocated=total>.6+1e-8)
    finally:
        bt.close(); row['arraySha256']=before; row['arrayUnchanged']=hashlib.sha256(arr.tobytes()).hexdigest()==before
        assert row['arrayUnchanged']


def main():
    assert hashlib.sha256(NATIVE.read_bytes()).hexdigest()==SHA
    sys.path.insert(0,str(PACKAGE)); import hftbacktest as h; import numpy as np
    assert Path(h.__file__).resolve().parent==(PACKAGE/'hftbacktest').resolve()
    result=dict(marketBE=0,promotion=False,nativeSha256=SHA,rows=[]); start=time.monotonic()
    try:
        for side in ['BUY','SELL']:
            row=dict(side=side,exercised=False); result['rows'].append(row)
            fixture(h,np,side,row)
        count=sum(r['overallocated'] for r in result['rows'])
        result['verdict']='SHARED_PRINT_OVERALLOCATION_REPRODUCED' if count==2 else ('MIXED' if count else 'NOT_REPRODUCED')
    except Exception as exc: result.update(verdict='ERROR_OR_NOT_EXERCISED',error=type(exc).__name__+': '+str(exc))
    result.update(attemptedEngines=len(result['rows']),elapsedSeconds=time.monotonic()-start)
    blob=json.dumps(result,indent=2).encode(); assert len(blob)<32*1024
    path=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'SHARED_PRINT_TRACE2.json'; assert not path.exists(); path.write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
    if result['verdict']=='ERROR_OR_NOT_EXERCISED': raise SystemExit(2)


if __name__=='__main__': main()
