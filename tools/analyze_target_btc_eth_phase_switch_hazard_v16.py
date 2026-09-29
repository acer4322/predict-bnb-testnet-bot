from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
from collections import defaultdict
import numpy as np
EPS=1e-9
TIME_BINS=[(0,1000,'0-1s'),(1000,3000,'1-3s'),(3000,5000,'3-5s'),(5000,10000,'5-10s'),(10000,20000,'10-20s'),(20000,10**12,'20s+')]
COUNT_BINS=[(1,1,'1'),(2,2,'2'),(3,3,'3'),(4,5,'4-5'),(6,10**9,'6+')]

def phase_for(u,d,side):
    weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
    return 'REPAIR' if weak is not None and side==weak else 'EXPAND'

def build(db,asset):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    ends={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset=? and window_end_ms is not null",(asset,))}
    rows=list(c.execute("select parent_id,market_id,side,first_event_ms,shares from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)));c.close()
    by=defaultdict(list)
    for r in rows:by[int(r['market_id'])].append(r)
    obs=[]
    for mid,rs in by.items():
        u=d=0.;seq=[]
        for r in rs:
            ph=phase_for(u,d,str(r['side']));seq.append((int(r['first_event_ms']),ph))
            sh=float(r['shares'])
            if str(r['side'])=='UP':u+=sh
            else:d+=sh
        if len(seq)<2:continue
        run_phase=seq[0][1];run_start=seq[0][0];run_count=1
        for i in range(len(seq)-1):
            cur_t,cur_ph=seq[i]; nxt_t,nxt_ph=seq[i+1]
            if cur_ph!=run_phase:
                run_phase=cur_ph;run_start=cur_t;run_count=1
            elapsed=max(0,nxt_t-run_start)
            obs.append({'marketId':mid,'end':ends.get(mid),'phase':cur_ph,'elapsed':elapsed,'runCount':run_count,'switch':1 if nxt_ph!=cur_ph else 0})
            if nxt_ph==cur_ph:run_count+=1
            else:run_phase=nxt_ph;run_start=nxt_t;run_count=1
    return obs

def summarize(obs):
    out={'n':len(obs),'overallSwitchRate':float(np.mean([o['switch'] for o in obs])) if obs else None,'byPhase':{}}
    for ph in ('EXPAND','REPAIR'):
        z=[o for o in obs if o['phase']==ph];p={'n':len(z),'switchRate':float(np.mean([o['switch'] for o in z])) if z else None,'timeBins':{},'countBins':{}}
        for lo,hi,name in TIME_BINS:
            q=[o for o in z if lo<=o['elapsed']<hi];p['timeBins'][name]={'n':len(q),'switchRate':float(np.mean([o['switch'] for o in q])) if q else None}
        for lo,hi,name in COUNT_BINS:
            q=[o for o in z if lo<=o['runCount']<=hi];p['countBins'][name]={'n':len(q),'switchRate':float(np.mean([o['switch'] for o in q])) if q else None}
        out['byPhase'][ph]=p
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    res={'version':'TARGET_BTC_ETH_PHASE_SWITCH_HAZARD_V16','researchOnly':True,'boundary':['Target-only Maker parent actual-filled sequence','No fixed dwell time inferred or copied into OUR runtime','Tests whether phase-switch hazard has stable time/state structure across chronology','Phase is relative to cumulative prior Maker inventory: weak-side=REPAIR, otherwise EXPAND'],'assets':{}}
    for asset in ('BTC','ETH'):
        obs=build(a.db,asset);ends=sorted(set(o['end'] for o in obs if o['end'] is not None));cut=ends[int(len(ends)*.70)] if ends else None
        early=[o for o in obs if o['end'] is not None and o['end']<cut];late=[o for o in obs if o['end'] is not None and o['end']>=cut]
        res['assets'][asset]={'cutWindowEndMs':cut,'all':summarize(obs),'early':summarize(early),'late':summarize(late)}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps(res),flush=True)
if __name__=='__main__':main()
