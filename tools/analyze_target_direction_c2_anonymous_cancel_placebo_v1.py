from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd

LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
CAN=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
OLD=LANE/'unseen_older173_v1'
CAN_EP=LANE/'C2_ALIGNED_RENEWED_RISK_FULL120_V4.csv'
OLD_EP=LANE/'UNSEEN_OLDER173_SERVICE_REPLICATION_V1.csv'
OUT=LANE/'C2_ANONYMOUS_CANCEL_PLACEBO_V1.json'
OUTCSV=LANE/'C2_ANONYMOUS_CANCEL_PLACEBO_V1.csv'
SEED=20260907


def strict_before(cp, mid, t):
    z=cp.get(int(mid))
    if z is None:return None
    q=z[z.checkpoint_ms < t].tail(1)
    if not len(q):return None
    r=q.iloc[0]
    age=t-r.checkpoint_ms
    return r if 0 < age <= 2000 else None

def match_hq(c,p):
    # Conservative exclusion: same market/side and placement within 250ms and price within 1 tick.
    z=p.get(int(c.market_id))
    if z is None:return False
    q=z[(z.target_side==c.target_side)&((z.placement_first_ms-c.placement_source_ms).abs()<=250)&((z.target_price-c.target_price).abs()<=0.0100001)]
    return len(q)>0

def pred_candidate(z,cur):
    # Candidate-level predecessor: prior candidate replacement points to current native price.
    q=z[(z.target_side==cur.target_side)&(z.placement_source_ms<cur.placement_source_ms)&z.post_action.isin(['SAME_PRICE_REFRESH','REPRICE_1_3_TICKS'])&z.post_action_native_price.notna()].copy()
    if not len(q):return None
    q=q[(q.post_action_native_price-cur.native_price).abs()<=0.0100001]
    q=q[(cur.placement_source_ms-q.placement_source_ms)<=5000]
    if not len(q):return None
    return q.sort_values('placement_source_ms').iloc[-1]
def trace_pre(z,cur,entry,max_depth=8):
    node=cur; seen=set()
    for _ in range(max_depth):
        p=pred_candidate(z,node)
        if p is None:return False
        key=str(p.candidate_id)
        if key in seen:return False
        seen.add(key)
        if p.placement_source_ms < entry:return True
        node=p
    return False

