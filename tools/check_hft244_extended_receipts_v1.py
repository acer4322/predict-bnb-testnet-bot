"""Prospective native receipt tests with schedule-defined cash, no market input."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
SCENARIOS=['MULTI_COALESCED','EXACT_THEN_TRADE','CANCEL_RACE','TAKER_SINGLE_PRICE','TAKER_TWO_PRICES']


def fixture(h,np,scenario,side,record):
    from hftbacktest.order import NEW,FILLED,PARTIALLY_FILLED,CANCELED
    buy=side=='BUY'; price=.74 if buy else .26; qty=1.35; aggressive=scenario.startswith('TAKER')
    evs=[]
    def ev(kind,t,p,q):
        r=np.zeros(1,h.event_dtype)[0]; r['ev']=kind|h.EXCH_EVENT|h.LOCAL_EVENT
        r['exch_ts']=r['local_ts']=t*1_000_000; r['px']=p; r['qty']=q; evs.append(r)
    if aggressive:
        opp=h.SELL_EVENT if buy else h.BUY_EVENT
        ev(h.DEPTH_SNAPSHOT_EVENT|(h.BUY_EVENT if buy else h.SELL_EVENT),1000,.73 if buy else .27,100)
        ev(h.DEPTH_SNAPSHOT_EVENT|opp,1000,.75 if buy else .25,.6 if scenario=='TAKER_TWO_PRICES' else 2.)
        ev(h.DEPTH_SNAPSHOT_EVENT|opp,1000,.76 if buy else .24,1.)
        price=.76 if buy else .24
    else:
        ev(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,price-.01,100)
        ev(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,price+.01,100)
        trades={'MULTI_COALESCED':[(2000,.4),(2200,.6),(2400,.35)],
                'EXACT_THEN_TRADE':[(2000,1.35),(2400,.5)],
                'CANCEL_RACE':[(2000,1.),(2300,.35)]}[scenario]
        for t,q in trades: ev(h.TRADE_EVENT|(h.SELL_EVENT if buy else h.BUY_EVENT),t,price,q)
    ev(h.DEPTH_EVENT|h.BUY_EVENT,5000,.73 if buy else .25,100)
    arr=np.asarray(evs,dtype=h.event_dtype)
    bt=h.HashMapMarketDepthBacktest([h.BacktestAsset().data(arr).linear_asset(1.)
        .constant_order_latency(250_000_000,250_000_000).partial_fill_exchange()
        .risk_adverse_queue_model().tick_size(.01).lot_size(.01)])
    record['snapshots']=[]
    def advance(t):
        rc=bt.elapse(t*1_000_000-int(bt.current_timestamp))
        assert rc==0,('elapse',t,int(rc))
    def snap(label):
        o=bt.orders(0).get(1); s=bt.state_values(0)
        r=dict(label=label,nowNs=int(bt.current_timestamp),status=int(o.status),req=int(o.req),
            qty=float(o.qty),leaves=float(o.leaves_qty),lastExecQty=float(o.exec_qty),lastExecPrice=float(o.exec_price),
            native={k:float(getattr(s,k)) for k in ['position','balance','trading_volume','trading_value','num_trades','fee']})
        record['snapshots'].append(r); return r
    def check(s,quantity,value,trades):
        expected=dict(position=quantity if buy else -quantity,balance=-value if buy else value,
                      trading_volume=quantity,trading_value=value,num_trades=trades,fee=0.)
        s['expectedNative']=expected
        s['errors']={k:s['native'][k]-v for k,v in expected.items()}
        assert all(abs(v)<1e-8 for v in s['errors'].values()),('native accounting',s['errors'])
    try:
        assert bt.wait_next_feed(False,1) in (0,2); advance(1100)
        submit=bt.submit_buy_order if buy else bt.submit_sell_order
        assert submit(0,1,price,qty,h.GTC if aggressive else h.GTX,h.LIMIT,False)==0
        advance(1700); ack=snap('ACK_OR_IMMEDIATE_FILL')
        if not aggressive:
            assert ack['status']==NEW and abs(ack['qty']-ack['leaves'])<1e-8
            check(ack,0.,0.,0)
        if scenario=='CANCEL_RACE':
            advance(1950); assert bt.cancel(0,1,False)==0
            advance(2300); partial=snap('PARTIAL_BEFORE_CANCEL_ACK')
            assert partial['status']==PARTIALLY_FILLED
            check(partial,1.,price,1)
        advance(2700); terminal=snap('TERMINAL')
        expected_qty=1. if scenario=='CANCEL_RACE' else qty
        assert terminal['status']==(CANCELED if scenario=='CANCEL_RACE' else FILLED)
        assert abs(terminal['qty']-terminal['leaves']-expected_qty)<1e-8
        if scenario=='TAKER_TWO_PRICES': value=.6*(.75 if buy else .25)+.75*(.76 if buy else .24); trades=2
        elif scenario=='TAKER_SINGLE_PRICE': value=qty*(.75 if buy else .25); trades=1
        else: value=expected_qty*price; trades=3 if scenario=='MULTI_COALESCED' else 1
        check(terminal,expected_qty,value,trades)
        repeat=snap('REPEAT_READ'); assert repeat['native']==terminal['native']
        advance(4000); late=snap('LATE_READ'); assert late['native']==terminal['native']
        record['pass']=True
    finally: bt.close()


def main():
    build=ROOT/'.tmp/hft244_accounting_build_v1'; output=build/'candidate-extended.json'
    assert not output.exists(),'immutable result exists'
    smoke=json.loads((build/'candidate-smoke.json').read_text())
    assert smoke['verdict']=='PARTIAL_ACCOUNTING_REPAIR_SMOKE_SUPPORTED'
    package=build/'candidate_python/hftbacktest'
    assert hashlib.sha256((package/'_hftbacktest.cp313-win_amd64.pyd').read_bytes()).hexdigest()==smoke['nativeSha256']
    sys.path.insert(0,str(package.parent))
    import numpy as np
    import hftbacktest as h
    assert Path(h.__file__).resolve().parent==package.resolve()
    rows=[]; start=time.time()
    result=dict(marketBE=0,modelsTrained=0,freshUsed=0,nativeSha256=smoke['nativeSha256'],promotion=False)
    try:
        for scenario in SCENARIOS:
            for side in ['BUY','SELL']:
                r=dict(scenario=scenario,side=side,pass_=False); rows.append(r)
                (build/'candidate-extended-progress.json').write_text(json.dumps(dict(attemptedSyntheticEngines=len(rows),scenario=scenario,side=side)))
                fixture(h,np,scenario,side,r)
                print(json.dumps(dict(scenario=scenario,side=side,passed=True)),flush=True)
        result['verdict']='EXTENDED_SYNTHETIC_CORRECTNESS_PASS'
    except Exception as e:
        result.update(verdict='EXTENDED_SYNTHETIC_CORRECTNESS_STOP',error=type(e).__name__+': '+str(e))
    result.update(rows=rows,attemptedSyntheticEngines=len(rows),passed=sum(bool(r.get('pass')) for r in rows),elapsedSeconds=time.time()-start)
    blob=json.dumps(result,indent=2).encode(); assert len(blob)<=96*1024; output.write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
    if result['verdict']!='EXTENDED_SYNTHETIC_CORRECTNESS_PASS': raise SystemExit(2)


if __name__=='__main__': main()
