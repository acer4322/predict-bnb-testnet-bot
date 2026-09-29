from __future__ import annotations
import json,sys,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hftbacktest_execution_tape_feed_v1 import build_archive_events
from tools import hftbacktest_execution_shift_audit_v0 as ex

def emit(x):
    sys.stdout.write(json.dumps(x,separators=(',',':'),allow_nan=True)+'\n');sys.stdout.flush()

def main():
    cfg=json.loads(sys.stdin.readline())
    mid=int(cfg['marketId']); events,_,meta=build_archive_events(mid,trade_offset=str(cfg.get('tradeOffset','mid')))
    bt=ex.new_bt(events,entry_latency_ms=int(cfg.get('entryLatencyMs',1092)),response_latency_ms=int(cfg.get('responseLatencyMs',273)),queue_model=str(cfg.get('queueModel','risk')))
    ex.initialize_bt(bt); emit({'ok':True,'meta':meta,'currentMs':int(bt.current_timestamp//1_000_000)})
    try:
        for line in sys.stdin:
            try:
                q=json.loads(line); op=q.get('op')
                if op=='advance':
                    t=int(q['ms']); ok=ex.advance_to(bt,t) if int(bt.current_timestamp)<=t*1_000_000 else True
                    emit({'ok':bool(ok),'currentMs':int(bt.current_timestamp//1_000_000)})
                elif op=='submit':
                    rc=ex.submit_native(bt,int(q['num']),str(q['side']),float(q['price']),float(q['shares']))
                    emit({'ok':True,'rc':int(rc)})
                elif op=='submit_taker':
                    n=int(q['num']); side=str(q['side']); max_price=float(q['maxPrice']); shares=float(q['shares']); native_side,native_price=ex.native_order(side,max_price)
                    if native_side=='BUY': rc=int(bt.submit_buy_order(0,n,native_price,shares,ex.hbt.GTC,ex.LIMIT,False))
                    else: rc=int(bt.submit_sell_order(0,n,native_price,shares,ex.hbt.GTC,ex.LIMIT,False))
                    emit({'ok':True,'rc':rc})
                elif op=='snap_many':
                    emit({'ok':True,'snaps':{str(int(n)):ex.order_snapshot(bt,int(n)) for n in q.get('nums',[])}})
                elif op=='cancel':
                    n=int(q['num']); cur=bt.orders(0).get(n); requested=False
                    if cur is not None and bool(cur.cancellable):
                        try: bt.cancel(0,n,False); requested=True
                        except Exception: pass
                    emit({'ok':True,'requested':requested})
                elif op=='close':
                    emit({'ok':True});break
                else: emit({'ok':False,'error':'unknown op'})
            except Exception as e: emit({'ok':False,'error':f'{type(e).__name__}: {e}'})
    finally: bt.close()
if __name__=='__main__':main()
