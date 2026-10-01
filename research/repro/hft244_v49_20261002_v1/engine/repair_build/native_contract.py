"""Small synthetic EOF contracts, executed only by the named worker job."""
import argparse
import ctypes as c
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

P=Path(__file__).resolve().parent
NS=1_000_000


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--backend',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    assert socket.gethostname().upper()=='DESKTOP-JIERAGF' and os.environ.get('BTC5M_LAN_RESULT_DIR')
    backend=Path(a.backend);binary=backend/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    sys.path.insert(0,str(backend));sys.path.insert(0,str(P/'contract_tools'))
    import hftbacktest as h
    import numpy as np
    from hft244_receipt_adapter_v1 import Reader,Receipt,Ledger
    assert Path(h.__file__).resolve().parent==binary.parent.resolve()
    results=[]
    def data(rows):
        arr=np.zeros(len(rows),h.event_dtype)
        for r,(kind,t,p,q) in zip(arr,rows):
            r['ev']=kind|h.EXCH_EVENT|h.LOCAL_EVENT;r['exch_ts']=r['local_ts']=t*NS;r['px']=p;r['qty']=q
        return arr
    def asset(rows):
        return h.BacktestAsset().data(data(rows)).linear_asset(1.).constant_order_latency(250*NS,250*NS).partial_fill_exchange().risk_adverse_queue_model().tick_size(.01).lot_size(.01)
    book=[(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,.39,100.),(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,.60,100.)]
    def raw(reader,bt):
        n=c.c_size_t();ptr=reader.get(bt.ptr,reader.asset,c.byref(n));assert n.value<=reader.capacity
        buf=c.string_at(ptr,n.value*c.sizeof(Receipt)) if n.value else b''
        rows=[{k:getattr(x,k) for k,_ in Receipt._fields_} for x in (Receipt*n.value).from_buffer_copy(buf)]
        return rows
    def snapshot(label,bt,rc,reader=None):
        o=bt.orders(0).get(1);st=bt.state_values(0)
        row=dict(label=label,rc=int(rc),clock_ns=int(bt.current_timestamp),
                 state={k:float(getattr(st,k)) for k in ('position','balance','trading_volume','trading_value','num_trades','fee')},
                 order=None if o is None else dict(status=int(o.status),qty=float(o.qty),leaves=float(o.leaves_qty),cancellable=bool(o.cancellable)))
        if reader is not None:
            rows=raw(reader,bt);row['receipts']=rows
            row['receipts_within_clock']=all(x['receive_ts']<=int(bt.current_timestamp) for x in rows)
            if row['receipts_within_clock']:
                assert reader.peek()==rows==reader.peek()
                ledger=Ledger();ledger.consume(rows);previous=dict(ledger.native);ledger.consume(rows);assert ledger.native==previous
                ledger.reconcile(st);row['ledger_reconciles']=True
        return row
    def advance(bt,t):return int(bt.elapse(t*NS-int(bt.current_timestamp)))
    for label,assets in [('BOUNDARY',[asset(book+[(h.DEPTH_EVENT|h.BUY_EVENT,2000,.39,100.)])]),
                         ('MULTI_ASSET',[asset(book+[(h.DEPTH_EVENT|h.BUY_EVENT,2000,.39,100.)]),asset(book+[(h.DEPTH_EVENT|h.BUY_EVENT,3000,.39,100.)])]),
                         ('EMPTY',[asset([])])]:
        bt=h.HashMapMarketDepthBacktest(assets);record=dict(case=label,snapshots=[])
        try:
            rc=int(bt.wait_next_feed(False,1));record['snapshots'].append(snapshot('INIT',bt,rc))
            if label!='EMPTY':
                for t in ([1500,2000,3000,3000] if label=='BOUNDARY' else [1500,2500,3000,4000]):
                    record['snapshots'].append(snapshot(str(t),bt,advance(bt,t)))
            else:
                assert rc==1 and int(bt.current_timestamp)==2**63-1
        finally:bt.close()
        results.append(record)
    for kind in ('RESTING','PARTIAL_TERMINAL','FILL_CANCEL','ACTIVE_TWO_PRICES'):
        feed=list(book)
        if kind=='PARTIAL_TERMINAL':feed.extend([(h.TRADE_EVENT|h.SELL_EVENT,1990,.4,.5),(h.TRADE_EVENT|h.SELL_EVENT,2000,.4,.5)])
        if kind=='FILL_CANCEL':feed.append((h.TRADE_EVENT|h.SELL_EVENT,1990,.4,.5))
        if kind=='ACTIVE_TWO_PRICES':
            feed=[(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,.03,100.),(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,.07,5.0849),(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,.29,4.9151)]
        feed.append((h.DEPTH_EVENT|h.BUY_EVENT,1550 if kind=='ACTIVE_TWO_PRICES' else 2010,.03 if kind=='ACTIVE_TWO_PRICES' else .39,100.))
        bt=h.HashMapMarketDepthBacktest([asset(feed)]);record=dict(case=kind,snapshots=[])
        reader=Reader(bt,binary)
        try:
            bt.wait_next_feed(False,1);assert advance(bt,1100)==0
            active=kind=='ACTIVE_TWO_PRICES'
            assert bt.submit_buy_order(0,1,.31 if active else .4,10. if active else 1.,h.GTC if active else h.GTX,h.LIMIT,False)==0
            if not active:
                assert advance(bt,1800)==0
                if kind=='FILL_CANCEL':assert bt.cancel(0,1,False)==0
            record['snapshots'].append(snapshot('EOF',bt,advance(bt,2500),reader))
            record['snapshots'].append(snapshot('REPEATED_EOF',bt,advance(bt,3000),reader))
            rows=record['snapshots'][-1]['receipts']
            if record['snapshots'][-1]['receipts_within_clock']:
                reader.ack(rows);assert reader.peek()==[];record['ack_once_empty']=True
            if kind=='RESTING':assert bt.orders(0).get(1).status==h.NEW
            elif kind=='PARTIAL_TERMINAL':assert len(rows)==2 and abs(sum(x['qty'] for x in rows)-1.)<1e-12
            elif kind=='FILL_CANCEL':assert len(rows)==1 and bt.orders(0).get(1).status==h.CANCELED
            else:assert len(rows)==2 and abs(sum(x['qty'] for x in rows)-10.)<1e-12
        finally:bt.close()
        results.append(record)
    out=dict(backend=str(backend),loaded=str(h.__file__),native_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),cases=results)
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(dict(status='CAPTURED',cases=len(results),output=a.output)),flush=True)


if __name__=='__main__':main()
