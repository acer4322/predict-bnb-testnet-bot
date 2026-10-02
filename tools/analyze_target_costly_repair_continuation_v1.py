from __future__ import annotations
import argparse,collections,json,sqlite3,statistics,math
from pathlib import Path
EPS=1e-9;SIDES=('UP','DOWN')
def opp(s):return 'DOWN' if s=='UP' else 'UP'
def qtile(xs,q):
    z=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not z:return None
    return z[min(len(z)-1,max(0,int(q*(len(z)-1))))]
def bucket(ps):
    if ps<=1.0+EPS:return 'LE1'
    if ps<=1.05+EPS:return 'GT1_LE1.05'
    if ps<=1.15+EPS:return 'GT1.05_LE1.15'
    return 'GT1.15'
def summarize(rows):
    if not rows:return {'n':0}
    def rate(k):return sum(bool(r[k]) for r in rows)/len(rows)
    dts=[r['nextExpandDtMs'] for r in rows if r['nextExpandDtMs'] is not None]
    ratios=[r['nextExpandQty']/r['repairQty'] for r in rows if r.get('nextExpandQty') is not None and r['repairQty']>EPS]
    return {'n':len(rows),'repairQty':sum(r['repairQty'] for r in rows),
            'pairMeanClock':sum(r['repairPairSum'] for r in rows)/len(rows),
            'sameClockExpandRate':rate('sameClockExpand'),
            'nextExpandWithin1s':sum(r['nextExpandDtMs'] is not None and r['nextExpandDtMs']<=1000 for r in rows)/len(rows),
            'nextExpandWithin3s':sum(r['nextExpandDtMs'] is not None and r['nextExpandDtMs']<=3000 for r in rows)/len(rows),
            'nextExpandWithin5s':sum(r['nextExpandDtMs'] is not None and r['nextExpandDtMs']<=5000 for r in rows)/len(rows),
            'nextExpandWithin10s':sum(r['nextExpandDtMs'] is not None and r['nextExpandDtMs']<=10000 for r in rows)/len(rows),
            'nextExpandObservedRate':sum(r['nextExpandDtMs'] is not None for r in rows)/len(rows),
            'nextExpandDtMsMedian':statistics.median(dts) if dts else None,
            'nextExpandDtMsP90':qtile(dts,.9),'nextExpandQtyOverRepairMedian':statistics.median(ratios) if ratios else None,
            'nextExpandQtyOverRepairP90':qtile(ratios,.9),
            'repairMakerShareMean':sum(r['repairMakerShare'] for r in rows)/len(rows),
            'nextExpandMakerShareMean':sum(r['nextExpandMakerShare'] for r in rows if r['nextExpandMakerShare'] is not None)/max(1,sum(r['nextExpandMakerShare'] is not None for r in rows)),
            'nextExpandSameSideAsRepairRate':sum(r.get('nextExpandSameSideAsRepair') is True for r in rows)/max(1,sum(r.get('nextExpandSameSideAsRepair') is not None for r in rows))}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row
    events=list(c.execute('select id,market_id,role,side,event_ms,price,shares from eth_events order by market_id,event_ms,id'));c.close()
    by=collections.defaultdict(lambda:collections.defaultdict(list))
    for r in events:by[int(r['market_id'])][int(r['event_ms'])].append(r)
    allrows=[]
    for mid,clocks in sorted(by.items()):
        queues={'UP':collections.deque(),'DOWN':collections.deque()};clockrows=[]
        for t,legs in sorted(clocks.items()):
            agg={s:{'q':0.,'not':0.,'maker':0.,'taker':0.} for s in SIDES}
            for r in legs:
                s=str(r['side']).upper();q=float(r['shares']);p=float(r['price']);role=str(r['role']).upper()
                if s not in SIDES or q<=EPS:continue
                agg[s]['q']+=q;agg[s]['not']+=q*p;agg[s]['maker']+=q*(role=='MAKER');agg[s]['taker']+=q*(role=='TAKER')
            for s in SIDES:agg[s]['px']=agg[s]['not']/agg[s]['q'] if agg[s]['q']>EPS else None
            rem={s:agg[s]['q'] for s in SIDES};repair=[];repair_side_qty=collections.Counter();expand_side_qty=collections.Counter();expand_maker=0.;expand_total=0.
            for pay in SIDES:
                dq=queues[opp(pay)];need=rem[pay]
                maker_frac=agg[pay]['maker']/agg[pay]['q'] if agg[pay]['q']>EPS else 0.
                while need>EPS and dq:
                    lot=dq[0];take=min(need,lot['remaining']);ps=lot['price']+agg[pay]['px']
                    repair.append((take,ps,maker_frac,pay));repair_side_qty[pay]+=take;lot['remaining']-=take;need-=take
                    if lot['remaining']<=EPS:dq.popleft()
                rem[pay]=need
            pair_now=min(rem['UP'],rem['DOWN']);rem['UP']-=pair_now;rem['DOWN']-=pair_now
            for s in SIDES:
                q=rem[s]
                if q<=EPS:continue
                mf=agg[s]['maker']/agg[s]['q'] if agg[s]['q']>EPS else 0.
                queues[s].append({'side':s,'bornAt':t,'remaining':q,'price':agg[s]['px']})
                expand_side_qty[s]+=q;expand_total+=q;expand_maker+=q*mf
            rq=sum(x[0] for x in repair)
            clockrows.append({'marketId':mid,'t':t,'repairQty':rq,
                'repairPairSum':sum(q*ps for q,ps,_,_ in repair)/rq if rq>EPS else None,
                'repairMakerShare':sum(q*mf for q,_,mf,_ in repair)/rq if rq>EPS else None,
                'repairSide':max(repair_side_qty,key=repair_side_qty.get) if repair_side_qty else None,
                'expandQty':expand_total,'expandMakerShare':expand_maker/expand_total if expand_total>EPS else None,
                'expandSide':max(expand_side_qty,key=expand_side_qty.get) if expand_side_qty else None})
        next_idx=None
        for i in range(len(clockrows)-1,-1,-1):
            r=clockrows[i]
            if r['expandQty']>EPS:next_idx=i
            if r['repairQty']<=EPS:continue
            same=r['expandQty']>EPS
            j=i if same else next((k for k in range(i+1,len(clockrows)) if clockrows[k]['expandQty']>EPS),None)
            ne=clockrows[j] if j is not None else None
            allrows.append({**r,'bucket':bucket(r['repairPairSum']),'sameClockExpand':same,
                'nextExpandDtMs':(ne['t']-r['t']) if ne else None,'nextExpandQty':ne['expandQty'] if ne else None,
                'nextExpandMakerShare':ne['expandMakerShare'] if ne else None,
                'nextExpandSameSideAsRepair':(ne['expandSide']==r['repairSide']) if ne and ne['expandSide'] and r['repairSide'] else None})
    groups={b:summarize([r for r in allrows if r['bucket']==b]) for b in ['LE1','GT1_LE1.05','GT1.05_LE1.15','GT1.15']}
    route_groups={'MAKER_DOMINANT':summarize([r for r in allrows if r['repairMakerShare']>=.5]),
                  'TAKER_DOMINANT':summarize([r for r in allrows if r['repairMakerShare']<.5])}
    out={'version':'TARGET_COSTLY_REPAIR_CONTINUATION_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,
         'dataset':{'markets':len(by),'repairClocks':len(allrows),'events':len(events)},'all':summarize(allrows),'byRepairPairBucket':groups,
         'byRepairRouteDominance':route_groups,
         'interpretationBoundary':['FIFO responsibility reconstruction hypothesis only, not Target private implementation','pair buckets are descriptive, never runtime thresholds','future Target Expand is posthoc anatomy only and cannot enter OUR runtime','no winner/PnL','no 8781'],
         'sample':[r for r in allrows if r['repairPairSum']>1.0][:100]}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'dataset':out['dataset'],'all':out['all'],'groups':groups,'routes':route_groups},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
