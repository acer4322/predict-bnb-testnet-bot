"""EOF response-clock test with actual queued acknowledgements, not a fabricated feed."""
from pathlib import Path
import os,sys,json,hashlib,time,traceback
BASE=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'


def main():
    assert os.environ.get('BTC5M_LAN_RESULT_DIR');out=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
    result=dict(native_market_runs=0,synthetic_tests=[],new_fits=0)
    try:
        assert hashlib.sha256((BASE/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd').read_bytes()).hexdigest()==SHA
        sys.path.insert(0,str(BASE));import numpy as np;import hftbacktest as h
        for late_fill in (False,True):
            data=[]
            def event(kind,t,p,q):
                a=np.zeros(1,h.event_dtype)[0];a['ev']=kind|h.EXCH_EVENT|h.LOCAL_EVENT;a['exch_ts']=a['local_ts']=int(t*1e6);a['px']=p;a['qty']=q;data.append(a)
            event(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,.39,100.)
            event(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,.6,100.)
            if late_fill:event(h.TRADE_EVENT|h.SELL_EVENT,1990,.4,.5)
            event(h.DEPTH_EVENT|h.BUY_EVENT,2000,.39,100.)
            arr=np.asarray(data,dtype=h.event_dtype);datahash=hashlib.sha256(arr.tobytes()).hexdigest()
            bt=h.HashMapMarketDepthBacktest([h.BacktestAsset().data(arr).linear_asset(1.).constant_order_latency(250000000,250000000).partial_fill_exchange().risk_adverse_queue_model().tick_size(.01).lot_size(.01)])
            row=dict(late_fill=late_fill,snapshots=[])
            def snap(label,rc):
                osnap={}
                for i in (1,2):
                    o=bt.orders(0).get(i)
                    if o is not None:osnap[i]=dict(status=int(o.status),cancellable=bool(o.cancellable),filled=float(o.qty-o.leaves_qty))
                row['snapshots'].append(dict(label=label,rc=int(rc),current=int(bt.current_timestamp),orders=osnap))
            try:
                bt.wait_next_feed(False,1);assert bt.elapse(1100000000-int(bt.current_timestamp))==0
                assert bt.submit_buy_order(0,1,.4,18.,h.GTX,h.LIMIT,False)==0
                assert bt.elapse(1900000000-int(bt.current_timestamp))==0
                assert bt.submit_buy_order(0,2,.4,18.,h.GTX,h.LIMIT,False)==0
                rc=bt.elapse(2000000000-int(bt.current_timestamp));snap('LAST_REPLAY_POINT',rc)
                for i in (1,2):
                    o=bt.orders(0).get(i)
                    if o.cancellable:assert bt.cancel(0,i,False)==0
                for step in range(8):
                    rc=bt.wait_next_feed(True,1000000000);snap('QUEUED_RESPONSE_'+str(step),rc)
                    for i in (1,2):
                        o=bt.orders(0).get(i)
                        if o.cancellable:assert bt.cancel(0,i,False)==0
                    if all(int(bt.orders(0).get(i).status) in (3,4,6) for i in (1,2)):break
                    if rc==1:break
                row['all_terminal']=all(int(bt.orders(0).get(i).status) in (3,4,6) for i in (1,2))
                row['late_fill_preserved']=float(bt.state_values(0).position)
                row['clock_monotone']=all(b['current']>=a['current'] for a,b in zip(row['snapshots'],row['snapshots'][1:]))
                row['data_unchanged']=hashlib.sha256(arr.tobytes()).hexdigest()==datahash
            finally:bt.close()
            result['synthetic_tests'].append(row)
        assert all(x['all_terminal'] and x['clock_monotone'] and x['data_unchanged'] for x in result['synthetic_tests'])
        result['verdict']='QUEUED_NATIVE_RESPONSE_CLOCK_AND_CANCEL_CHAIN_PASS'
    except Exception as e:result.update(verdict='RESPONSE_CLOCK_ERROR',error=type(e).__name__+': '+str(e),traceback=traceback.format_exc(limit=8))
    (out/'COMPACT.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)
    if result['verdict']=='RESPONSE_CLOCK_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
