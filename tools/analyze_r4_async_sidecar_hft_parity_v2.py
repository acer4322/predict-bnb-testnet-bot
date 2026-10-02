from __future__ import annotations
import argparse,bisect,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_r4_async_reducer_lock_decoupling_v1 as ar
from tools.r4_async_state_sidecar_v2 import ContinuousMakerReducerV2
EPS=1e-9

def norm_events(events):
    out=[];engine_seq=0;book_id=0
    for t,prio,typ,p in events:
        if typ=='BOOK':
            book_id+=1;out.append((t,prio,{'source':'BOOK','bookId':book_id,'kind':'BOOK_UPDATE','atMs':t,'upBid':p.get('upBid'),'downBid':p.get('downBid'),'upAsk':p.get('upAsk'),'downAsk':p.get('downAsk')}))
        elif typ=='PLACE':
            oid=str(p.get('orderId') or '');out.append((t,prio,{'source':'LOCAL','eventKey':f'PLACE:{oid}','kind':'MAKER_INTENT','atMs':t,'clientOrderId':oid,'side':str(p.get('side') or ''),'qty':float(p.get('shares') or 0),'price':float(p.get('price') or 0)}))
        elif typ=='CANCEL_REQUEST':
            oid=str(p.get('orderId') or '');out.append((t,prio,{'source':'LOCAL','eventKey':f'CANCEL:{oid}:{t}','kind':'CANCEL_REQUESTED','atMs':t,'clientOrderId':oid}))
        elif typ=='MAKER_FILL':
            engine_seq+=1;out.append((t,prio,{'source':'ENGINE','engineSeq':engine_seq,'kind':'FILL_DELTA','atMs':t,'clientOrderId':str(p.get('orderId') or ''),'role':'MAKER','side':str(p.get('side') or ''),'qty':float(p.get('deltaShares') or 0)}))
        elif typ=='TAKER_FILL':
            engine_seq+=1;out.append((t,prio,{'source':'ENGINE','engineSeq':engine_seq,'kind':'FILL_DELTA','atMs':t,'clientOrderId':f'TAKER:{engine_seq}','role':'TAKER','side':str(p.get('side') or ''),'qty':float(p.get('shares') or 0)}))
    out.sort(key=lambda z:(z[0],z[1]));return out

