from __future__ import annotations
import argparse,json,math,re,sys,time
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import analyze_r4_continuous_maker_state_v1 as cms
from tools import hftbacktest_r3_r31_maker10_adapter_v1 as mk10
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot.execution_tape_archive_v1 import load_archive
EPS=1e-9

def pct(xs,p):
    if not xs:return None
    z=sorted(float(x) for x in xs);k=(len(z)-1)*p;i=int(k);f=k-i
    return z[i] if i+1>=len(z) else z[i]*(1-f)+z[i+1]*f

def configure(dr:Path):
    base.STRATEGY_DB=dr/'strategy_target_compare_v1.db'
    base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db'
    ex.BOOK_DB=dr/'wallet_maker_book_inference.db'
    tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets'

def parse_latency_profile(path:Path):
    vals=[]
    rx=re.compile(r'stage=step\.total elapsedMs=([0-9.]+)')
    for line in path.read_text(encoding='utf-8',errors='ignore').splitlines():
        m=rx.search(line)
        if m: vals.append(float(m.group(1)))
    if not vals: raise RuntimeError(f'no step.total latency values in {path}')
    return vals

def build_events(mid:int,dr:Path):
    rep,audit=mk10.run_market(mid)
    om=rep.get('orderMeta') or {}
    fills=sorted([x for x in (rep.get('makerFillEvents') or []) if float(x.get('deltaShares') or 0)>EPS],key=lambda x:int(x.get('atMs') or 0))
    takers=sorted([x for x in (rep.get('takerEvents') or []) if float(x.get('shares') or 0)>EPS],key=lambda x:int(x.get('atMs') or 0))
    cancels=sorted(rep.get('cancelEvents') or [],key=lambda x:int(x.get('atMs') or 0))
    decisions=[]
    for x in rep.get('decisionRows') or []:
        t=int(x.get('decisionMs') or x.get('atMs') or 0)
        if t>0:
            decisions.append({'atMs':t,'executionChoice':str(x.get('executionChoice') or 'WAIT'),'desired':str(x.get('desiredPortfolioAction') or ''),'actions':x.get('actions') or []})
    # de-dupe same timestamp while preserving strongest venue mutation signal
    dd={}
    rank={'WAIT':0,'MAKER':1,'TAKER':2}
    for d in decisions:
        t=d['atMs']; old=dd.get(t)
        if old is None or rank.get(d['executionChoice'],0)>rank.get(old['executionChoice'],0): dd[t]=d
    decisions=[dd[k] for k in sorted(dd)]
    books=cms.reconstruct_book_updates(mid,dr/'execution_tape_v1/markets')
    events=[]
    for oid,m in om.items():
        t=int(m.get('placedAtMs') or 0)
        if t>0:events.append((t,1,'PLACE',{'orderId':str(oid),**m}))
    for x in fills:events.append((int(x.get('atMs') or 0),0,'MAKER_FILL',x))
    for x in takers:events.append((int(x.get('atMs') or 0),0,'TAKER_FILL',x))
    for x in cancels:events.append((int(x.get('atMs') or 0),2,'CANCEL_REQUEST',x))
    for x in books:events.append((int(x['atMs']),3,'BOOK',x))
    events.sort(key=lambda z:(z[0],z[1]))
    return rep,audit,events,decisions

