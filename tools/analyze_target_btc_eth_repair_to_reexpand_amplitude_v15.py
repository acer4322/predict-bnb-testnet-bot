from __future__ import annotations
import argparse,json,sqlite3,math,statistics
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr
EPS=1e-9

def build(db,asset):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    ends={int(r['market_id']):int(r['window_end_ms']) for r in c.execute('select market_id,window_end_ms from target_markets where asset=? and window_end_ms is not null',(asset,))}
    rows=list(c.execute("select parent_id,market_id,side,first_event_ms,shares from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)));c.close()
    out=[];cur=None;u=d=0.0;episode=None
    for r in rows:
        mid=int(r['market_id']);side=str(r['side']);sh=float(r['shares']);t=int(r['first_event_ms'])
        if mid!=cur:
            cur=mid;u=d=0.0;episode=None
        pre=abs(u-d);pu=u+sh if side=='UP' else u;pd=d+sh if side=='DOWN' else d;post=abs(pu-pd);delta=post-pre
        if delta>EPS:
            if episode is not None and episode['startDelta']>EPS:
                repair=episode['repairAmount'];frac=repair/episode['startDelta'];next_ratio=delta/episode['startDelta']
                out.append({'marketId':mid,'end':ends.get(mid),'startT':episode['startT'],'nextT':t,
                            'repairCount':episode['repairCount'],'repairAmount':repair,'repairFraction':frac,
                            'startExpandDelta':episode['startDelta'],'nextExpandDelta':delta,'nextExpandRatio':next_ratio,
                            'elapsedMs':t-episode['startT']})
            episode={'startT':t,'startDelta':delta,'repairAmount':0.0,'repairCount':0}
        elif delta<-EPS and episode is not None:
            episode['repairAmount']+=-delta;episode['repairCount']+=1
        u,d=pu,pd
    return out

def summarize(z):
    if not z:return {'n':0}
    x=np.asarray([min(2.0,max(0.0,r['repairFraction'])) for r in z],float)
    y=np.asarray([math.log1p(max(0.0,r['nextExpandRatio'])) for r in z],float)
    rho,p=spearmanr(x,y)
    bins=[('0',0,EPS),('0-25%',EPS,.25),('25-50%',.25,.5),('50-75%',.5,.75),('75-100%',.75,1.0),('100%+',1.0,1e9)]
    b={}
    for name,lo,hi in bins:
        if name=='0': q=[r for r in z if r['repairFraction']<=EPS]
        elif name=='100%+': q=[r for r in z if r['repairFraction']>=1.0]
        else:q=[r for r in z if r['repairFraction']>lo-EPS and r['repairFraction']<hi]
        if q:
            b[name]={'n':len(q),'nextExpandRatioMedian':float(statistics.median(r['nextExpandRatio'] for r in q)),
                     'nextExpandRatioMean':float(statistics.mean(r['nextExpandRatio'] for r in q)),
                     'elapsedMedianMs':float(statistics.median(r['elapsedMs'] for r in q)),
                     'repairCountMedian':float(statistics.median(r['repairCount'] for r in q))}
        else:b[name]={'n':0}
    return {'n':len(z),'spearmanRepairFractionVsNextExpandLogRatio':float(rho),'spearmanP':float(p),
            'repairFractionMedian':float(statistics.median(r['repairFraction'] for r in z)),
            'nextExpandRatioMedian':float(statistics.median(r['nextExpandRatio'] for r in z)),'bins':b}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    out={'version':'TARGET_BTC_ETH_REPAIR_TO_REEXPAND_AMPLITUDE_V15','researchOnly':True,
         'boundary':['Target-only Maker parent actual-filled sequence','No Target numeric threshold copied into OUR runtime','Tests structural association: how much repair occurred after an expansion vs size of the next expansion increment','Next expansion amplitude normalized by prior expansion gap increment; early/late chronology audited separately'], 'assets':{}}
    c=sqlite3.connect(a.db)
    for asset in ('BTC','ETH'):
        z=build(a.db,asset); ends=sorted(set(r['end'] for r in z if r['end'] is not None));cut=ends[int(len(ends)*.70)]
        early=[r for r in z if r['end'] is not None and r['end']<cut];late=[r for r in z if r['end'] is not None and r['end']>=cut]
        out['assets'][asset]={'cutWindowEndMs':cut,'all':summarize(z),'early':summarize(early),'late':summarize(late)}
    c.close();Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out),flush=True)
if __name__=='__main__':main()
