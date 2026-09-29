from __future__ import annotations
import argparse, json, math, sys
from collections import defaultdict
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
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

def reconstruct_book_updates(mid:int,archive_dir:Path):
    tape=load_archive(archive_dir/f'{mid}.json.xz')
    rows=list(tape.get('updates') or [])
    rows.sort(key=lambda r:(int(r[1]),int(r[0])))
    first=next((r for r in rows if int(r[3])==1 and r[4] is not None and r[5] is not None),None)
    if first is None:return []
    book={'bids':{float(p):float(q) for p,q in (first[4] or {}).items()},'asks':{float(p):float(q) for p,q in (first[5] or {}).items()}}
    out=[]
    def snap(ts):
        bf=base.mod.outcome_book(book,None) or {}
        out.append({'atMs':int(ts),'upBid':bf.get('up_bid'),'downBid':bf.get('down_bid'),'upAsk':bf.get('up_ask'),'downAsk':bf.get('down_ask')})
    snap(int(first[1]));passed=False
    for r in rows:
        if not passed:
            if r is first:passed=True
            continue
        ts=int(r[1]);changes=r[6] or {}
        for side in ('bids','asks'):
            for item in changes.get(side,[]) or []:
                p,before,after,delta=map(float,item)
                if after<=EPS:book[side].pop(p,None)
                else:book[side][p]=after
        snap(ts)
    # collapse same receipt timestamp to final book image
    z={}
    for r in out:z[int(r['atMs'])]=r
    return [z[k] for k in sorted(z)]

