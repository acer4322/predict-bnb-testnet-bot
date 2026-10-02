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
def qtybin(v):
    if v<2:return '<2'
    if v<5:return '2-5'
    if v<10:return '5-10'
    if v<20:return '10-20'
    return '>=20'
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',default='data/target_wallet_official_v1.db');ap.add_argument('--output',required=True);a=ap.parse_args();ts=time.time()
    c=sqlite3.connect(f'file:{a.db}?mode=ro',uri=True);c.row_factory=sqlite3.Row
    ev=list(c.execute("select id,market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where asset='ETH' order by market_id,event_ms,id"));c.close()
    by=collections.defaultdict(lambda:collections.defaultdict(list))
    for r in ev:by[int(r['market_id'])][int(r['event_ms'])].append(r)
    comp=[];repair_only=[];viol=collections.Counter();lot_id=0
    for mid,clocks in sorted(by.items()):
        qs={s:collections.deque() for s in SIDES};out={s:0.0 for s in SIDES};hold={s:0.0 for s in SIDES}
        for t,legs in sorted(clocks.items()):
            pre=dict(out);pre_total=pre['UP']+pre['DOWN'];pre_side='UP' if pre['UP']>EPS else ('DOWN' if pre['DOWN']>EPS else None)
            agg={s:{'q':0.0,'notional':0.0,'maker':0.0,'taker':0.0,'legs':0} for s in SIDES}
            for r in legs:
                s=str(r['side']).upper();q=float(r['shares']);p=float(r['price']);role=str(r['role']).upper()
                if s not in SIDES or q<=EPS:continue
                agg[s]['q']+=q;agg[s]['notional']+=q*p;agg[s]['legs']+=1
                if role=='MAKER':agg[s]['maker']+=q
                elif role=='TAKER':agg[s]['taker']+=q
            rem={s:agg[s]['q'] for s in SIDES};repair={s:0.0 for s in SIDES};touched=set()
            for pay in SIDES:
                dq=qs[opp(pay)];need=rem[pay]
                while need>EPS and dq:
                    lot=dq[0];take=min(need,float(lot['remaining']))
                    if take<=EPS:break
                    lot['remaining']-=take;out[opp(pay)]-=take;repair[pay]+=take;need-=take;touched.add(int(lot['id']))
                    if lot['remaining']<=EPS:dq.popleft()
                rem[pay]=need
            pair_now=min(rem['UP'],rem['DOWN'])
            if pair_now>EPS:rem['UP']-=pair_now;rem['DOWN']-=pair_now
            births={s:0.0 for s in SIDES}
            for s in SIDES:
                q=rem[s]
                if q<=EPS:continue
                lot_id+=1;qs[s].append({'id':lot_id,'side':s,'bornAt':t,'remaining':q});out[s]+=q;births[s]+=q
            repair_total=repair['UP']+repair['DOWN'];birth_total=births['UP']+births['DOWN'];post_total=out['UP']+out['DOWN']
            if pre_total>EPS and repair_total>EPS:
                pay_sides=[s for s in SIDES if repair[s]>EPS];birth_sides=[s for s in SIDES if births[s]>EPS]
                same_side_cross=any(repair[s]>EPS and births[s]>EPS for s in SIDES)
                full_clear=repair_total>=pre_total-EPS
                one_sided_input=sum(agg[s]['q']>EPS for s in SIDES)==1
                total_legs=sum(agg[s]['legs'] for s in SIDES)
                maker_qty=sum(agg[s]['maker'] for s in SIDES);taker_qty=sum(agg[s]['taker'] for s in SIDES);input_qty=sum(agg[s]['q'] for s in SIDES)
                rec={'marketId':mid,'t':t,'preOutstandingQty':pre_total,'preOutstandingSide':pre_side,'repairQty':repair_total,'expandBirthQty':birth_total,'postOutstandingQty':post_total,
                     'sameSideRepairThenExpand':same_side_cross,'fullyClearsPreDebt':full_clear,'oneSidedInput':one_sided_input,'bothSidesInput':not one_sided_input,
                     'totalInputQty':input_qty,'totalLegs':total_legs,'singleLegClock':total_legs==1,'repairSides':pay_sides,'expandBirthSides':birth_sides,
                     'repairFractionOfPreDebt':repair_total/pre_total,'overflowToRepairRatio':birth_total/repair_total if repair_total>EPS else None,
                     'makerInputShare':maker_qty/input_qty if input_qty>EPS else None,'takerInputShare':taker_qty/input_qty if input_qty>EPS else None,
                     'crossLot':len(touched)>=2,'touchedLots':len(touched),'qtyBin':qtybin(pre_total)}
                (comp if birth_total>EPS else repair_only).append(rec)
            hold['UP']+=agg['UP']['q'];hold['DOWN']+=agg['DOWN']['q'];gap=abs(hold['UP']-hold['DOWN'])
            if abs(post_total-gap)>1e-6:viol['outstandingGapMismatch']+=1
            if out['UP']>EPS and out['DOWN']>EPS:viol['twoOutstandingSides']+=1
    def block(rows):
        n=len(rows);totin=sum(r['totalInputQty'] for r in rows);maker=sum(r['makerInputShare']*r['totalInputQty'] for r in rows)
        return {'clocks':n,'sameSideRepairThenExpandShare':sum(r['sameSideRepairThenExpand'] for r in rows)/n if n else None,
                'fullyClearsPreDebtShare':sum(r['fullyClearsPreDebt'] for r in rows)/n if n else None,'oneSidedInputShare':sum(r['oneSidedInput'] for r in rows)/n if n else None,
                'singleLegClockShare':sum(r['singleLegClock'] for r in rows)/n if n else None,'crossLotShare':sum(r['crossLot'] for r in rows)/n if n else None,
                'preOutstandingQty':stats([r['preOutstandingQty'] for r in rows]),'repairFractionOfPreDebt':stats([r['repairFractionOfPreDebt'] for r in rows]),
                'overflowToRepairRatio':stats([r['overflowToRepairRatio'] for r in rows if r['overflowToRepairRatio'] is not None]),
                'totalLegs':stats([r['totalLegs'] for r in rows]),'makerInputQtyShare':maker/totin if totin else None}
    bybin={}
    for b in ['<2','2-5','5-10','10-20','>=20']:
        cr=[r for r in comp if r['qtyBin']==b];rr=[r for r in repair_only if r['qtyBin']==b]
        bybin[b]={'composite':block(cr),'repairOnly':block(rr),'compositeAmongRepair':len(cr)/(len(cr)+len(rr)) if cr or rr else None}
    out={'version':'TARGET_ETH_COMPOSITE_BOUNDARY_CROSSING_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,
         'composite':block(comp),'repairOnly':block(repair_only),'byPreOutstandingQtyBin':bybin,'invariantViolations':dict(viol),'runtimeSeconds':time.time()-ts,
         'interpretation':['Composite means an event clock both pays pre-existing FIFO responsibility and births new one-sided residual after direct-pair allocation.',
          'sameSideRepairThenExpand identifies literal acquisition-side boundary crossing: the same side both pays old debt and leaves new residual responsibility.',
          'This is accounting anatomy; it does not prove one private order or private intent. Multiple fill legs may share a clock.',
          'No winner/PnL/future action used.']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'composite':out['composite'],'repairOnly':out['repairOnly'],'byPreOutstandingQtyBin':out['byPreOutstandingQtyBin'],'invariantViolations':out['invariantViolations']},ensure_ascii=False))
if __name__=='__main__':main()
