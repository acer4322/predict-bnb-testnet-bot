"""Frozen fee/lifecycle/depth controls; strict synthetic schedules, no market data."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
SCENARIOS=['GTC_TWO_FEE','IOC_PARTIAL','IOC_TWO_PARTIAL','PASSIVE_CANCEL','FULL_CANCEL_RACE','GTC_DEPTH_BOUND']


def fixture(h,np,scenario,side,record):
    from hftbacktest.order import NEW,FILLED,PARTIALLY_FILLED,CANCELED,EXPIRED
    buy=side=='BUY'; passive=scenario in ['PASSIVE_CANCEL','FULL_CANCEL_RACE']
    first=.75 if buy else .25; second=.76 if buy else .24
    price=(.74 if buy else .26) if passive else second
    qty=1.35; rows=[]
    def event(kind,t,p,q):
        r=np.zeros(1,h.event_dtype)[0]; r['ev']=kind|h.EXCH_EVENT|h.LOCAL_EVENT
        r['exch_ts']=r['local_ts']=t*1_000_000; r['px']=p; r['qty']=q; rows.append(r)
    if passive:
        event(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,price-.01,100)
        event(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,price+.01,100)
        event(h.TRADE_EVENT|(h.SELL_EVENT if buy else h.BUY_EVENT),2000,price,1.35 if scenario=='FULL_CANCEL_RACE' else .6)
    else:
        event(h.DEPTH_SNAPSHOT_EVENT|(h.BUY_EVENT if buy else h.SELL_EVENT),1000,.73 if buy else .27,100)
        opp=h.SELL_EVENT if buy else h.BUY_EVENT
        event(h.DEPTH_SNAPSHOT_EVENT|opp,1000,first,.6)
        if scenario in ['GTC_TWO_FEE','IOC_TWO_PARTIAL']:
            event(h.DEPTH_SNAPSHOT_EVENT|opp,1000,second,1. if scenario=='GTC_TWO_FEE' else .4)
        event(h.DEPTH_SNAPSHOT_EVENT|opp,1000,.80 if buy else .20,100)
    event(h.DEPTH_EVENT|(h.BUY_EVENT if buy else h.SELL_EVENT),5000,.70 if buy else .30,100)
    asset=(h.BacktestAsset().data(np.asarray(rows,dtype=h.event_dtype)).linear_asset(1.)
        .constant_order_latency(250_000_000,250_000_000).partial_fill_exchange()
        .risk_adverse_queue_model().tick_size(.01).lot_size(.01).trading_value_fee_model(-.0001,.001))
    bt=h.HashMapMarketDepthBacktest([asset]); record['snapshots']=[]
    def advance(t): assert bt.elapse(t*1_000_000-int(bt.current_timestamp))==0
    def snapshot(label):
        o=bt.orders(0).get(1); s=bt.state_values(0)
        r=dict(label=label,nowNs=int(bt.current_timestamp),status=int(o.status),req=int(o.req),qty=float(o.qty),leaves=float(o.leaves_qty),
               native={k:float(getattr(s,k)) for k in ['position','balance','trading_volume','trading_value','num_trades','fee']})
        record['snapshots'].append(r); return r
    def check(s,q,value,n,fee):
        expected=dict(position=q if buy else -q,balance=-value if buy else value,trading_volume=q,trading_value=value,num_trades=n,fee=fee)
        s['expected']=expected; s['errors']={k:s['native'][k]-v for k,v in expected.items()}
        assert all(abs(x)<1e-8 for x in s['errors'].values()), ('ledger',s['errors'])
    try:
        assert bt.wait_next_feed(False,1) in (0,2); advance(1100)
        tif=h.GTX if passive else (h.IOC if scenario.startswith('IOC') else h.GTC)
        submit=bt.submit_buy_order if buy else bt.submit_sell_order
        assert submit(0,1,price,qty,tif,h.LIMIT,False)==0
        advance(1599); check(snapshot('BEFORE_RESPONSE'),0,0,0,0)
        advance(1700); ack=snapshot('ACK')
        if passive:
            assert ack['status']==NEW; check(ack,0,0,0,0)
            if scenario=='FULL_CANCEL_RACE':
                advance(1950); assert bt.cancel(0,1,False)==0
                advance(2300); check(snapshot('FILL_BEFORE_REJECT'),1.35,price*1.35,1,price*1.35*(-.0001))
                q=1.35; status=FILLED
            else:
                advance(2300); check(snapshot('PARTIAL_BEFORE_CANCEL'),.6,price*.6,1,price*.6*(-.0001))
                assert bt.cancel(0,1,False)==0; q=.6; status=CANCELED
            value=price*q; n=1; fee=value*(-.0001)
        else:
            q=1.35 if scenario=='GTC_TWO_FEE' else (1. if scenario=='IOC_TWO_PARTIAL' else .6)
            value=.6*first+(q-.6)*second; n=2 if q>.6 else 1; fee=value*.001
            check(ack,q,value,n,fee)
            if scenario=='GTC_DEPTH_BOUND':
                assert ack['status']==PARTIALLY_FILLED and abs(ack['leaves']-.75)<1e-8, 'GTC residual never exercised'
                advance(2300); assert bt.cancel(0,1,False)==0; status=CANCELED
            else: status=FILLED if scenario=='GTC_TWO_FEE' else EXPIRED
        advance(3200); terminal=snapshot('TERMINAL')
        check(terminal,q,value,n,fee); assert terminal['status']==status
        assert abs(terminal['qty']-terminal['leaves']-q)<1e-8
        repeat=snapshot('REPEAT'); assert repeat['native']==terminal['native']
        advance(4000); late=snapshot('LATE'); assert late['native']==terminal['native']
        record['pass']=True
    finally: bt.close()


def main():
    build=ROOT/'.tmp/hft244_accounting_build_v1'; output=build/'v2-controls.json'
    assert not output.exists()
    assert json.loads((build/'candidate-extended.json').read_text())['verdict']=='EXTENDED_SYNTHETIC_CORRECTNESS_PASS'
    smoke=json.loads((build/'candidate-smoke.json').read_text()); package=build/'candidate_python/hftbacktest'
    assert hashlib.sha256((package/'_hftbacktest.cp313-win_amd64.pyd').read_bytes()).hexdigest()==smoke['nativeSha256']
    sys.path.insert(0,str(package.parent)); import numpy as np; import hftbacktest as h
    assert Path(h.__file__).resolve().parent==package.resolve()
    result=dict(marketBE=0,promotion=False,nativeSha256=smoke['nativeSha256']); rows=[]; start=time.monotonic()
    try:
        for scenario in SCENARIOS:
            for side in ['BUY','SELL']:
                row=dict(scenario=scenario,side=side,**{'pass':False}); rows.append(row)
                (build/'v2-controls-progress.json').write_text(json.dumps(dict(attempted=len(rows),scenario=scenario,side=side)))
                fixture(h,np,scenario,side,row)
                print(json.dumps(dict(scenario=scenario,side=side,passed=True)),flush=True)
        result['verdict']='LIMITED_SYNTHETIC_PASS'
    except Exception as exc:
        result.update(verdict='STOP_DEPTH_REALISM' if rows[-1]['scenario']=='GTC_DEPTH_BOUND' else 'STOP_NEW_CONTROL',error=type(exc).__name__+': '+str(exc))
    result.update(rows=rows,attempted=len(rows),passed=sum(r['pass'] for r in rows),elapsedSeconds=time.monotonic()-start)
    blob=json.dumps(result,indent=2).encode(); assert len(blob)<128*1024; output.write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
    if result['verdict']!='LIMITED_SYNTHETIC_PASS': raise SystemExit(2)


if __name__=='__main__': main()