class Reducer:
    __slots__=('execution','book','obligation','carrier','inv','children','weak','gap','last_book')
    def __init__(self):
        self.execution=0;self.book=0;self.obligation=0;self.carrier=0
        self.inv={'maker_up':0.,'maker_down':0.,'taker_up':0.,'taker_down':0.}
        self.children={};self.weak=None;self.gap=0.;self.last_book={}
    def snap(self):return (self.execution,self.book,self.obligation,self.carrier,self.weak,round(self.gap,10))
    def reduce(self,ev):
        t,_,typ,p=ev
        if typ=='BOOK':
            # only count accepted images whose displayed top state changed
            new=(p.get('upBid'),p.get('downBid'),p.get('upAsk'),p.get('downAsk'))
            old=(self.last_book.get('upBid'),self.last_book.get('downBid'),self.last_book.get('upAsk'),self.last_book.get('downAsk')) if self.last_book else None
            if old!=new:self.book+=1
            self.last_book=dict(p)
        elif typ=='PLACE':
            oid=str(p.get('orderId') or '')
            self.children[oid]={'side':str(p.get('side') or ''),'remaining':float(p.get('shares') or 0),'cancelPending':False}
            self.carrier+=1
        elif typ=='CANCEL_REQUEST':
            oid=str(p.get('orderId') or '')
            if oid in self.children and not self.children[oid]['cancelPending']:
                self.children[oid]['cancelPending']=True;self.carrier+=1
        elif typ=='MAKER_FILL':
            side=str(p.get('side') or '');q=float(p.get('deltaShares') or 0);oid=str(p.get('orderId') or '')
            if side=='UP':self.inv['maker_up']+=q
            elif side=='DOWN':self.inv['maker_down']+=q
            if oid in self.children:
                self.children[oid]['remaining']=max(0.,self.children[oid]['remaining']-q);self.carrier+=1
            self.execution+=1
        elif typ=='TAKER_FILL':
            side=str(p.get('side') or '');q=float(p.get('shares') or 0)
            if side=='UP':self.inv['taker_up']+=q
            elif side=='DOWN':self.inv['taker_down']+=q
            self.execution+=1
        net=(self.inv['maker_up']+self.inv['taker_up'])-(self.inv['maker_down']+self.inv['taker_down'])
        gap=abs(net);weak='DOWN' if net>EPS else 'UP' if net<-EPS else None
        if abs(gap-self.gap)>EPS or weak!=self.weak:
            self.obligation+=1;self.gap=gap;self.weak=weak
        return self.snap()

def material_changed(a,b):
    # safety/action-relevant revisions only; book is tracked separately
    return a[0]!=b[0] or a[2]!=b[2] or a[3]!=b[3]

