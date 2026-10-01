from __future__ import annotations
import argparse,json,sys
from collections import defaultdict
from pathlib import Path
from typing import Any
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from src.predict_bot.execution_tape_archive_v1 import load_archive
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_true_match_calibration_v0 as tm
ARCHIVE_DIR=ROOT/'data/execution_tape_v1/markets'
OUT_DIR=ROOT/'data/research/hftbacktest_execution_shift_v0'

def build_archive_events(market_id:int,*,trade_offset:str='mid')->tuple[np.ndarray,list[int],dict[str,Any]]:
    path=ARCHIVE_DIR/f'{int(market_id)}.json.xz'
    if not path.exists(): raise RuntimeError(f'execution tape archive missing for market {market_id}')
    tape=load_archive(path)
    rows=list(tape.get('updates') or [])
    if not rows: raise RuntimeError('archive has no L2 updates')
    # Replay clock remains receipt-aligned for compatibility with controller wall-clock decisions.
    rows.sort(key=lambda r:(int(r[1]),int(r[0])))
    first=next((r for r in rows if int(r[3])==1 and r[4] is not None and r[5] is not None),None)
    if first is None: raise RuntimeError('archive has no full L2 checkpoint')
    out=[]
    for p,q in (first[4] or {}).items(): out.append(ex.event_row(ex.DEPTH_SNAPSHOT_EVENT|ex.BUY_EVENT,int(first[1]),float(p),float(q)))
    for p,q in (first[5] or {}).items(): out.append(ex.event_row(ex.DEPTH_SNAPSHOT_EVENT|ex.SELL_EVENT,int(first[1]),float(p),float(q)))
    update_times=[int(first[1])]; negative=0.0
    first_key=(int(first[1]),int(first[0])); passed=False
    for r in rows:
        key=(int(r[1]),int(r[0]))
        if not passed:
            if r is first: passed=True
            continue
        ts=int(r[1]); update_times.append(ts); changes=r[6] or {}
        for side,flag in [('bids',ex.BUY_EVENT),('asks',ex.SELL_EVENT)]:
            for item in changes.get(side,[]) or []:
                p,before,after,delta=map(float,item); negative+=max(0.0,-delta); out.append(ex.event_row(ex.DEPTH_EVENT|flag,ts,p,max(0.0,after)))
    # Full raw Predict match payloads are normalized only at replay-build time.
    trades=[]
    for raw in tape.get('matches') or []:
        n=tm.normalize_match(raw)
        if n is not None: trades.append(n)
    trades.sort(key=lambda x:(int(x['tsMs']),str(x.get('transactionHash') or ''),float(x['nativeYesPrice']),float(x['qty'])))
    grouped=defaultdict(list)
    for t in trades: grouped[int(t['tsMs'])].append(t)
    for ts,vals in grouped.items():
        for i,t in enumerate(vals):
            row=ex.event_row(ex.TRADE_EVENT|(ex.BUY_EVENT if t['nativeAggressor']=='BUY' else ex.SELL_EVENT),ts,float(t['nativeYesPrice']),float(t['qty']))
            if trade_offset=='late': off=999_000_000+min(i,999)*1000
            elif trade_offset=='mid': off=500_000_000+min(i,999)*1000
            elif trade_offset=='early': off=min(i,999)*1000
            else: raise ValueError(trade_offset)
            row['exch_ts']+=off; row['local_ts']+=off; out.append(row)
    arr=np.asarray(out,dtype=ex.event_dtype); arr.sort(order=['local_ts','exch_ts'])
    update_times=sorted(set(update_times+[int(t['tsMs']) for t in trades]))
    meta={'version':'HFTBACKTEST_EXECUTION_TAPE_FEED_V1','marketId':int(market_id),'archivePath':str(path),'archiveVersion':tape.get('version'),'updates':len(rows),'executionMetaRows':len(tape.get('executionMeta') or []),'rawMatchRows':len(tape.get('matches') or []),'normalizedTrades':len(trades),'trueMatchQty':sum(float(t['qty']) for t in trades),'events':len(arr),'firstReceivedMs':int(first[1]),'lastReceivedMs':max(int(r[1]) for r in rows),'negativeDepthQty':negative,'timestampBasis':'L2 uses receivedAtMs as exchange/local replay clock; historical raw match executedAt remains second-granular','tradeOffsetPolicy':trade_offset,'pendingMetadataUsedAsDepth':False,'settlementMetadataUsedForTradeTiming':False}
    return arr,update_times,meta

def compare_old_feed(market_id:int,trade_offset:str)->dict[str,Any]:
    new,_,nm=build_archive_events(market_id,trade_offset=trade_offset)
    old,_,om=tm.depth_plus_true_trades(market_id,trade_offset=trade_offset)
    fields=['ev','exch_ts','local_ts','px','qty']
    same_len=len(new)==len(old); equal=same_len and all(np.array_equal(new[f],old[f]) for f in fields)
    return {'sameLength':same_len,'newEvents':len(new),'oldEvents':len(old),'fieldwiseExact':equal,'newFeed':nm,'oldFeed':om}

def calibrate_1513668(trade_offset:str,entry:int,response:int,queue:str)->dict[str,Any]:
    events,times,meta=build_archive_events(1513668,trade_offset=trade_offset); original=ex.build_market_events
    def patched(_book,market_id:int,*,depletion_as_trade:bool):
        if int(market_id)==1513668:return events,times,meta
        return original(_book,market_id,depletion_as_trade=depletion_as_trade)
    ex.build_market_events=patched
    try: rep=ex.live_calibration_1513668(entry_latency_ms=entry,response_latency_ms=response,queue_model=queue,depletion_as_trade=False)
    finally: ex.build_market_events=original
    rep['executionTapeFeedV1']=meta; return rep

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--market-id',type=int,default=1513668); ap.add_argument('--trade-offset',choices=['early','mid','late'],default='mid'); ap.add_argument('--compare-old',action='store_true'); ap.add_argument('--calibrate',action='store_true'); ap.add_argument('--entry-latency-ms',type=int,default=1092); ap.add_argument('--response-latency-ms',type=int,default=273); ap.add_argument('--queue-model',choices=['risk','log'],default='risk'); a=ap.parse_args()
    result={'ok':True,'marketId':a.market_id}
    if a.compare_old: result['compareOld']=compare_old_feed(a.market_id,a.trade_offset)
    if a.calibrate:
        if a.market_id!=1513668: raise RuntimeError('live truth calibration is only available for 1513668')
        rep=calibrate_1513668(a.trade_offset,a.entry_latency_ms,a.response_latency_ms,a.queue_model); out=OUT_DIR/f'cap100_1513668_execution_tape_v1_{a.trade_offset}_{a.queue_model}_lat{a.entry_latency_ms}_resp{a.response_latency_ms}.json'; out.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); result['calibration']={'path':str(out),'summary':rep['summary']}
    if not a.compare_old and not a.calibrate:
        ev,_,meta=build_archive_events(a.market_id,trade_offset=a.trade_offset); result['feed']=meta
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