def analyze(mid,dr):
    rep,audit,events,_=ar.build_events(mid,dr)
    # Availability-time parity: occurrence/exchange time remains evidence, but the sidecar may only apply an event when it is observable locally.
    base_events=[z for z in events if z[2] not in {'MAKER_FILL','TAKER_FILL'}]
    ne=norm_events(base_events)
    engine_seq=0
    for x in sorted(rep.get('makerFillEvents') or [],key=lambda q:(int(q.get('observedAtMs') or q.get('atMs') or 0),int(q.get('atMs') or 0))):
        if float(x.get('deltaShares') or 0)<=EPS: continue
        engine_seq+=1; avail=int(x.get('observedAtMs') or x.get('atMs') or 0)
        ne.append((avail,0,{'source':'ENGINE','engineSeq':engine_seq,'kind':'FILL_DELTA','atMs':avail,'occurredAtMs':int(x.get('atMs') or avail),'clientOrderId':str(x.get('orderId') or ''),'role':'MAKER','side':str(x.get('side') or ''),'qty':float(x.get('deltaShares') or 0)}))
    for x in sorted(rep.get('takerEvents') or [],key=lambda q:(int(q.get('decisionMs') or q.get('atMs') or 0),int(q.get('atMs') or 0))):
        if float(x.get('shares') or 0)<=EPS: continue
        engine_seq+=1; avail=int(x.get('decisionMs') or x.get('atMs') or 0)
        ne.append((avail,0,{'source':'ENGINE','engineSeq':engine_seq,'kind':'FILL_DELTA','atMs':avail,'occurredAtMs':int(x.get('atMs') or avail),'clientOrderId':f'TAKER:{engine_seq}','role':'TAKER','side':str(x.get('side') or ''),'qty':float(x.get('shares') or 0)}))
    # Re-number execution watermarks in actual local availability order.
    ne.sort(key=lambda z:(z[0],z[1])); seq=0; fixed=[]
    for t,prio,e in ne:
        if e.get('source')=='ENGINE': seq+=1; e=dict(e); e['engineSeq']=seq
        fixed.append((t,prio,e))
    ne=fixed; r=ContinuousMakerReducerV2();timeline=[]
    for t,_,e in ne:timeline.append((t,r.apply(e)))
    times=[x[0] for x in timeline]
    def state_at(t):
        i=bisect.bisect_right(times,int(t))-1
        return timeline[i][1] if i>=0 else r.__class__().snapshot()
    decisions=[];errs=[]
    seen=set()
    for d in sorted(rep.get('decisionRows') or [],key=lambda x:int(x.get('decisionMs') or 0)):
        t=int(d.get('decisionMs') or 0)
        if t<=0 or t in seen:continue
        seen.add(t);s=state_at(t);p=d.get('portfolio') if isinstance(d.get('portfolio'),dict) else {}
        cnet=float(p.get('combined_net') or 0.0);cgap=abs(cnet);cweak='DOWN' if cnet>EPS else 'UP' if cnet<-EPS else None
        snet=(s.makerUp+s.takerUp)-(s.makerDown+s.takerDown);diff=snet-cnet;gapdiff=s.exactGap-cgap;weakok=s.weakSide==cweak
        row={'atMs':t,'controllerNet':cnet,'sidecarNet':snet,'netDiff':diff,'controllerGap':cgap,'sidecarGap':s.exactGap,'gapDiff':gapdiff,'controllerWeak':cweak,'sidecarWeak':s.weakSide,'weakMatch':weakok,'executionRevision':s.executionRevision,'obligationRevision':s.obligationRevision,'carrierRevision':s.carrierRevision,'bookRevision':s.bookRevision}
        decisions.append(row)
        if abs(diff)>1e-8 or abs(gapdiff)>1e-8 or not weakok:errs.append(row)
    return {'marketId':mid,'audit':audit,'normalizedEvents':len(ne),'decisionComparisons':len(decisions),'parityErrors':len(errs),'parityRate':(len(decisions)-len(errs))/len(decisions) if decisions else None,'maxAbsNetDiff':max((abs(x['netDiff']) for x in decisions),default=0.0),'maxAbsGapDiff':max((abs(x['gapDiff']) for x in decisions),default=0.0),'errorSample':errs[:20],'finalSidecar':r.snapshot().__dict__}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();dr=Path(a.data_root).resolve();ar.configure(dr)
    rows=[];errors=[]
    for mid in [int(x) for x in a.ids.split(',') if x.strip()]:
        try:
            z=analyze(mid,dr);rows.append(z);print(json.dumps({'marketId':mid,'comparisons':z['decisionComparisons'],'parityErrors':z['parityErrors'],'parityRate':z['parityRate']},ensure_ascii=False),flush=True)
        except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps(errors[-1]),flush=True)
    n=sum(r['decisionComparisons'] for r in rows);bad=sum(r['parityErrors'] for r in rows)
    out={'version':'R4_ASYNC_SIDECAR_HFT_PARITY_V2','researchOnly':True,'actionAuthority':False,'rows':rows,'errors':errors,'aggregate':{'markets':len(rows),'errors':len(errors),'decisionComparisons':n,'parityErrors':bad,'parityRate':(n-bad)/n if n else None,'maxAbsNetDiff':max((r['maxAbsNetDiff'] for r in rows),default=None),'maxAbsGapDiff':max((r['maxAbsGapDiff'] for r in rows),default=None)},'boundary':'Sidecar consumes only current baseline order intents, HFT-confirmed fill deltas, cancel requests and book updates. Settlement/winner/PnL is not used. This tests state reconstruction parity at strategy decision timestamps, not action quality.'}
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True,default=str),encoding='utf-8');print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
