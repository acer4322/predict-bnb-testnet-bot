from __future__ import annotations
import argparse,json,sqlite3,math
from collections import defaultdict,Counter
from pathlib import Path

EPS=1e-9

def econ_role(side,up,dn):
    if up+dn<=EPS or abs(up-dn)<=EPS:return 'INIT'
    weak='UP' if up<dn else 'DOWN'
    return 'REPAIR' if side==weak else 'EXPAND'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row
    mend={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    raw=list(c.execute("select parent_id,market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' order by market_id,first_event_ms,parent_id"));c.close()
    by=defaultdict(list)
    for r in raw:by[int(r['market_id'])].append(r)
    ends=sorted(set(mend.values()));c1=ends[int(len(ends)*.60)];c2=ends[int(len(ends)*.80)]
    rows=[]
    for mid,evs in by.items():
        up=dn=cost=0.0;hist=[]
        for i,r in enumerate(evs):
            side=str(r['side']);route=str(r['role']);t=int(r['first_event_ms']);q=float(r['shares']);px=float(r['average_price']);pre_gap=abs(up-dn);er=econ_role(side,up,dn)
            pre_floor=min(up,dn)-cost;pre_best=max(up,dn)-cost
            pre_up,pre_dn=up,dn
            if side=='UP':up+=q
            else:dn+=q
            cost+=q*px
            post_gap=abs(up-dn);post_floor=min(up,dn)-cost;post_best=max(up,dn)-cost
            crossing=(er=='REPAIR' and pre_gap>EPS and q>=pre_gap-EPS)
            if crossing:
                nxt=evs[i+1] if i+1<len(evs) else None;dt=None;nr=None;next_cross=None;next_route=None
                if nxt is not None:
                    dt=int(nxt['first_event_ms'])-t
                    if dt<=30000:
                        ns=str(nxt['side']);nq=float(nxt['shares']);nr=econ_role(ns,up,dn);next_route=str(nxt['role']);ngap=abs(up-dn);next_cross=(nr=='REPAIR' and ngap>EPS and nq>=ngap-EPS)
                end=mend.get(mid);gross=up+dn;pair=min(up,dn);overflow=max(0.0,q-pre_gap)
                recent=hist[-12:];rc=Counter(x['econRole'] for x in recent);routes=Counter(x['route'] for x in recent)
                rows.append({'marketId':mid,'t':t,'windowEnd':end,'secondsLeft':None if end is None else (end-t)/1000.0,'route':route,'side':side,'price':px,'qty':q,'preGap':pre_gap,'overflow':overflow,'overflowToPreGap':overflow/max(pre_gap,EPS),'postGap':post_gap,'postPairCoverage':2*pair/gross if gross>EPS else 1.0,'postAbsnetRatio':post_gap/gross if gross>EPS else 0.0,'postFloor':post_floor,'postBest':post_best,'deltaFloor':post_floor-pre_floor,'deltaBest':post_best-pre_best,'recentRepairFrac':rc['REPAIR']/max(1,len(recent)),'recentExpandFrac':rc['EXPAND']/max(1,len(recent)),'recentTakerFrac':routes['TAKER']/max(1,len(recent)),'recentEventCount':len(recent),'nextWithin30s':int(nr is not None),'nextRole':nr,'nextRoleRepair':None if nr is None else int(nr=='REPAIR'),'nextRoleExpand':None if nr is None else int(nr=='EXPAND'),'nextIsComposite':None if nr is None else int(bool(next_cross)),'nextRoute':next_route,'nextDelayMs':dt if nr is not None else None})
            hist.append({'t':t,'econRole':er,'route':route,'side':side,'price':px,'qty':q,'postGap':post_gap,'postFloor':post_floor,'postBest':post_best})
    def split(r):
        e=int(r['windowEnd'] or 0)
        return 'train' if e<c1 else 'validation' if e<c2 else 'test'
    for r in rows:r['split']=split(r)
    def stats(z):
        active=[r for r in z if r['nextRoleRepair'] is not None]
        return {'n':len(z),'markets':len(set(r['marketId'] for r in z)),'nextWithin30sRate':sum(r['nextWithin30s'] for r in z)/max(1,len(z)),'activeLabeledN':len(active),'nextRepairRateAmongActive':sum(r['nextRoleRepair'] for r in active)/max(1,len(active)),'nextCompositeRateAmongActive':sum(r['nextIsComposite'] for r in active)/max(1,len(active)),'makerCurrentRate':sum(r['route']=='MAKER' for r in z)/max(1,len(z))}
    out={'version':'TARGET_ETH_POST_SETTLEMENT_TRANSITION_DATASET_AUDIT_V1','date':'2026-09-04','researchOnly':True,'sourceDb':str(Path(a.db).resolve()),'definition':'Target ETH parent fill is a post-settlement sample when its strict-pre-fill economic role is REPAIR and physical parent qty >= pre-fill share gap. Features are post-fill/current-past only; labels inspect only subsequent Target parent chronology.','splitCutoffs':{'trainEndExclusiveMs':c1,'validationEndExclusiveMs':c2},'aggregate':stats(rows),'splits':{s:stats([r for r in rows if r['split']==s]) for s in ['train','validation','test']},'routeBreakdown':{route:stats([r for r in rows if r['route']==route]) for route in ['MAKER','TAKER']},'examples':rows[:80],'boundary':['Target future parent role/delay used as offline label only','no winner/PnL/settlement outcome feature','Maker/Taker is execution route feature, not economic-role label','no action authority','ETH-only gradients intended']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate'],'splits':out['splits'],'routeBreakdown':out['routeBreakdown']},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
