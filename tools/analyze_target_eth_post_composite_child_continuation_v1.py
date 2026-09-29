from __future__ import annotations
import argparse,collections,json,sqlite3,statistics,time
from pathlib import Path
EPS=1e-9;SIDES=('UP','DOWN')
def opp(s):return 'DOWN' if s=='UP' else 'UP'
def pct(xs,q):
    if not xs:return None
    z=sorted(float(x) for x in xs);return z[min(len(z)-1,max(0,int(q*(len(z)-1))))]
def stats(xs):
    z=[float(x) for x in xs if x is not None]
    return {'n':len(z),'mean':sum(z)/len(z) if z else None,'median':statistics.median(z) if z else None,'p25':pct(z,.25),'p75':pct(z,.75),'p90':pct(z,.9)}
def qbin(v):
    if v<.5:return '<0.5'
    if v<1:return '0.5-1'
    if v<2:return '1-2'
    if v<5:return '2-5'
    if v<10:return '5-10'
    return '>=10'
def summarize(rows):
    n=len(rows);nxt=collections.Counter(r['nextTransition'] for r in rows if r['nextTransition'] is not None);fp=collections.Counter(r['firstPaymentTransition'] for r in rows if r['firstPaymentTransition'])
    return {'children':n,
            'nextDebtClock':{'observedChildren':sum(nxt.values()),'unresolvedNoNextClock':n-sum(nxt.values()),'counts':dict(nxt),'repairNowShare':sum(v for k,v in nxt.items() if k.startswith('REPAIR'))/sum(nxt.values()) if sum(nxt.values()) else None,'expandOnlyShare':nxt.get('EXPAND_ONLY_WITH_DEBT',0)/sum(nxt.values()) if sum(nxt.values()) else None},
            'firstPayment':{'observedChildren':sum(r['firstPaymentAgeMs'] is not None for r in rows),'ageMs':stats([r['firstPaymentAgeMs'] for r in rows if r['firstPaymentAgeMs'] is not None]),
                            'priorDebtClocksWithoutPayment':stats([r['priorDebtClocksWithoutPayment'] for r in rows if r['firstPaymentAgeMs'] is not None]),'transitionCounts':dict(fp),
                            'firstPaymentCrossesAgainShare':sum(bool(r['firstPaymentCrossesAgain']) for r in rows if r['firstPaymentAgeMs'] is not None)/sum(r['firstPaymentAgeMs'] is not None for r in rows) if any(r['firstPaymentAgeMs'] is not None for r in rows) else None},
            'childQty':stats([r['childInitialQty'] for r in rows])}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default='data/target_wallet_official_v1.db');ap.add_argument('--output',required=True);a=ap.parse_args();ts=time.time()
    c=sqlite3.connect(f'file:{a.db}?mode=ro',uri=True);c.row_factory=sqlite3.Row
    ev=list(c.execute("select id,market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where asset='ETH' order by market_id,event_ms,id"));c.close()
    by=collections.defaultdict(lambda:collections.defaultdict(list))
    for r in ev:by[int(r['market_id'])][int(r['event_ms'])].append(r)
    children=[];viol=collections.Counter();lot_id=0
    for mid,clocks in sorted(by.items()):
        qs={s:collections.deque() for s in SIDES};out={s:0.0 for s in SIDES};hold={s:0.0 for s in SIDES};active_children=[]
        for t,legs in sorted(clocks.items()):
            pre=dict(out);pre_total=pre['UP']+pre['DOWN'];pre_side='UP' if pre['UP']>EPS else ('DOWN' if pre['DOWN']>EPS else None)
            # This is the next Target fill-event clock for every currently active clean child.
            for ch in active_children:
                if ch['nextTransition'] is None and t>ch['bornAt'] and pre_total>EPS:ch['nextClockT']=t
            agg={s:{'q':0.0,'notional':0.0,'maker':0.0,'taker':0.0,'legs':0} for s in SIDES}
            for r in legs:
                s=str(r['side']).upper();q=float(r['shares']);p=float(r['price']);role=str(r['role']).upper()
                if s not in SIDES or q<=EPS:continue
                agg[s]['q']+=q;agg[s]['notional']+=q*p;agg[s]['legs']+=1
                if role=='MAKER':agg[s]['maker']+=q
                elif role=='TAKER':agg[s]['taker']+=q
            rem={s:agg[s]['q'] for s in SIDES};repair={s:0.0 for s in SIDES};touched=[]
            for pay in SIDES:
                dq=qs[opp(pay)];need=rem[pay]
                while need>EPS and dq:
                    lot=dq[0];take=min(need,float(lot['remaining']))
                    if take<=EPS:break
                    before=float(lot['remaining']);lot['remaining']-=take;out[opp(pay)]-=take;repair[pay]+=take;need-=take;touched.append((int(lot['id']),pay,take,before,float(lot['remaining'])))
                    if lot['remaining']<=EPS:dq.popleft()
                rem[pay]=need
            pair_now=min(rem['UP'],rem['DOWN'])
            if pair_now>EPS:rem['UP']-=pair_now;rem['DOWN']-=pair_now
            births=[]
            for s in SIDES:
                q=rem[s]
                if q<=EPS:continue
                lot_id+=1;lot={'id':lot_id,'side':s,'bornAt':t,'initialQty':q,'remaining':q};qs[s].append(lot);out[s]+=q;births.append(lot)
            repair_total=repair['UP']+repair['DOWN'];birth_total=sum(x['initialQty'] for x in births);post_total=out['UP']+out['DOWN']
            if pre_total>EPS:
                if repair_total>EPS and birth_total>EPS:tr='REPAIR_PLUS_EXPAND'
                elif repair_total>EPS:tr='REPAIR_PRESENT_NO_NEW_EXPAND'
                elif birth_total>EPS:tr='EXPAND_ONLY_WITH_DEBT'
                else:tr='NO_REPAIR_NO_EXPAND_WITH_DEBT'
            else:tr='NO_PRE_DEBT'
            # Resolve next-clock labels and first-payment state for previously born clean crossing children.
            touched_ids={x[0] for x in touched}
            for ch in active_children:
                if ch['nextTransition'] is None and ch.get('nextClockT')==t:ch['nextTransition']=tr
                if ch['firstPaymentAgeMs'] is None and ch['lotId'] in touched_ids:
                    ch['firstPaymentT']=t;ch['firstPaymentAgeMs']=t-ch['bornAt'];ch['firstPaymentTransition']=tr
                    ch['firstPaymentCrossesAgain']=bool(repair_total>EPS and birth_total>EPS)
                    # route share of acquisition side that paid child
                    pay=opp(ch['side']);q=agg[pay]['q'];ch['firstPaymentMakerShare']=agg[pay]['maker']/q if q>EPS else None
                elif ch['firstPaymentAgeMs'] is None and t>ch['bornAt'] and pre_total>EPS:
                    ch['priorDebtClocksWithoutPayment']+=1
            # A clean child = prior debt fully paid and a same-side acquisition residual becomes the new sole responsibility side.
            if pre_total>EPS and repair_total>=pre_total-EPS and birth_total>EPS and len(births)==1:
                child=births[0];pay_side=child['side'];same_side=repair[pay_side]>EPS
                if same_side:
                    rec={'marketId':mid,'lotId':int(child['id']),'side':child['side'],'bornAt':t,'childInitialQty':float(child['initialQty']),
                         'preDebtQty':pre_total,'crossingRepairQty':repair_total,'crossingResidualToRepairRatio':float(child['initialQty'])/repair_total if repair_total>EPS else None,
                         'birthClockSingleLeg':sum(agg[s]['legs'] for s in SIDES)==1,'birthClockOneSided':sum(agg[s]['q']>EPS for s in SIDES)==1,
                         'nextClockT':None,'nextTransition':None,'firstPaymentT':None,'firstPaymentAgeMs':None,'priorDebtClocksWithoutPayment':0,
                         'firstPaymentTransition':None,'firstPaymentCrossesAgain':False,'firstPaymentMakerShare':None}
                    children.append(rec);active_children.append(rec)
            # Stop tracking children that have been fully paid; their first payment info remains in record.
            open_ids={int(x['id']) for s in SIDES for x in qs[s]}
            active_children=[ch for ch in active_children if ch['lotId'] in open_ids]
            hold['UP']+=agg['UP']['q'];hold['DOWN']+=agg['DOWN']['q'];gap=abs(hold['UP']-hold['DOWN'])
            if abs(post_total-gap)>1e-6:viol['outstandingGapMismatch']+=1
            if out['UP']>EPS and out['DOWN']>EPS:viol['twoOutstandingSides']+=1
    byq={b:summarize([r for r in children if qbin(r['childInitialQty'])==b]) for b in ['<0.5','0.5-1','1-2','2-5','5-10','>=10']}
    out={'version':'TARGET_ETH_POST_COMPOSITE_CHILD_CONTINUATION_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,
         'cleanCrossingChildDefinition':'pre-existing debt fully paid at clock; exactly one same-side residual responsibility is born on the repair/acquisition side',
         'overall':summarize(children),'byChildInitialQty':byq,'invariantViolations':dict(viol),'runtimeSeconds':time.time()-ts,
         'interpretation':['nextTransition is the next Target fill-event clock while child debt exists; it is not a public-book receipt clock.',
          'priorDebtClocksWithoutPayment counts Target fill-event clocks before this exact child receives its first FIFO payment.',
          'firstPaymentCrossesAgain means the first payment clock also births a new residual responsibility after old debt allocation.',
          'No winner/PnL/future action is used; FIFO is a reconstruction hypothesis.']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'overall':out['overall'],'byChildInitialQty':out['byChildInitialQty'],'invariantViolations':out['invariantViolations']},ensure_ascii=False))
if __name__=='__main__':main()
