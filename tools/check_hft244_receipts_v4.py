"""Native receipt contract; preserves fixture goldens and response chronology."""
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];BUILD=ROOT/'.tmp/hft244_accounting_build_v1'
sys.path.insert(0,str(BUILD/'candidate_python'));sys.path.insert(0,str(ROOT/'tools'))
from hft244_receipt_adapter_v1 import Reader,Ledger
import hftbacktest as h
import numpy as np
NATIVE=BUILD/'candidate_python/hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
assert Path(h.__file__).resolve().parent==NATIVE.parent.resolve()
factory=h.HashMapMarketDepthBacktest


def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tools'/f'{name}.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


class Observed:
    def __init__(self,assets,record):
        self.bt=factory(assets);self.reader=Reader(self.bt,NATIVE);self.record=record
    def __getattr__(self,name):return getattr(self.bt,name)
    def close(self):
        try:
            rows=self.reader.peek();assert rows==self.reader.peek()
            ledger=Ledger();ledger.consume(rows);before=dict(ledger.native);ledger.consume(rows);assert before==ledger.native
            ledger.reconcile(self.bt.state_values(0))
            self.reader.ack(rows);assert self.reader.peek()==[]
            ledger.reconcile(self.bt.state_values(0))
            self.record.update(receipts=rows,adapterCost=ledger.cost,adapterInv=ledger.inv,owners=[dict(orderId=k[0],generation=k[1],**v) for k,v in ledger.owners.items()])
        finally:self.bt.close()


def custom(kind,record):
    from hftbacktest.order import FILLED,PARTIALLY_FILLED
    events=[]
    def ev(kind,t,p,q):
        r=np.zeros(1,h.event_dtype)[0];r['ev']=kind|h.EXCH_EVENT|h.LOCAL_EVENT;r['exch_ts']=r['local_ts']=t*1_000_000;r['px']=p;r['qty']=q;events.append(r)
    ev(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,.73,100)
    ev(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,.77 if kind!='OVERFLOW' else .75,.6 if kind=='OVERFLOW' else 100)
    if kind=='OVERFLOW':ev(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,.76,1)
    elif kind=='MIXED':
        ev(h.TRADE_EVENT|h.SELL_EVENT,2000,.74,.4);ev(h.TRADE_EVENT|h.BUY_EVENT,2000,.76,.5)
    else:
        ev(h.TRADE_EVENT|h.SELL_EVENT,2000,.74,1);ev(h.TRADE_EVENT|h.SELL_EVENT,3000,.74,.2)
    ev(h.DEPTH_EVENT|h.BUY_EVENT,6000,.70,100)
    arr=np.asarray(events,dtype=h.event_dtype)
    bt=factory([h.BacktestAsset().data(arr).linear_asset(1.).constant_order_latency(250_000_000,250_000_000).partial_fill_exchange().risk_adverse_queue_model().tick_size(.01).lot_size(.01).trading_value_fee_model(-.0001,.001)])
    reader=Reader(bt,NATIVE,capacity=1 if kind=='OVERFLOW' else 4096);ledger=Ledger()
    def advance(t):return bt.elapse(t*1_000_000-int(bt.current_timestamp))
    try:
        assert bt.wait_next_feed(False,1) in (0,2);assert advance(1100)==0
        assert bt.submit_buy_order(0,90,.76 if kind=='OVERFLOW' else .74,1.35 if kind=='OVERFLOW' else 1.,h.GTC if kind=='OVERFLOW' else h.GTX,h.LIMIT,False)==0
        if kind=='MIXED':assert bt.submit_sell_order(0,1,.76,1.,h.GTX,h.LIMIT,False)==0
        if kind=='OVERFLOW':
            rc=advance(1700);assert rc==13,('overflow must stop',rc)
            record.update(overflowStopped=True,code=rc,retainedReceipts=reader.peek());return
        assert advance(2300)==0;rows=reader.peek();ledger.consume(rows);ledger.reconcile(bt.state_values(0));reader.ack(rows)
        record['first']=rows
        if kind=='REUSE':
            assert bt.orders(0).get(90).status==FILLED
            bt.clear_inactive_orders(0);assert advance(2400)==0
            assert bt.submit_buy_order(0,90,.74,1.,h.GTX,h.LIMIT,False)==0
            assert advance(3300)==0;second=reader.peek();ledger.consume(second);ledger.reconcile(bt.state_values(0))
            assert len(second)==1 and second[0]['order_id']==90 and second[0]['generation']!=rows[0]['generation']
            assert second[0]['sequence']==2;reader.ack(second);record['second']=second
        else:
            assert len(rows)==2 and {r['order_id'] for r in rows}=={90,1}
            assert abs(ledger.cost-.416)<1e-10 and len(ledger.owners)==2
        assert reader.peek()==[];record.update(adapterCost=ledger.cost,adapterInv=ledger.inv)
    finally:bt.close()


def main():
    reference=json.loads((BUILD/'v31-smoke-reference.json').read_text())
    result=dict(marketBE=0,promotion=False,rows=[]);start=time.monotonic();output=BUILD/'v4-receipt-contract.json';assert not output.exists()
    def new(kind,side):
        row=dict(kind=kind,side=side,passed=False);result['rows'].append(row);(BUILD/'v4-progress.json').write_text(json.dumps(dict(attempted=len(result['rows']),kind=kind,side=side)));return row
    try:
        probe=load('probe_hft244_partial_receipt_accounting_v1')
        for enabled in [False,True]:
            for old in reference['rows']:
                row=new('ORIGINAL6_ON' if enabled else 'ORIGINAL6_OFF',old['side']+'_'+old['route'])
                h.HashMapMarketDepthBacktest=(lambda assets:Observed(assets,row)) if enabled else factory
                actual=probe.run_fixture(h,np,old['side'],old['route']);assert actual==old,'V31 raw behavior parity'
                row['passed']=True
        for name,scenario in [('check_hft244_extended_receipts_v1','TAKER_TWO_PRICES'),('check_hft244_native_v3_joint','TAKE_PASSIVE_CANCEL'),('check_hft244_native_v3_joint','QUEUE_AHEAD')]:
            module=load(name)
            for side in ['BUY','SELL']:
                row=new(scenario,side);h.HashMapMarketDepthBacktest=lambda assets:Observed(assets,row)
                module.fixture(h,np,scenario,side,row);rs=row['receipts'];assert rs
                if scenario=='TAKER_TWO_PRICES':
                    assert len(rs)==2 and all(r['maker']==0 for r in rs);assert abs(row['adapterCost']-1.02)<1e-10
                elif scenario=='TAKE_PASSIVE_CANCEL':assert len(rs)==2 and [r['maker'] for r in rs]==[0,1]
                else:assert len(rs)==3 and len(row['owners'])==2
                row['passed']=True
        h.HashMapMarketDepthBacktest=factory
        for kind in ['MIXED','REUSE','OVERFLOW']:
            row=new(kind,'MIXED' if kind=='MIXED' else 'BUY');custom(kind,row);row['passed']=True
        result['verdict']='RECEIPT_ADAPTER_SYNTHETIC_CONTRACT_PASS'
    except Exception as e:result.update(verdict='RECEIPT_ADAPTER_STOP',error=type(e).__name__+': '+str(e))
    finally:h.HashMapMarketDepthBacktest=factory
    result.update(attempted=len(result['rows']),passed=sum(r['passed'] for r in result['rows']),elapsedSeconds=time.monotonic()-start)
    output.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
    if result['verdict']!='RECEIPT_ADAPTER_SYNTHETIC_CONTRACT_PASS':raise SystemExit(2)


if __name__=='__main__':main()
