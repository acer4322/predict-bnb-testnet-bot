"""Native finite-resource fixtures. Owned arrays, explicit budgets, no market data."""
import gc
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
SCENARIOS=['SHARED_PRINT','QUEUE_AHEAD','PRICE_PRIORITY','BOOK_REUSE','FOK_DEPLETION','TAKE_PASSIVE_CANCEL','FRACTIONAL_RESIDUAL']


def fixture(h,np,scenario,side,row):
    from hftbacktest.order import IOC,FOK,NEW,PARTIALLY_FILLED,FILLED,CANCELED,EXPIRED
    buy=side=='BUY'; maker_price=.74 if buy else .26; take_price=.75 if buy else .25
    limit=.76 if buy else .24; evs=[]
    active=scenario in ['BOOK_REUSE','FOK_DEPLETION','TAKE_PASSIVE_CANCEL','FRACTIONAL_RESIDUAL']
    def event(kind,t,p,q):
        e=np.zeros(1,h.event_dtype)[0]; e['ev']=kind|h.EXCH_EVENT|h.LOCAL_EVENT
        e['exch_ts']=e['local_ts']=t*1_000_000; e['px']=p; e['qty']=q; evs.append(e)
    opp=h.SELL_EVENT if buy else h.BUY_EVENT
    trade=h.TRADE_EVENT|(h.SELL_EVENT if buy else h.BUY_EVENT)
    if active:
        event(h.DEPTH_SNAPSHOT_EVENT|(h.BUY_EVENT if buy else h.SELL_EVENT),1000,.73 if buy else .27,100)
        initial=1. if scenario=='FOK_DEPLETION' else (1.35 if scenario=='FRACTIONAL_RESIDUAL' else .6)
        event(h.DEPTH_SNAPSHOT_EVENT|opp,1000,take_price,initial)
        event(h.DEPTH_SNAPSHOT_EVENT|opp,1000,.80 if buy else .20,100)
        if scenario=='BOOK_REUSE':
            event(h.DEPTH_EVENT|opp,2000,take_price,.6)
            event(h.DEPTH_EVENT|opp,2500,take_price,.8)
            event(h.DEPTH_EVENT|opp,3000,take_price,.8)
        elif scenario=='TAKE_PASSIVE_CANCEL': event(trade,2000,limit,.25)
    else:
        bid=.72 if buy else .24; ask=.76 if buy else .28
        event(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,bid,100)
        event(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,ask,100)
        if scenario=='QUEUE_AHEAD':
            event(h.DEPTH_SNAPSHOT_EVENT|(h.BUY_EVENT if buy else h.SELL_EVENT),1000,maker_price,10)
            event(trade,2000,maker_price,12); event(trade,2500,maker_price,1)
        else: event(trade,2000,(.73 if buy else .27) if scenario=='PRICE_PRIORITY' else maker_price,.6)
    event(h.DEPTH_EVENT|(h.BUY_EVENT if buy else h.SELL_EVENT),6000,.70 if buy else .30,100)
    arr=np.asarray(evs,dtype=h.event_dtype); digest=hashlib.sha256(arr.tobytes()).hexdigest()
    bt=h.HashMapMarketDepthBacktest([h.BacktestAsset().data(arr).linear_asset(1.)
        .constant_order_latency(250_000_000,250_000_000).partial_fill_exchange()
        .risk_adverse_queue_model().tick_size(.01).lot_size(.01).trading_value_fee_model(-.0001,.001)])
    row['snapshots']=[]; ids=[]
    def advance(t): assert bt.elapse(t*1_000_000-int(bt.current_timestamp))==0
    def submit(oid,t,p,q,tif):
        advance(t); fn=bt.submit_buy_order if buy else bt.submit_sell_order
        assert fn(0,oid,p,q,tif,h.LIMIT,False)==0; ids.append(oid)
    def snapshot(label):
        state=bt.state_values(0); orders={}
        for oid in ids:
            o=bt.orders(0).get(oid)
            orders[str(oid)]=dict(status=int(o.status),req=int(o.req),qty=float(o.qty),leaves=float(o.leaves_qty),cum=float(o.qty-o.leaves_qty))
        r=dict(label=label,timeNs=int(bt.current_timestamp),orders=orders,native={k:float(getattr(state,k)) for k in ['position','balance','trading_volume','trading_value','num_trades','fee']})
        row['snapshots'].append(r); return r
    def check(r,amount,value,maker_value,taker_value,count):
        expected=dict(position=amount if buy else -amount,balance=-value if buy else value,
            trading_volume=amount,trading_value=value,num_trades=count,fee=maker_value*(-.0001)+taker_value*.001)
        r['expected']=expected; r['errors']={k:r['native'][k]-v for k,v in expected.items()}
        assert all(abs(x)<1e-8 for x in r['errors'].values()), ('ledger',r['errors'])
    def order(r,oid,cum,leaves,status):
        o=r['orders'][str(oid)]
        assert abs(o['cum']-cum)<1e-8 and abs(o['leaves']-leaves)<1e-8 and o['status']==status, ('order',oid,o)
    try:
        assert bt.wait_next_feed(False,1) in (0,2); advance(1100); gc.collect()
        depth=bt.depth(0)
        row['book']=dict(bid=float(depth.best_bid),ask=float(depth.best_ask))
        assert np.isfinite(depth.best_bid) and np.isfinite(depth.best_ask)
        if active:
            actual=depth.ask_qty_at_tick(75) if buy else depth.bid_qty_at_tick(25)
            assert abs(actual-initial)<1e-8
        if scenario in ['SHARED_PRINT','QUEUE_AHEAD','PRICE_PRIORITY']:
            p=(.73 if buy else .27) if scenario=='PRICE_PRIORITY' else maker_price
            submit(90,1100,p,1.,h.GTX); submit(1,1150,maker_price,2. if scenario=='QUEUE_AHEAD' else 1.,h.GTX)
            advance(1700); ack=snapshot('ACK'); check(ack,0,0,0,0,0)
            for oid in ids: assert ack['orders'][str(oid)]['status']==NEW
            advance(2300); r=snapshot('FIRST_PRINT')
            if scenario=='QUEUE_AHEAD':
                check(r,2,2*maker_price,2*maker_price,0,2)
                order(r,90,1,0,FILLED); order(r,1,1,1,PARTIALLY_FILLED)
                advance(2800); r=snapshot('SECOND_PRINT'); check(r,3,3*maker_price,3*maker_price,0,3)
                order(r,1,2,0,FILLED)
            else:
                check(r,.6,.6*maker_price,.6*maker_price,0,1)
                winner=1 if scenario=='PRICE_PRIORITY' else 90; loser=90 if winner==1 else 1
                order(r,winner,.6,.4,PARTIALLY_FILLED); order(r,loser,0,1,NEW)
        elif scenario=='BOOK_REUSE':
            submit(90,1100,limit,1,h.GTC); submit(1,1150,limit,1,h.GTC)
            advance(1700); r=snapshot('FIRST_DEPTH'); check(r,.6,.6*take_price,0,.6*take_price,1)
            order(r,90,.6,.4,PARTIALLY_FILLED); order(r,1,0,1,NEW)
            advance(2300); r=snapshot('UNCHANGED_DEPTH'); check(r,.6,.6*take_price,0,.6*take_price,1)
            advance(2800); r=snapshot('POSITIVE_INCREMENT'); check(r,.8,.6*take_price+.2*limit,.2*limit,.6*take_price,2)
            order(r,90,.8,.2,PARTIALLY_FILLED); order(r,1,0,1,NEW)
            advance(3300); r=snapshot('SECOND_UNCHANGED'); check(r,.8,.6*take_price+.2*limit,.2*limit,.6*take_price,2)
            assert bt.cancel(0,90,False)==0
            advance(3500); pending=snapshot('CANCEL_PENDING'); assert pending['orders']['90']['leaves']>.19
            advance(3900); r=snapshot('CANCELED'); order(r,90,.8,.2,CANCELED)
            submit(7,4000,limit,1,h.GTC); advance(4600); r=snapshot('NO_REFUND')
            check(r,.8,.6*take_price+.2*limit,.2*limit,.6*take_price,2); order(r,7,0,1,NEW)
        elif scenario=='FOK_DEPLETION':
            submit(90,1100,limit,.6,h.GTC); submit(1,1150,limit,.5,FOK); submit(7,1200,limit,.5,IOC)
            advance(1800); r=snapshot('FOK_ATOMIC_IOC_REMAINDER'); check(r,1,take_price,0,take_price,2)
            order(r,90,.6,0,FILLED); order(r,1,0,.5,EXPIRED); order(r,7,.4,.1,EXPIRED)
        elif scenario=='TAKE_PASSIVE_CANCEL':
            submit(90,1100,limit,1.35,h.GTC); advance(1700); r=snapshot('TAKE'); check(r,.6,.6*take_price,0,.6*take_price,1)
            order(r,90,.6,.75,PARTIALLY_FILLED)
            advance(2300); r=snapshot('PASSIVE'); check(r,.85,.6*take_price+.25*limit,.25*limit,.6*take_price,2)
            assert bt.cancel(0,90,False)==0; advance(2900); r=snapshot('CANCELED'); order(r,90,.85,.5,CANCELED)
            check(r,.85,.6*take_price+.25*limit,.25*limit,.6*take_price,2)
        else:
            submit(90,1100,limit,1.35135135,h.GTC); advance(1700); r=snapshot('FRACTIONAL_REMAINDER')
            check(r,1.35,1.35*take_price,0,1.35*take_price,1); order(r,90,1.35,.00135135,PARTIALLY_FILLED)
            assert bt.cancel(0,90,False)==0; advance(2300); r=snapshot('CANCELED'); order(r,90,1.35,.00135135,CANCELED)
        final=r['native']; repeat=snapshot('REPEAT'); assert repeat['native']==final
        advance(5000); late=snapshot('LATE'); assert late['native']==final
        row['pass']=True
    finally:
        bt.close(); row['arrayUnchanged']=hashlib.sha256(arr.tobytes()).hexdigest()==digest
        assert row['arrayUnchanged']