def load_cohort(name,root,epfile):
    ep=pd.read_csv(epfile,low_memory=False)
    # canonical V4 already contains net_aligned; older replication CSV is C2-aligned construction output.
    if 'net_aligned_prior' in ep.columns: ep=ep[ep.net_aligned_prior==True].copy()
    if 'full5s_observed' in ep.columns: ep=ep[ep.full5s_observed==True].copy()
    # older replication has exact primary C2-aligned rows; deduplicate by key.
    ep=ep.drop_duplicates(['market_id','entry_ms']).copy()
    c=pd.read_csv(root/'27_TARGET_BTC_MAKER_CANCEL_DIAGNOSTICS_RETROSPECTIVE_V1.csv',low_memory=False)
    p=pd.read_csv(root/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv',low_memory=False)
    cp=pd.read_csv(root/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',low_memory=False)
    # same rough size/confidence band used by prior time-layer secondary sample.
    c=c[(c.allocated_quantity.between(15.3,20.7))&(c.confidence>=.70)&c.placement_source_ms.notna()].copy()
    p=p[(p.placement_coverage>=.85)&(p.fill_allocation_coverage>=.70)&p.placement_first_ms.notna()].copy()
    byc={int(m):z.sort_values('placement_source_ms') for m,z in c.groupby('market_id',sort=False)}
    byp={int(m):z.sort_values('placement_first_ms') for m,z in p.groupby('market_id',sort=False)}
    bycp={int(m):z.sort_values('checkpoint_ms') for m,z in cp.groupby('market_id',sort=False)}
    rows=[]
    for r in ep.itertuples():
        z=byc.get(int(r.market_id))
        if z is None:continue
        q=z[(z.placement_source_ms>r.entry_ms)&(z.placement_source_ms<=r.entry_ms+5000)].copy()
        if not len(q):continue
        q=q.sort_values('placement_source_ms')
        for _,x in q.iterrows():
            if match_hq(x,byp):continue
            if trace_pre(z,x,int(r.entry_ms)):continue
            s=strict_before(bycp,int(r.market_id),int(x.placement_source_ms))
            if s is None:continue
            anchor=str(r.anchor); sg=1. if anchor=='UP' else -1.
            rows.append({'cohort':name,'market_id':int(r.market_id),'entry_ms':int(r.entry_ms),'anchor':anchor,
                         'candidate_id':str(x.candidate_id),'placement_ms':int(x.placement_source_ms),'candidate_side':str(x.target_side),
                         'same_side':int(str(x.target_side)==anchor),'target_price':float(x.target_price),'native_price':float(x.native_price),
                         'allocated_quantity':float(x.allocated_quantity),'resting_ms':float(x.resting_ms),
                         'post_action':str(x.post_action),'o_spot_q':sg*float(s.spot_queue_imbalance),'o_fut_q':sg*float(s.futures_queue_imbalance),
                         'snapshot_age_ms':int(x.placement_source_ms-s.checkpoint_ms)})
            break # first fresh anonymous candidate only
    return pd.DataFrame(rows), {'episodes':len(ep),'candidateRows':len(c),'hqParents':len(p)}

def cluster_diff(d,col='same_side'):
    # Compare both queues adverse vs other for side rate; market-cluster bootstrap.
    z=d.copy();z['both_adverse']=(z.o_spot_q<0)&(z.o_fut_q<0)
    a=z[z.both_adverse];b=z[~z.both_adverse]
    diff=float(a[col].mean()-b[col].mean()) if len(a) and len(b) else None
    mids=np.array(sorted(z.market_id.unique()));rng=np.random.default_rng(SEED);vals=[]
    for _ in range(10000):
        sm=rng.choice(mids,size=len(mids),replace=True);parts=[]
        for m in sm:
            parts.append(z[z.market_id==m])
        q=pd.concat(parts,ignore_index=True);aa=q[q.both_adverse];bb=q[~q.both_adverse]
        if len(aa) and len(bb):vals.append(float(aa[col].mean()-bb[col].mean()))
    return {'rows':len(z),'markets':int(z.market_id.nunique()),'bothAdverseN':len(a),'otherN':len(b),'bothAdverseSameRate':float(a[col].mean()) if len(a) else None,'otherSameRate':float(b[col].mean()) if len(b) else None,'difference':diff,'ci95':[float(x) for x in np.quantile(vals,[.025,.975])] if vals else None}
def quartiles(d,col):
    q=np.unique(np.nanquantile(d[col],[0,.25,.5,.75,1]));out=[]
    if len(q)<3:return out
    t=d.copy();t['bin']=pd.cut(t[col],q,include_lowest=True,duplicates='drop')
    for k,g in t.groupby('bin',observed=True):out.append({'bin':str(k),'n':len(g),'markets':int(g.market_id.nunique()),'medianQueue':float(g[col].median()),'sameRate':float(g.same_side.mean())})
    return out

def main():
    dc,mc=load_cohort('CANONICAL120',CAN,CAN_EP)
    do,mo=load_cohort('OLDER173',OLD,OLD_EP)
    d=pd.concat([dc,do],ignore_index=True);d.to_csv(OUTCSV,index=False)
    out={'version':'OUR_C2_ANONYMOUS_CANCEL_PLACEBO_V1','status':'OWNERSHIP_WEAK_PLACEBO_ONLY','sourceMeta':{'CANONICAL120':mc,'OLDER173':mo},'summary':{}}
    for name,z in [('CANONICAL120',dc),('OLDER173',do),('ALL',d)]:
        out['summary'][name]={'rows':len(z),'markets':int(z.market_id.nunique()) if len(z) else 0,'sameRate':float(z.same_side.mean()) if len(z) else None,
                             'bothQueueAdverse':cluster_diff(z) if len(z) else None,'spotQuartiles':quartiles(z,'o_spot_q') if len(z) else [],'futuresQuartiles':quartiles(z,'o_fut_q') if len(z) else [],
                             'postActions':z.post_action.value_counts().to_dict() if len(z) else {}}
    out['guards']=['Anonymous cancel candidates are public-book inference and Target ownership is NOT proven.','Exclude candidate placements close to any HQ parent placement (±250ms, same side, <=1 tick) to reduce anchored-parent contamination.','Trace and exclude candidate reprice/refresh chains that reach pre-conflict placement.','Use first remaining 15.3–20.7-share confidence>=0.70 candidate in 5s only.','A replicated queue-side pattern here weakens Target-specific admission interpretation; absence here does not prove Target-specific intent because ownership/noise differ.']
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
