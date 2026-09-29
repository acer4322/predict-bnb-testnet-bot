from __future__ import annotations
import argparse, json, math, statistics, sys, tempfile, zipfile
from collections import defaultdict
from pathlib import Path

ROOT=Path.cwd()
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_execution_tape_feed_v1 as feed
from tools import hftbacktest_execution_shift_audit_v0 as ex

ARRIVAL_OFFSETS_MS=[0,250,500,750,999]
ENTRY_LATENCIES_MS=[250,1000]
QUEUE_MODELS=['risk','log']
RESPONSE_LATENCY_MS=273
POLL_MS=25


def submit_taker(bt, oid, side, price, qty):
    native_side,native_price=ex.native_order(side,price)
    if native_side=='BUY':
        return int(bt.submit_buy_order(0,oid,native_price,float(qty),ex.hbt.GTC,ex.LIMIT,False))
    return int(bt.submit_sell_order(0,oid,native_price,float(qty),ex.hbt.GTC,ex.LIMIT,False))


def run_one(row, tape_dir:Path, model:str, offset_ms:int, entry_ms:int, queue_model:str):
    feed.ARCHIVE_DIR=tape_dir
    events,_,meta=feed.build_archive_events(int(row['marketId']),trade_offset='mid')
    bt=ex.new_bt(events,entry_latency_ms=entry_ms,response_latency_ms=RESPONSE_LATENCY_MS,queue_model=queue_model)
    oid=1; cancel_issued=False; cancel_at=None; first_fill_at=None; max_cum=0.0; submit_rc=None
    obs=float(row['observedFillQty']); gap=float(row['gap']); legal=float(row['minLegalQty']); price=float(row['price'])
    if model=='observed_fill': requested=obs; responsibility=None
    elif model=='minimum_legal': requested=max(obs,legal); responsibility=None
    elif model=='gap_responsibility_cancel': requested=max(gap,legal); responsibility=gap
    else: raise ValueError(model)
    # Target event timestamp is second-quantized observed execution chronology. We anchor exchange arrival
    # to a preregistered within-second offset, then back out local submit time by entry latency.
    arrival_ms=int(row['firstEventMs'])+int(offset_ms)
    submit_ms=arrival_ms-int(entry_ms)
    parent_span=max(0,int(row['lastEventMs'])-int(row['firstEventMs']))
    terminal_ms=int(row['firstEventMs'])+max(5000,parent_span+2000)
    try:
        ex.initialize_bt(bt)
        if int(bt.current_timestamp)//1_000_000 > submit_ms:
            return {'error':'submit_before_replay_clock','currentMs':int(bt.current_timestamp)//1_000_000,'submitMs':submit_ms}
        ex.advance_to(bt,submit_ms)
        submit_rc=submit_taker(bt,oid,str(row['side']),price,requested)
        now=submit_ms
        while now < terminal_ms:
            step=min(POLL_MS,terminal_ms-now)
            rc=int(bt.elapse(int(step)*1_000_000)); now += step
            snap=ex.order_snapshot(bt,oid); cum=float(snap.get('cumExecQty') or 0.0)
            if cum>max_cum+1e-9:
                max_cum=cum
                if first_fill_at is None: first_fill_at=now
            if responsibility is not None and not cancel_issued and cum+1e-9>=responsibility:
                cur=bt.orders(0).get(oid)
                if cur is not None and int(cur.status) in {int(ex.NEW),int(ex.PARTIALLY_FILLED)} and bool(cur.cancellable):
                    bt.cancel(0,oid,False); cancel_issued=True; cancel_at=now
            if rc!=0: break
        final=ex.order_snapshot(bt,oid); predicted=float(final.get('cumExecQty') or max_cum or 0.0)
        return {'marketId':int(row['marketId']),'parentId':row['parentId'],'model':model,'arrivalOffsetMs':offset_ms,'entryLatencyMs':entry_ms,'responseLatencyMs':RESPONSE_LATENCY_MS,'queueModel':queue_model,'price':price,'side':row['side'],'observedFillQty':obs,'gap':gap,'minLegalQty':legal,'requestedQty':requested,'predictedFillQty':predicted,'absErrorVsObserved':abs(predicted-obs),'signedErrorVsObserved':predicted-obs,'absErrorVsGap':abs(predicted-gap),'fillToObserved':predicted/obs if obs>0 else None,'fillToGap':predicted/gap if gap>0 else None,'firstFillAtMs':first_fill_at,'arrivalAnchorMs':arrival_ms,'cancelIssued':cancel_issued,'cancelObservedAtMs':cancel_at,'finalStatus':final.get('status'),'submitRc':submit_rc,'tape':meta}
    finally:
        bt.close()


def summarize(results):
    out={}
    for model in ['observed_fill','minimum_legal','gap_responsibility_cancel']:
        rs=[r for r in results if r.get('model')==model and 'error' not in r]
        out[model]={'runs':len(rs),'maeObserved':statistics.mean(r['absErrorVsObserved'] for r in rs) if rs else None,'medianAbsErrorObserved':statistics.median(r['absErrorVsObserved'] for r in rs) if rs else None,'meanSignedErrorObserved':statistics.mean(r['signedErrorVsObserved'] for r in rs) if rs else None,'within20pctObserved':sum(r['absErrorVsObserved']<=max(1.0,0.2*r['observedFillQty']) for r in rs)/len(rs) if rs else None,'zeroFillRate':sum(r['predictedFillQty']<=1e-9 for r in rs)/len(rs) if rs else None,'overObservedRate':sum(r['predictedFillQty']>r['observedFillQty']+1e-9 for r in rs)/len(rs) if rs else None,'underObservedRate':sum(r['predictedFillQty']+1e-9<r['observedFillQty'] for r in rs)/len(rs) if rs else None}
    # fixed-setting summaries; no per-parent best-offset tuning
    fixed=[]
    for model in ['observed_fill','minimum_legal','gap_responsibility_cancel']:
      for entry in ENTRY_LATENCIES_MS:
       for q in QUEUE_MODELS:
        for off in ARRIVAL_OFFSETS_MS:
         rs=[r for r in results if r.get('model')==model and r.get('entryLatencyMs')==entry and r.get('queueModel')==q and r.get('arrivalOffsetMs')==off and 'error' not in r]
         if rs:
          fixed.append({'model':model,'entryLatencyMs':entry,'queueModel':q,'arrivalOffsetMs':off,'n':len(rs),'maeObserved':statistics.mean(r['absErrorVsObserved'] for r in rs),'medianAbsErrorObserved':statistics.median(r['absErrorVsObserved'] for r in rs),'within20pctObserved':sum(r['absErrorVsObserved']<=max(1.0,0.2*r['observedFillQty']) for r in rs)/len(rs),'meanPredictedFill':statistics.mean(r['predictedFillQty'] for r in rs),'meanObservedFill':statistics.mean(r['observedFillQty'] for r in rs)})
    return out,fixed


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='eth_hft_pilot_') as td:
        td=Path(td)
        with zipfile.ZipFile(a.bundle) as z: z.extractall(td)
        cohort=json.loads((td/'cohort.json').read_text(encoding='utf-8'))
        rows=cohort['rows']; results=[]
        for i,row in enumerate(rows,1):
            for model in ['observed_fill','minimum_legal','gap_responsibility_cancel']:
                for entry in ENTRY_LATENCIES_MS:
                    for q in QUEUE_MODELS:
                        for off in ARRIVAL_OFFSETS_MS:
                            try: results.append(run_one(row,td/'tapes',model,off,entry,q))
                            except Exception as e: results.append({'marketId':row['marketId'],'parentId':row['parentId'],'model':model,'entryLatencyMs':entry,'queueModel':q,'arrivalOffsetMs':off,'error':repr(e)})
            print(json.dumps({'progress':i,'total':len(rows),'marketId':row['marketId']}),flush=True)
        summary,fixed=summarize(results)
        # Per-parent uncertainty envelope across preregistered timing/queue settings.
        envelopes=[]
        for row in rows:
            for model in ['observed_fill','minimum_legal','gap_responsibility_cancel']:
                rs=[r for r in results if r.get('parentId')==row['parentId'] and r.get('model')==model and 'error' not in r]
                vals=[r['predictedFillQty'] for r in rs]
                envelopes.append({'parentId':row['parentId'],'marketId':row['marketId'],'model':model,'observedFillQty':row['observedFillQty'],'gap':row['gap'],'minLegalQty':row['minLegalQty'],'predictedMin':min(vals) if vals else None,'predictedMedian':statistics.median(vals) if vals else None,'predictedMax':max(vals) if vals else None,'observedInsideEnvelope':(min(vals)-1e-9<=row['observedFillQty']<=max(vals)+1e-9) if vals else None})
        envsum={}
        for model in ['observed_fill','minimum_legal','gap_responsibility_cancel']:
            es=[x for x in envelopes if x['model']==model and x['predictedMin'] is not None]
            envsum[model]={'parents':len(es),'observedInsideEnvelopeRate':sum(x['observedInsideEnvelope'] for x in es)/len(es) if es else None,'medianEnvelopePrediction':statistics.median(x['predictedMedian'] for x in es) if es else None,'medianObserved':statistics.median(x['observedFillQty'] for x in es) if es else None}
        payload={'version':'ETH_MIN_NOTIONAL_HFT_PILOT_V1','cohortVersion':cohort.get('version'),'cohortRows':len(rows),'preregisteredSettings':{'models':['observed_fill','minimum_legal','gap_responsibility_cancel'],'arrivalOffsetsMs':ARRIVAL_OFFSETS_MS,'entryLatenciesMs':ENTRY_LATENCIES_MS,'responseLatencyMs':RESPONSE_LATENCY_MS,'queueModels':QUEUE_MODELS,'pollMs':POLL_MS,'tradeOffset':'mid'},'summaryAllSettings':summary,'fixedSettingSummary':fixed,'envelopeSummary':envsum,'envelopes':envelopes,'results':results,'boundaries':['Pilot execution falsification only; not PnL promotion evidence','Target firstEventMs is observed/second-quantized execution chronology, so arrival offsets are preregistered sensitivity scenarios rather than known submit timestamps','Observed-fill model is a capacity sanity baseline and is structurally capped at the observed quantity','No per-parent best timing offset is promoted']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(payload,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'ok':True,'output':a.output,'cohortRows':len(rows),'summary':summary,'envelopeSummary':envsum},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
