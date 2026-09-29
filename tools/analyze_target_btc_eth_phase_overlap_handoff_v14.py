from __future__ import annotations
import argparse, json, sqlite3, statistics
from pathlib import Path
from collections import Counter

EPS=1e-9

def q(xs,p):
    if not xs:return None
    z=sorted(xs);i=(len(z)-1)*p;lo=int(i);hi=min(lo+1,len(z)-1);f=i-lo
    return z[lo]*(1-f)+z[hi]*f

def classify(rows):
    out=[];cur=None;u=d=0.0
    for r in rows:
        mid=int(r['market_id'])
        if mid!=cur:
            cur=mid;u=d=0.0
        side=str(r['side']);weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
        phase='REPAIR' if weak is not None and side==weak else 'EXPAND'
        z=dict(r);z['phase']=phase;z['pre_up']=u;z['pre_down']=d;out.append(z)
        sh=float(r['shares'])
        if side=='UP':u+=sh
        else:d+=sh
    return out

def summarize(rows):
    if not rows:return {'parents':0}
    durations=[max(0,int(r['last_event_ms'])-int(r['first_event_ms'])) for r in rows]
    multi=[r for r in rows if int(r['fill_legs'] or 0)>=2]
    by={}
    for r in rows:by.setdefault(int(r['market_id']),[]).append(r)
    adj=[];handoff=[]
    for mid,z in by.items():
        z=sorted(z,key=lambda r:(int(r['first_event_ms']),str(r['parent_id'])))
        for a,b in zip(z,z[1:]):
            gap=int(b['first_event_ms'])-int(a['last_event_ms'])
            adj.append({'from':a['phase'],'to':b['phase'],'gap':gap,'overlap':gap<0,'touch':gap<=0,
                        'prevMulti':int(a['fill_legs'] or 0)>=2,'nextMulti':int(b['fill_legs'] or 0)>=2})
        # Stronger handoff test: for each phase change, does any opposite-phase prior parent remain active?
        for i,b in enumerate(z):
            if i==0:continue
            prior=z[:i]
            opp=[a for a in prior if a['phase']!=b['phase']]
            active=[a for a in opp if int(a['last_event_ms'])>=int(b['first_event_ms'])]
            if opp:
                latest=max(opp,key=lambda a:(int(a['first_event_ms']),str(a['parent_id'])))
                handoff.append({'to':b['phase'],'from':latest['phase'],'activeOpp':bool(active),
                                'latestGap':int(b['first_event_ms'])-int(latest['last_event_ms']),
                                'activeCount':len(active),
                                'activeMultiCount':sum(int(a['fill_legs'] or 0)>=2 for a in active)})
    def trans(fr,to):
        z=[x for x in adj if x['from']==fr and x['to']==to]
        if not z:return {'n':0}
        gaps=[x['gap'] for x in z]
        return {'n':len(z),'overlapRate':sum(x['overlap'] for x in z)/len(z),'touchRate':sum(x['touch'] for x in z)/len(z),
                'gapMedianMs':statistics.median(gaps),'gapP25Ms':q(gaps,.25),'gapP75Ms':q(gaps,.75),
                'prevMultiLegRate':sum(x['prevMulti'] for x in z)/len(z),'nextMultiLegRate':sum(x['nextMulti'] for x in z)/len(z)}
    def ho(fr,to):
        z=[x for x in handoff if x['from']==fr and x['to']==to]
        if not z:return {'n':0}
        gaps=[x['latestGap'] for x in z]
        return {'n':len(z),'priorOppStillActiveRate':sum(x['activeOpp'] for x in z)/len(z),
                'latestOppGapMedianMs':statistics.median(gaps),'activeOppCountMean':statistics.mean(x['activeCount'] for x in z),
                'activeOppMultiLegRate':sum(x['activeMultiCount']>0 for x in z)/len(z)}
    return {
        'parents':len(rows),'multiLegRate':len(multi)/len(rows),
        'durationMedianMs':statistics.median(durations),'durationP75Ms':q(durations,.75),'durationP90Ms':q(durations,.90),
        'phaseCounts':dict(Counter(r['phase'] for r in rows)),
        'adjacent':{'EXPAND_to_REPAIR':trans('EXPAND','REPAIR'),'REPAIR_to_EXPAND':trans('REPAIR','EXPAND'),
                    'EXPAND_to_EXPAND':trans('EXPAND','EXPAND'),'REPAIR_to_REPAIR':trans('REPAIR','REPAIR')},
        'handoff':{'EXPAND_to_REPAIR':ho('EXPAND','REPAIR'),'REPAIR_to_EXPAND':ho('REPAIR','EXPAND')}
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    c=sqlite3.connect(a.db);c.row_factory=sqlite3.Row
    ends={asset:{int(r['market_id']):int(r['window_end_ms']) for r in c.execute('select market_id,window_end_ms from target_markets where asset=? and window_end_ms is not null',(asset,))} for asset in ('BTC','ETH')}
    data={}
    for asset in ('BTC','ETH'):
        raw=list(c.execute("select parent_id,asset,market_id,role,side,first_event_ms,last_event_ms,average_price,shares,fill_legs from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)))
        data[asset]=classify(raw)
    c.close()
    out={'version':'TARGET_BTC_ETH_PHASE_OVERLAP_HANDOFF_V14','researchOnly':True,
         'boundary':['Target-only behavioral anatomy; no claim of internal controller implementation','Parent phase is classified relative to cumulative prior Maker-parent shares, consistent with earlier V5-style parent anatomy','Overlap evidence uses observed first_event_ms/last_event_ms and fill_legs; it asks whether old carrier activity spans a phase transition','No Target numeric threshold copied into OUR runtime'], 'assets':{}}
    for asset in ('BTC','ETH'):
        vals=sorted(set(ends[asset].values()));cut=vals[int(len(vals)*.70)]
        z=data[asset];early=[r for r in z if ends[asset].get(int(r['market_id']),0)<cut];late=[r for r in z if ends[asset].get(int(r['market_id']),0)>=cut]
        out['assets'][asset]={'cutWindowEndMs':cut,'all':summarize(z),'early':summarize(early),'late':summarize(late)}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