def analyze_market(mid:int,dr:Path):
    rep,audit=mk10.run_market(mid)
    om=rep.get('orderMeta') or {}
    fills=sorted([x for x in (rep.get('makerFillEvents') or []) if float(x.get('deltaShares') or 0)>EPS],key=lambda x:int(x.get('atMs') or 0))
    takers=sorted([x for x in (rep.get('takerEvents') or []) if float(x.get('shares') or 0)>EPS],key=lambda x:int(x.get('atMs') or 0))
    cancels=sorted(rep.get('cancelEvents') or [],key=lambda x:int(x.get('atMs') or 0))
    decisions=sorted(set(int(x.get('decisionMs') or x.get('atMs') or 0) for x in (rep.get('decisionRows') or []) if int(x.get('decisionMs') or x.get('atMs') or 0)>0))
    books=reconstruct_book_updates(mid,dr/'execution_tape_v1/markets')
    events=[]
    for oid,m in om.items():
        t=int(m.get('placedAtMs') or 0)
        if t>0:events.append((t,1,'PLACE',{'orderId':str(oid),**m}))
    for x in fills:events.append((int(x.get('atMs') or 0),0,'MAKER_FILL',x))
    for x in takers:events.append((int(x.get('atMs') or 0),0,'TAKER_FILL',x))
    for x in cancels:events.append((int(x.get('atMs') or 0),2,'CANCEL_REQUEST',x))
    for x in books:events.append((int(x['atMs']),3,'BOOK',x))
    events.sort(key=lambda z:(z[0],z[1]))
    active={};inv={'maker_up':0.,'maker_down':0.,'taker_up':0.,'taker_down':0.};book={}
    execution_revision=0;book_revision=0;obligation_revision=0;carrier_revision=0
    prior_gap=0.;prior_weak=None;last_signature={};trans=[];lead=[];oversize_first={};weak_flip=[];responsibility_changes=[];quote_changes=[]
    def current_gap():
        net=(inv['maker_up']+inv['taker_up'])-(inv['maker_down']+inv['taker_down'])
        return abs(net),('DOWN' if net>EPS else 'UP' if net<-EPS else None),net
    def next_decision(t):
        for d in decisions:
            if d>=t:return d
        return None
    def emit(t,kind,payload):
        nd=next_decision(t);row={'atMs':t,'kind':kind,**payload,'nextDecisionMs':nd,'leadToNextDecisionMs':(nd-t if nd is not None else None)}
        trans.append(row)
        if nd is not None and nd>=t:lead.append(nd-t)
    for t,_,typ,p in events:
        if typ=='BOOK':
            old=book.copy();book=p;book_revision+=1
        elif typ=='PLACE':
            oid=str(p['orderId']);active[oid]={'orderId':oid,'side':str(p.get('side')),'price':float(p.get('price') or 0),'originalQty':float(p.get('shares') or 0),'remainingQty':float(p.get('shares') or 0),'placedAtMs':t,'cancelPending':False};carrier_revision+=1
        elif typ=='CANCEL_REQUEST':
            oid=str(p.get('orderId') or '')
            if oid in active:
                active[oid]['cancelPending']=True;carrier_revision+=1
                emit(t,'CANCEL_PENDING',{'orderId':oid,'side':active[oid]['side'],'remainingQty':active[oid]['remainingQty']})
        elif typ=='MAKER_FILL':
            side=str(p.get('side') or '');q=float(p.get('deltaShares') or 0);oid=str(p.get('orderId') or '')
            if side=='UP':inv['maker_up']+=q
            elif side=='DOWN':inv['maker_down']+=q
            if oid in active:active[oid]['remainingQty']=max(0.,active[oid]['remainingQty']-q)
            execution_revision+=1
        elif typ=='TAKER_FILL':
            side=str(p.get('side') or '');q=float(p.get('shares') or 0)
            if side=='UP':inv['taker_up']+=q
            elif side=='DOWN':inv['taker_down']+=q
            execution_revision+=1
        gap,weak,net=current_gap()
        if abs(gap-prior_gap)>EPS or weak!=prior_weak:
            obligation_revision+=1
            responsibility_changes.append({'atMs':t,'fromGap':prior_gap,'toGap':gap,'fromWeak':prior_weak,'toWeak':weak,'sourceEvent':typ})
            emit(t,'OBLIGATION_CHANGED',{'fromGap':prior_gap,'toGap':gap,'fromWeak':prior_weak,'toWeak':weak,'sourceEvent':typ,'obligationRevision':obligation_revision})
            if prior_weak is not None and weak!=prior_weak:
                weak_flip.append(t);emit(t,'WEAK_SIDE_FLIP',{'fromWeak':prior_weak,'toWeak':weak,'gap':gap})
            prior_gap,prior_weak=gap,weak
        # compute exact responsibility relationships continuously
        side_totals=defaultdict(float)
        for o in active.values():
            if o['remainingQty']>EPS:side_totals[o['side']]+=o['remainingQty']
        for oid,o in list(active.items()):
            if o['remainingQty']<=EPS:continue
            side=o['side'];bid=book.get('upBid') if side=='UP' else book.get('downBid')
            off=((float(bid)-o['price'])/base.mod.GRID) if bid is not None and math.isfinite(float(bid)) else math.nan
            other=max(0.,side_totals[side]-o['remainingQty'])
            unowned=max(0.,gap-other) if weak==side else 0.
            owned=min(o['remainingQty'],unowned) if weak==side else 0.
            redundant=max(0.,o['remainingQty']-owned)
            sig=(weak,round(gap,8),round(owned,8),round(redundant,8),None if not math.isfinite(off) else round(off,3),bool(o['cancelPending']))
            prev=last_signature.get(oid)
            if prev is not None:
                if (prev[2],prev[3])!=(sig[2],sig[3]):
                    emit(t,'CARRIER_RESPONSIBILITY_CHANGED',{'orderId':oid,'side':side,'ownedBefore':prev[2],'ownedNow':sig[2],'redundantBefore':prev[3],'redundantNow':sig[3],'gap':gap,'weakSide':weak,'sourceEvent':typ})
                if prev[4]!=sig[4] and sig[4] is not None:
                    quote_changes.append(t)
                    emit(t,'QUOTE_REACHABILITY_CHANGED',{'orderId':oid,'side':side,'offsetBefore':prev[4],'offsetNow':sig[4],'sourceEvent':typ,'bookRevision':book_revision})
            if redundant>EPS and oid not in oversize_first:
                oversize_first[oid]=t
                emit(t,'REDUNDANT_RESPONSIBILITY_DETECTED',{'orderId':oid,'side':side,'remainingQty':o['remainingQty'],'ownedNow':owned,'redundantNow':redundant,'gap':gap,'otherSameSideReservation':other,'sourceEvent':typ})
            last_signature[oid]=sig
    # compare structural transition times to current coarse live reassessment eligibility: >=15s, zero fill, >=2 children, PASSIVE_REPAIR.
    structural=[r for r in trans if r['kind'] in {'OBLIGATION_CHANGED','WEAK_SIDE_FLIP','CARRIER_RESPONSIBILITY_CHANGED','REDUNDANT_RESPONSIBILITY_DETECTED'}]
    leads=[r['leadToNextDecisionMs'] for r in structural if r.get('leadToNextDecisionMs') is not None]
    return {'marketId':mid,'audit':audit,'eventCounts':{'rawMergedEvents':len(events),'bookUpdates':len(books),'makerFills':len(fills),'takerFills':len(takers),'placements':len(om),'cancelRequests':len(cancels),'strategyDecisions':len(decisions)},'revisions':{'execution':execution_revision,'book':book_revision,'obligation':obligation_revision,'carrier':carrier_revision},'continuousTransitions':{'total':len(trans),'structural':len(structural),'responsibilityChanges':sum(r['kind']=='CARRIER_RESPONSIBILITY_CHANGED' for r in trans),'redundantDetected':sum(r['kind']=='REDUNDANT_RESPONSIBILITY_DETECTED' for r in trans),'weakSideFlips':sum(r['kind']=='WEAK_SIDE_FLIP' for r in trans),'quoteReachabilityChanges':sum(r['kind']=='QUOTE_REACHABILITY_CHANGED' for r in trans)},'leadToNextStrategyDecisionMs':{'n':len(leads),'median':median(leads) if leads else None,'p90':pct(leads,.9),'p95':pct(leads,.95),'max':max(leads) if leads else None,'positiveLeadCount':sum(x>0 for x in leads)},'transitionSample':structural[:80]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();dr=Path(a.data_root).resolve();configure(dr)
    ids=[int(x) for x in a.ids.split(',') if x.strip()];rows=[];errors=[]
    for mid in ids:
        try:
            r=analyze_market(mid,dr);rows.append(r);print(json.dumps({'marketId':mid,'transitions':r['continuousTransitions'],'lead':r['leadToNextStrategyDecisionMs']},ensure_ascii=False),flush=True)
        except Exception as e:
            errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps(errors[-1]),flush=True)
    leads=[]
    for r in rows:
        z=r['leadToNextStrategyDecisionMs'];
        # aggregate from samples is approximate; keep weighted counts plus market medians separately.
        if z.get('median') is not None:leads.append(float(z['median']))
    out={'version':'R4_CONTINUOUS_MAKER_STATE_V1','researchOnly':True,'actionAuthority':False,'runtimeMutation':False,'question':'How much earlier can an event-driven Maker responsibility tracker observe structural state changes than the next strategy decision?','stateModel':['executionRevision','bookRevision','obligationRevision','carrierRevision','exactGap','weakSide','perCarrierRemaining','perCarrierOwnedResponsibility','perCarrierRedundantResponsibility','quoteOffset','cancelPending'],'rows':rows,'errors':errors,'aggregate':{'markets':len(rows),'errors':len(errors),'rawMergedEvents':sum(r['eventCounts']['rawMergedEvents'] for r in rows),'bookUpdates':sum(r['eventCounts']['bookUpdates'] for r in rows),'strategyDecisions':sum(r['eventCounts']['strategyDecisions'] for r in rows),'structuralTransitions':sum(r['continuousTransitions']['structural'] for r in rows),'responsibilityChanges':sum(r['continuousTransitions']['responsibilityChanges'] for r in rows),'redundantDetected':sum(r['continuousTransitions']['redundantDetected'] for r in rows),'weakSideFlips':sum(r['continuousTransitions']['weakSideFlips'] for r in rows),'quoteReachabilityChanges':sum(r['continuousTransitions']['quoteReachabilityChanges'] for r in rows),'medianOfMarketMedianLeadMs':median(leads) if leads else None},'boundary':'No action thresholds are selected. Book/fill/order events update state continuously; terminal outcome is not used. This is a liveness/state-observation study, not a PnL policy test.'}
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
