from __future__ import annotations
import argparse,json,queue,sqlite3,sys,threading,time
from dataclasses import dataclass
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import analyze_r4_async_reducer_lock_decoupling_v1 as ar
from src.predict_bot.unified_controller_paper_v2 import PublicBookTailer,outcome_book

def pct(xs,p):
    if not xs:return None
    z=sorted(float(x) for x in xs);k=(len(z)-1)*p;i=int(k);f=k-i
    return z[i] if i+1>=len(z) else z[i]*(1-f)+z[i+1]*f

@dataclass(frozen=True)
class PublishedState:
    ingestRevision:int
    executionRevision:int
    bookRevision:int
    obligationRevision:int
    carrierRevision:int
    weakSide:str|None
    gap:float
    publishedNs:int

class AsyncService:
    def __init__(self):
        self.q=queue.SimpleQueue();self.reducer=ar.Reducer();self.stop=object();self.thread=None
        self._pub=PublishedState(0,0,0,0,0,None,0.0,time.perf_counter_ns())
        self._pub_lock=threading.Lock();self.lat_us=[];self.processed=0
    def start(self):
        self.thread=threading.Thread(target=self._run,name='r4-async-reducer-shadow',daemon=True);self.thread.start()
    def submit(self,ev):self.q.put((time.perf_counter_ns(),ev))
    def snapshot(self):
        with self._pub_lock:return self._pub
    def _run(self):
        rev=0
        while True:
            item=self.q.get()
            if item is self.stop:return
            enq,ev=item; s=self.reducer.reduce(ev); rev+=1; now=time.perf_counter_ns()
            pub=PublishedState(rev,s[0],s[1],s[2],s[3],s[4],float(s[5]),now)
            with self._pub_lock:self._pub=pub
            self.processed+=1;self.lat_us.append((now-enq)/1000.0)
    def close(self):
        self.q.put(self.stop);self.thread.join(timeout=5)

def burn_cpu(seconds:float):
    end=time.perf_counter()+seconds;x=0
    while time.perf_counter()<end:
        x=(x*1664525+1013904223)&0xffffffff
    return x

def concurrency_case(events,mode:str,hold_s:float):
    svc=AsyncService();svc.start();strategy_lock=threading.Lock();started=threading.Event();released=threading.Event()
    def strategy():
        with strategy_lock:
            started.set()
            if mode=='SLEEP':time.sleep(hold_s)
            else:burn_cpu(hold_s)
        released.set()
    th=threading.Thread(target=strategy,daemon=True);th.start();started.wait(1)
    n=min(len(events),3000);src=(events*(3000//max(1,len(events))+1))[:n]
    before=svc.snapshot();t0=time.perf_counter_ns()
    for ev in src:svc.submit(ev)
    # wait until all submitted events published, without waiting for strategy lock release
    deadline=time.perf_counter()+max(2.0,hold_s+1.0)
    while svc.processed<n and time.perf_counter()<deadline:time.sleep(0.001)
    done_before_release=svc.processed==n and not released.is_set()
    publish_done_ms=(time.perf_counter_ns()-t0)/1e6
    after=svc.snapshot();th.join(timeout=hold_s+1);svc.close()
    z=list(svc.lat_us)
    return {'mode':mode,'strategyLockHoldMs':hold_s*1000,'submitted':n,'processed':svc.processed,'allProcessed':svc.processed==n,'allPublishedBeforeStrategyLockReleased':done_before_release,'publishBatchMs':publish_done_ms,'eventLatencyUs':{'median':median(z) if z else None,'p90':pct(z,.9),'p99':pct(z,.99),'max':max(z) if z else None},'startIngestRevision':before.ingestRevision,'endIngestRevision':after.ingestRevision}

def independent_tailer_case(db:Path,mid:int):
    con=sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True);rows=con.execute('select source_timestamp_ms from maker_book_inference_updates where market_id=? order by id',(mid,)).fetchall();con.close()
    if len(rows)<5:raise RuntimeError('insufficient book rows')
    t1=int(rows[max(1,len(rows)//4)][0]);t2=int(rows[-1][0])
    a=PublicBookTailer(db);b=PublicBookTailer(db)
    try:
        ok1=a.reset(mid,t1);ok2=b.reset(mid,t1);b_id0=b.last_id;b_src0=b.last_source_ms;b_book0={'bids':dict(b.book['bids']),'asks':dict(b.book['asks'])}
        oka=a.advance(mid,t2);b_unchanged=(b.last_id==b_id0 and b.last_source_ms==b_src0 and b.book==b_book0)
        okb=b.advance(mid,t2);same_final=(a.last_id==b.last_id and a.last_source_ms==b.last_source_ms and a.book==b.book)
        return {'marketId':mid,'rows':len(rows),'resetA':ok1,'resetB':ok2,'advanceA':oka,'secondTailerUnchangedWhileAAdvanced':b_unchanged,'advanceB':okb,'sameFinalState':same_final,'aLastId':a.last_id,'bLastId':b.last_id}
    finally:a.close();b.close()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,default=1801703);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();dr=Path(a.data_root).resolve();ar.configure(dr)
    rep,audit,events,decisions=ar.build_events(a.market_id,dr)
    # 1.5s ~ live median, 4.0s ~ live p95; test both sleeping and pure-Python CPU contention.
    cases=[concurrency_case(events,'SLEEP',1.5),concurrency_case(events,'CPU',1.5),concurrency_case(events,'CPU',4.0)]
    tail=independent_tailer_case(dr/'wallet_maker_book_inference.db',a.market_id)
    out={'version':'R4_ASYNC_REDUCER_SERVICE_V1_TEST','researchOnly':True,'actionAuthority':False,'marketId':a.market_id,'eventSourceCount':len(events),'concurrencyCases':cases,'independentPublicBookTailer':tail,'interpretationBoundary':'Synthetic concurrency stress uses the real replay event objects but accelerated enqueue. It tests lock independence/throughput, not market timing or PnL.'}
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
