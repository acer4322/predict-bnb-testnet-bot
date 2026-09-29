"""Tiny synthetic native tests: drain real queued replies after last data event.
No appended market data, no looped book snapshots, no invented settlement/expiry.
"""
from pathlib import Path
import os,sys,json,hashlib,time,traceback
BASE=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'


def main():
    assert os.environ.get('BTC5M_LAN_RESULT_DIR')
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic();result=dict(market_runs=0,synthetic_runs=[],model_updates=0)
    try:
        assert hashlib.sha256((BASE/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd').read_bytes()).hexdigest()==SHA
        sys.path.insert(0,str(BASE));import numpy as np;import hftbacktest as h
        for scenario in ('NO_FILL','LATE_FILL_BEFORE_CANCEL'):
            data=[]
            def ev(kind,t,p,q):
                x=np.zeros(1,h.event_dtype)[0];x['ev']=kind|h.EXCH_EVENT|h.LOCAL_EVENT;x['exch_ts']=x['local_ts']=t*1000000;x['px']=p;x['qty']=q;data.append(x)
            ev(h.DEPTH_SNAPSHOT_EVENT|h.BUY_EVENT,1000,.39,100.)
            ev(h.DEPTH_SNAPSHOT_EVENT|h.SELL_EVENT,1000,.6,100.)
            if scenario!='NO_FILL':ev(h.TRADE_EVENT|h.SELL_EVENT,1990,.4,.5)
            ev(h.DEPTH_EVENT|h.BUY_EVENT,2000,.39,100.)
            arr=np.asarray(data,dtype=h.event_dtype);before=hashlib.sha256(arr.tobytes()).hexdigest()
            asset=h.BacktestAsset().data(arr).linear_asset(1.).constant_order_latency(250000000,250000000).partial_fill_exchange().risk_adverse_queue_model().tick_size(.01).lot_size(.01)
            bt=h.HashMapMarketDepthBacktest([asset]);row=dict(scenario=scenario,snapshots=[],synthetic=True)
            def snap(label,rc):
                o=bt.orders(0).get(1);s=bt.state_values(0)
                row['snapshots'].append(dict(label=label,rc=int(rc),now_ns=int(bt.current_timestamp),status=None if o is None else int(o.status),
                    cancellable=None if o is None else bool(o.cancellable),filled=None if o is None else float(o.qty-o.leaves_qty),
                    position=float(s.position),balance=float(s.balance)))
            try:
                bt.wait_next_feed(False,1)
                rc=bt.elapse(1100000000-int(bt.current_timestamp));snap('BEFORE_SUBMIT',rc)
                assert bt.submit_buy_order(0,1,.4,18.,h.GTX,h.LIMIT,False)==0
                rc=bt.elapse(2000000000-int(bt.current_timestamp));snap('LAST_SOURCE_EVENT',rc)
                assert bt.orders(0).get(1).cancellable
                rc=bt.cancel(0,1,False);snap('CANCEL_SENT_AT_SOURCE_END',rc)
                # Advance only native clocks; transport250+250 from the cancel.
                rc=bt.elapse(max(0,2500000001-int(bt.current_timestamp)));snap('QUEUED_REPLY_DRAIN',rc)
                o=bt.orders(0).get(1)
                row['terminal_after_native_drain']=int(o.status) in (3,4,6)
                row['position_after_drain']=float(bt.state_values(0).position)
                row['data_unchanged']=hashlib.sha256(arr.tobytes()).hexdigest()==before
                assert row['data_unchanged']
            finally:bt.close()
            result['synthetic_runs'].append(row)
        result['verdict']='NATIVE_EOF_TRANSPORT_PROBED'
    except Exception as e:result.update(verdict='EOF_PROBE_ERROR',error=type(e).__name__+': '+str(e),traceback=traceback.format_exc(limit=8))
    result['elapsed_seconds']=time.monotonic()-start
    (out/'COMPACT.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)
    if result['verdict']=='EOF_PROBE_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
