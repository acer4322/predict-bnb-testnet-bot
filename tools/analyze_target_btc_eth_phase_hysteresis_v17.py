from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
from collections import defaultdict
import numpy as np
EPS=1e-9

def phase_for(u,d,side):
    weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None
    return 'REPAIR' if weak is not None and side==weak else 'EXPAND'

def build(db,asset):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    ends={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset=? and window_end_ms is not null",(asset,))}
    rows=list(c.execute("select parent_id,market_id,side,first_event_ms,shares from target_parent_orders where asset=? and role='MAKER' order by market_id,first_event_ms,parent_id",(asset,)));c.close()
    by=defaultdict(list)
    for r in rows:by[int(r['market_id'])].append(r)
    out=[]
    for mid,rs in by.items():
        u=d=0.;seq=[]
        for r in rs:
            g=u+d;gap=abs(u-d);pair=2*min(u,d)/g if g>EPS else 0.;gr=gap/g if g>EPS else 0.
            ph=phase_for(u,d,str(r['side']))
            seq.append({'t':int(r['first_event_ms']),'phase':ph,'gapRatio':gr,'paircov':pair,'gross':g,'shares':float(r['shares'])})
            if str(r['side'])=='UP':u+=float(r['shares'])
            else:d+=float(r['shares'])
        for i in range(1,len(seq)):
            a,b=seq[i-1],seq[i]
            if a['phase']==b['phase']:continue
            bounce1=(i+1<len(seq) and seq[i+1]['phase']==a['phase'])
            bounce2=(i+2<len(seq) and (seq[i+1]['phase']==a['phase'] or seq[i+2]['phase']==a['phase']))
            out.append({'marketId':mid,'end':ends.get(mid),'transition':a['phase']+'_to_'+b['phase'],'gapRatio':b['gapRatio'],'paircov':b['paircov'],'gross':b['gross'],'nextQtyRatio':b['shares']/max(b['gross'],1.0),'gapMs':b['t']-a['t'],'bounce1':int(bounce1),'bounce2':int(bounce2)})
    return out

def qstats(z,k):
    a=np.asarray([r[k] for r in z],float)
    return {'n':len(z),'p25':float(np.quantile(a,.25)) if len(a) else None,'median':float(np.median(a)) if len(a) else None,'p75':float(np.quantile(a,.75)) if len(a) else None}

def summarize(obs):
    out={}
    for tr in ('EXPAND_to_REPAIR','REPAIR_to_EXPAND'):
        z=[r for r in obs if r['transition']==tr]
        out[tr]={'n':len(z),'gapRatio':qstats(z,'gapRatio'),'paircov':qstats(z,'paircov'),'nextQtyRatio':qstats(z,'nextQtyRatio'),'gapMs':qstats(z,'gapMs'),'bounce1Rate':float(np.mean([r['bounce1'] for r in z])) if z else None,'bounce2Rate':float(np.mean([r['bounce2'] for r in z])) if z else None}
    # simple threshold-free separation: probability random R->E gapRatio exceeds random E->R estimated by rank AUC
    a=np.asarray([r['gapRatio'] for r in obs if r['transition']=='EXPAND_to_REPAIR']);b=np.asarray([r['gapRatio'] for r in obs if r['transition']=='REPAIR_to_EXPAND'])
    if len(a) and len(b):
        vals=np.concatenate([a,b]);order=np.argsort(vals,kind='mergesort');ranks=np.empty(len(vals),float);ranks[order]=np.arange(1,len(vals)+1);rb=ranks[len(a):].sum();auc=(rb-len(b)*(len(b)+1)/2)/(len(a)*len(b));out['gapRatioDirectionAuc_RtoE_high']=float(auc)
    else:out['gapRatioDirectionAuc_RtoE_high']=None
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    res={'version':'TARGET_BTC_ETH_PHASE_HYSTERESIS_V17','researchOnly':True,'boundary':['Target-only Maker parent actual-filled sequence','No numeric threshold copied into OUR','Tests whether E->R and R->E switches occur in measurably different normalized inventory regions','Chronology early/late stability and immediate switchback rates included'],'assets':{}}
    for asset in ('BTC','ETH'):
        obs=build(a.db,asset);ends=sorted(set(r['end'] for r in obs if r['end'] is not None));cut=ends[int(len(ends)*.70)] if ends else None
        early=[r for r in obs if r['end'] is not None and r['end']<cut];late=[r for r in obs if r['end'] is not None and r['end']>=cut]
        res['assets'][asset]={'cutWindowEndMs':cut,'all':summarize(obs),'early':summarize(early),'late':summarize(late)}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps(res),flush=True)
if __name__=='__main__':main()