def analyze_market(mid:int,dr:Path,latencies):
    rep,audit,events,decisions=build_events(mid,dr)
    r=Reducer();timeline=[];bench=[]
    t0=time.perf_counter_ns()
    for ev in events:
        s=r.reduce(ev);timeline.append((ev[0],s,ev[2]))
    elapsed_ns=time.perf_counter_ns()-t0
    # point lookup: latest reducer state at or before timestamp
    times=[x[0] for x in timeline]
    import bisect
    def state_at(t):
        i=bisect.bisect_right(times,int(t))-1
        return timeline[i][1] if i>=0 else (0,0,0,0,None,0.0)
    exposure=[];mat=0;bookchg=0;execchg=0;obchg=0;carchg=0;venue=0;venue_stale=0;max_exec_lag=0;max_ob_lag=0;max_car_lag=0
    for i,d in enumerate(decisions):
        start=int(d['atMs']);dur=float(latencies[(i+mid)%len(latencies)]);end=int(round(start+dur))
        a=state_at(start);b=state_at(end)
        de=b[0]-a[0];db=b[1]-a[1];do=b[2]-a[2];dc=b[3]-a[3]
        ismat=material_changed(a,b);isvenue=d['executionChoice'] in {'MAKER','TAKER'} or bool(d.get('actions'))
        if ismat:mat+=1
        if db>0:bookchg+=1
        if de>0:execchg+=1
        if do>0:obchg+=1
        if dc>0:carchg+=1
        if isvenue:
            venue+=1
            if ismat:venue_stale+=1
        max_exec_lag=max(max_exec_lag,de);max_ob_lag=max(max_ob_lag,do);max_car_lag=max(max_car_lag,dc)
        exposure.append({'startMs':start,'durationMs':dur,'endMs':end,'choice':d['executionChoice'],'executionDelta':de,'bookDelta':db,'obligationDelta':do,'carrierDelta':dc,'materialRevisionChanged':ismat,'venueIntent':isvenue,'startWeak':a[4],'endWeak':b[4],'startGap':a[5],'endGap':b[5]})
    n=len(decisions)
    stale_durations=[x['durationMs'] for x in exposure if x['materialRevisionChanged']]
    exec_deltas=[x['executionDelta'] for x in exposure]
    book_deltas=[x['bookDelta'] for x in exposure]
    ob_deltas=[x['obligationDelta'] for x in exposure]
    car_deltas=[x['carrierDelta'] for x in exposure]
    event_span=max(1,(events[-1][0]-events[0][0])) if events else 1
    event_rate=len(events)/(event_span/1000.0)
    proc_us=elapsed_ns/1000.0/max(1,len(events))
    return {
      'marketId':mid,'audit':audit,'events':len(events),'decisions':n,
      'eventRatePerSec':event_rate,'reducerBenchmark':{'totalMs':elapsed_ns/1e6,'meanUsPerEvent':proc_us,'capacityEventsPerSec':1e6/proc_us if proc_us>0 else None},
      'lockExposure':{
        'materialChangedSteps':mat,'materialChangedRate':mat/n if n else None,
        'bookChangedSteps':bookchg,'bookChangedRate':bookchg/n if n else None,
        'executionChangedSteps':execchg,'obligationChangedSteps':obchg,'carrierChangedSteps':carchg,
        'venueIntentSteps':venue,'venueIntentStaleSteps':venue_stale,'venueIntentStaleRate':venue_stale/venue if venue else None,
        'executionRevisionDelta':{'mean':sum(exec_deltas)/n if n else None,'p90':pct(exec_deltas,.9),'max':max_exec_lag},
        'bookRevisionDelta':{'mean':sum(book_deltas)/n if n else None,'p90':pct(book_deltas,.9),'max':max(book_deltas) if book_deltas else 0},
        'obligationRevisionDelta':{'mean':sum(ob_deltas)/n if n else None,'p90':pct(ob_deltas,.9),'max':max_ob_lag},
        'carrierRevisionDelta':{'mean':sum(car_deltas)/n if n else None,'p90':pct(car_deltas,.9),'max':max_car_lag},
        'staleWindowMs':{'median':median(stale_durations) if stale_durations else None,'p90':pct(stale_durations,.9),'max':max(stale_durations) if stale_durations else None}
      },
      'sample': [x for x in exposure if x['materialRevisionChanged']][:30]
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--latency-json',required=True);ap.add_argument('--out',required=True);a=ap.parse_args()
    dr=Path(a.data_root).resolve();configure(dr);lat=json.loads(Path(a.latency_json).read_text(encoding='utf-8'))
    latencies=[float(x) for x in (lat.get('stepTotalMs') or [])]
    if not latencies:raise RuntimeError('empty latency profile')
    rows=[];errors=[]
    for mid in [int(x) for x in a.ids.split(',') if x.strip()]:
        try:
            z=analyze_market(mid,dr,latencies);rows.append(z);print(json.dumps({'marketId':mid,'materialRate':z['lockExposure']['materialChangedRate'],'venueStaleRate':z['lockExposure']['venueIntentStaleRate'],'meanUsPerEvent':z['reducerBenchmark']['meanUsPerEvent']},ensure_ascii=False),flush=True)
        except Exception as e:
            errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps(errors[-1]),flush=True)
    total_dec=sum(r['decisions'] for r in rows);total_mat=sum(r['lockExposure']['materialChangedSteps'] for r in rows);total_venue=sum(r['lockExposure']['venueIntentSteps'] for r in rows);total_vs=sum(r['lockExposure']['venueIntentStaleSteps'] for r in rows)
    out={'version':'R4_ASYNC_REDUCER_LOCK_DECOUPLING_V1','researchOnly':True,'actionAuthority':False,'runtimeMutation':False,'simulation':'Replay current Maker10/R3 raw-q baseline event streams. At each recorded strategy decision, apply an empirical live step.total duration as a hypothetical strategy-lock window; compare reducer revisions at window start vs end. Async reducer continues through the window; lock-coupled state is treated as frozen for exposure measurement.','latencyProfile':{'n':len(latencies),'medianMs':median(latencies),'p90Ms':pct(latencies,.9),'maxMs':max(latencies)},'rows':rows,'errors':errors,'aggregate':{'markets':len(rows),'errors':len(errors),'decisions':total_dec,'materialChangedSteps':total_mat,'materialChangedRate':total_mat/total_dec if total_dec else None,'venueIntentSteps':total_venue,'venueIntentStaleSteps':total_vs,'venueIntentStaleRate':total_vs/total_venue if total_venue else None,'meanReducerUsPerEvent':sum(r['reducerBenchmark']['meanUsPerEvent']*r['events'] for r in rows)/max(1,sum(r['events'] for r in rows)),'meanEventRatePerSec':sum(r['eventRatePerSec'] for r in rows)/max(1,len(rows)),'maxEventRatePerSec':max((r['eventRatePerSec'] for r in rows),default=None)},'boundary':'This is a state-freshness/lock-exposure experiment, not a PnL or action-policy test. Empirical live step durations are projected onto HFT decision timestamps only to quantify how much state can change while strategy work is slow.'}
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
