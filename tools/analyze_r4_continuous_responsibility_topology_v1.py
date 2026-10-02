from __future__ import annotations
import argparse,bisect,json,math,sys
from collections import Counter
from pathlib import Path
from statistics import median
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import analyze_r4_async_reducer_lock_decoupling_v1 as ar
EPS=1e-9

def pct(xs,p):
    if not xs:return None
    z=sorted(float(x) for x in xs);k=(len(z)-1)*p;i=int(k);f=k-i
    return z[i] if i+1>=len(z) else z[i]*(1-f)+z[i+1]*f

def availability_events(rep,base_events):
    ev=[z for z in base_events if z[2] not in {'MAKER_FILL','TAKER_FILL','BOOK'}]
    for x in rep.get('makerFillEvents') or []:
        q=float(x.get('deltaShares') or 0)
        if q>EPS:ev.append((int(x.get('observedAtMs') or x.get('atMs') or 0),0,'MAKER_FILL',x))
    for x in rep.get('takerEvents') or []:
        q=float(x.get('shares') or 0)
        if q>EPS:ev.append((int(x.get('decisionMs') or x.get('atMs') or 0),0,'TAKER_FILL',x))
    ev.sort(key=lambda z:(z[0],z[1]));return ev

def analyze(mid,dr):
    rep,audit,base_events,decisions0=ar.build_events(mid,dr);events=availability_events(rep,base_events)
    decisions=sorted(set(int(x.get('decisionMs') or 0) for x in rep.get('decisionRows') or [] if int(x.get('decisionMs') or 0)>0))
    inv={'maker_up':0.,'maker_down':0.,'taker_up':0.,'taker_down':0.};children={};last=None;trans=[]
    def next_dec(t):
        i=bisect.bisect_left(decisions,t);return decisions[i] if i<len(decisions) else None
    def topo(t,source):
        net=(inv['maker_up']+inv['taker_up'])-(inv['maker_down']+inv['taker_down']);gap=abs(net);weak='DOWN' if net>EPS else 'UP' if net<-EPS else None
        live=[c for c in children.values() if c['remaining']>EPS]
        wl=[c for c in live if weak and c['side']==weak];res=sum(c['remaining'] for c in wl);cancel=sum(c['remaining'] for c in wl if c['cancelPending'])
        deficit=max(0.,gap-res) if weak else 0.;excess=max(0.,res-gap) if weak else 0.
        if not weak:cov='BALANCED_NO_GAP'
        elif res<=EPS:cov='EMPTY'
        elif res<gap-EPS:cov='UNDER_COVERED'
        elif abs(res-gap)<=EPS:cov='EXACT_COVER'
        else:cov='OVER_RESERVED'
        necessary=[]
        for c in wl:
            marginal=min(c['remaining'],max(0.,gap-(res-c['remaining'])))
            necessary.append((c['id'],marginal,c['remaining']-marginal))
        optional=sum(1 for _,m,_ in necessary if m<=EPS)
        partial=sum(1 for _,m,o in necessary if m>EPS and o>EPS)
        fullnec=sum(1 for _,m,o in necessary if m>EPS and o<=EPS)
        ages=[max(0,t-int(c['placedAt'])) for c in wl]
        return {'atMs':t,'sourceEvent':source,'net':net,'gap':gap,'weakSide':weak,'coverageState':cov,'weakCarrierCount':len(wl),'weakReservationQty':res,'coverageDeficitQty':deficit,'coverageExcessQty':excess,'cancelPendingReservationQty':cancel,'marginalOptionalCarrierCount':optional,'partiallyNecessaryCarrierCount':partial,'fullyNecessaryCarrierCount':fullnec,'youngestWeakCarrierAgeMs':min(ages) if ages else None,'oldestWeakCarrierAgeMs':max(ages) if ages else None,'marginal':necessary}
    for t,_,typ,p in events:
        if typ=='PLACE':
            oid=str(p.get('orderId') or '');children[oid]={'id':oid,'side':str(p.get('side') or ''),'remaining':float(p.get('shares') or 0),'placedAt':t,'cancelPending':False}
        elif typ=='CANCEL_REQUEST':
            oid=str(p.get('orderId') or '')
            if oid in children:children[oid]['cancelPending']=True
        elif typ=='MAKER_FILL':
            side=str(p.get('side') or '');q=float(p.get('deltaShares') or 0);oid=str(p.get('orderId') or '')
            if side=='UP':inv['maker_up']+=q
            elif side=='DOWN':inv['maker_down']+=q
            if oid in children:children[oid]['remaining']=max(0.,children[oid]['remaining']-q)
        elif typ=='TAKER_FILL':
            side=str(p.get('side') or '');q=float(p.get('shares') or 0)
            if side=='UP':inv['taker_up']+=q
            elif side=='DOWN':inv['taker_down']+=q
        cur=topo(t,typ)
        sig=(cur['weakSide'],cur['coverageState'],round(cur['weakReservationQty'],8),round(cur['gap'],8),cur['weakCarrierCount'],cur['marginalOptionalCarrierCount'],round(cur['cancelPendingReservationQty'],8))
        if last is None or sig!=last[0]:
            nd=next_dec(t);cur['nextDecisionMs']=nd;cur['leadToNextDecisionMs']=None if nd is None else nd-t
            if last is not None:
                prev=last[1];cur['weakSideChanged']=prev['weakSide']!=cur['weakSide'];cur['coverageStateChanged']=prev['coverageState']!=cur['coverageState'];cur['optionalityChanged']=prev['marginalOptionalCarrierCount']!=cur['marginalOptionalCarrierCount'];cur['reservationChanged']=abs(prev['weakReservationQty']-cur['weakReservationQty'])>EPS
            else:cur.update(weakSideChanged=False,coverageStateChanged=False,optionalityChanged=False,reservationChanged=False)
            trans.append(cur);last=(sig,cur)
    structural=[x for x in trans if x['weakSideChanged'] or x['coverageStateChanged'] or x['optionalityChanged']]
    leads=[x['leadToNextDecisionMs'] for x in structural if x.get('leadToNextDecisionMs') is not None]
    pre15=[x for x in structural if x.get('oldestWeakCarrierAgeMs') is not None and x['oldestWeakCarrierAgeMs']<15000]
    return {'marketId':mid,'audit':audit,'events':len(events),'strategyDecisions':len(decisions),'topologyTransitions':len(trans),'structuralTopologyTransitions':len(structural),'weakSideChanges':sum(x['weakSideChanged'] for x in structural),'coverageStateChanges':sum(x['coverageStateChanged'] for x in structural),'optionalityChanges':sum(x['optionalityChanged'] for x in structural),'structuralBeforeOldestWeakCarrier15s':len(pre15),'coverageStates':dict(Counter(x['coverageState'] for x in trans)),'leadToNextDecisionMs':{'n':len(leads),'median':median(leads) if leads else None,'p90':pct(leads,.9),'max':max(leads) if leads else None,'positive':sum(x>0 for x in leads)},'sample':structural[:50]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();dr=Path(a.data_root).resolve();ar.configure(dr);rows=[];errors=[]
    for mid in [int(x) for x in a.ids.split(',') if x.strip()]:
        try:
            z=analyze(mid,dr);rows.append(z);print(json.dumps({'marketId':mid,'structural':z['structuralTopologyTransitions'],'pre15':z['structuralBeforeOldestWeakCarrier15s'],'lead':z['leadToNextDecisionMs']},ensure_ascii=False),flush=True)
        except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps(errors[-1]),flush=True)
    leads=[]
    for r in rows:
        if r['leadToNextDecisionMs']['median'] is not None:leads.append(float(r['leadToNextDecisionMs']['median']))
    out={'version':'R4_CONTINUOUS_RESPONSIBILITY_TOPOLOGY_V1','researchOnly':True,'actionAuthority':False,'rows':rows,'errors':errors,'aggregate':{'markets':len(rows),'errors':len(errors),'topologyTransitions':sum(r['topologyTransitions'] for r in rows),'structuralTopologyTransitions':sum(r['structuralTopologyTransitions'] for r in rows),'weakSideChanges':sum(r['weakSideChanges'] for r in rows),'coverageStateChanges':sum(r['coverageStateChanges'] for r in rows),'optionalityChanges':sum(r['optionalityChanges'] for r in rows),'structuralBeforeOldestWeakCarrier15s':sum(r['structuralBeforeOldestWeakCarrier15s'] for r in rows),'medianOfMarketMedianLeadMs':median(leads) if leads else None},'semantics':'Responsibility is represented as portfolio coverage plus per-child marginal necessity/optionality. Multiple carriers may be mutually substitutable; the reducer does not force exclusive ownership assignment. Cancel-pending remaining quantity stays reserved.','boundary':'No child is canceled/repriced by this study. No stale timer or PnL label is used.'}
    p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