def main():
    build=ROOT/'.tmp/hft244_accounting_build_v1'
    assert json.loads((build/'v2-controls-fix2.json').read_text())['verdict']=='LIMITED_SYNTHETIC_PASS'
    smoke=json.loads((build/'candidate-smoke.json').read_text()); package=build/'candidate_python/hftbacktest'
    assert hashlib.sha256((package/'_hftbacktest.cp313-win_amd64.pyd').read_bytes()).hexdigest()==smoke['nativeSha256']
    sys.path.insert(0,str(package.parent)); import hftbacktest as h; import numpy as np
    assert Path(h.__file__).resolve().parent==package.resolve()
    result=dict(marketBE=0,promotion=False,nativeSha256=smoke['nativeSha256'],rows=[]); start=time.monotonic()
    output=build/'v3-joint.json'; assert not output.exists()
    try:
        for scenario in SCENARIOS:
            for side in ['BUY','SELL']:
                row=dict(scenario=scenario,side=side,**{'pass':False}); result['rows'].append(row)
                (build/'v3-progress.json').write_text(json.dumps(dict(attempted=len(result['rows']),scenario=scenario,side=side)))
                fixture(h,np,scenario,side,row)
        result['verdict']='JOINT_SYNTHETIC_RESOURCE_CONTRACT_PASS'
    except Exception as exc: result.update(verdict='STOP_V3_JOINT',error=type(exc).__name__+': '+str(exc))
    result.update(attempted=len(result['rows']),passed=sum(r['pass'] for r in result['rows']),elapsedSeconds=time.monotonic()-start)
    blob=json.dumps(result,indent=2).encode(); assert len(blob)<192*1024; output.write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
    if result['verdict']!='JOINT_SYNTHETIC_RESOURCE_CONTRACT_PASS': raise SystemExit(2)


if __name__=='__main__': main()
