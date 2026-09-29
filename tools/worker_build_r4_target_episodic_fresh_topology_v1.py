from pathlib import Path
import argparse, json
import numpy as np
import pandas as pd

EPS=1e-9
H5=5000

def safe(v,default=0.0):
    try:
        x=float(v)
        return x if np.isfinite(x) else default
    except Exception:
        return default

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--data-dir',required=True)
    ap.add_argument('--out',required=True)
    args=ap.parse_args()
    D=Path(args.data_dir)
    life=pd.read_csv(D/'lifecycles.csv').replace([np.inf,-np.inf],np.nan)
    parents=pd.read_csv(D/'parents.csv').replace([np.inf,-np.inf],np.nan)
    events=pd.read_csv(D/'events.csv').replace([np.inf,-np.inf],np.nan)
    markets=pd.read_csv(D/'markets.csv')
    life['market_id']=life.market_id.astype(int)
    life['placement_first_ms']=life.placement_first_ms.astype(np.int64)
    life['last_target_ms']=life.last_target_ms.astype(np.int64)
    events['market_id']=events.market_id.astype(int)
    events['event_ms']=events.event_ms.astype(np.int64)
    events['role']=events.role.astype(str).str.upper()
    events['side']=events.side.astype(str)
    parents['market_id']=parents.market_id.astype(int)
    parents['first_event_ms']=parents.first_event_ms.astype(np.int64)
    wend={int(r.market_id):int(r.window_end_ms) for r in markets.itertuples()}

    maker=parents[(parents.role.astype(str).str.upper()=='MAKER') & (parents.quote_type.astype(str).str.upper()=='BID')].copy()
    maker['order_hash']=maker.order_hash.astype(str).str.lower()
    life['order_hash']=life.order_hash.astype(str).str.lower()
    maker=maker.merge(life[['market_id','order_hash']].drop_duplicates(),on=['market_id','order_hash'],how='inner')

    eg={int(m):g.sort_values('event_ms').reset_index(drop=True) for m,g in events.groupby('market_id')}
    lg={int(m):g.copy() for m,g in life.groupby('market_id')}
    raw=[]
    skipped_flat=0
    missing=0
    for mid,gp in maker.groupby('market_id',sort=False):
        ge=eg.get(int(mid)); gl=lg.get(int(mid))
        if ge is None or gl is None:
            missing+=1; continue
        times=ge.event_ms.to_numpy(np.int64)
        sides=ge.side.to_numpy()
        sh=ge.shares.fillna(0).to_numpy(float)
        px=ge.price.fillna(0).to_numpy(float)
        upcs=np.cumsum(np.where(sides=='UP',sh,0.0))
        dncs=np.cumsum(np.where(sides=='DOWN',sh,0.0))
        ccs=np.cumsum(sh*px)
        me=ge[ge.role.eq('MAKER')].copy()
        byhash={str(h).lower():x.sort_values('event_ms') for h,x in me.dropna(subset=['order_hash']).groupby(me.order_hash.astype(str).str.lower())}
        for r in gp.sort_values(['first_event_ms','parent_id']).itertuples(index=False):
            t=int(r.first_event_ms)
            k=np.searchsorted(times,t,'left')-1
            up=float(upcs[k]) if k>=0 else 0.0
            dn=float(dncs[k]) if k>=0 else 0.0
            cost=float(ccs[k]) if k>=0 else 0.0
            gap=up-dn
            if abs(gap)<=EPS:
                skipped_flat+=1; continue
            dom='UP' if gap>0 else 'DOWN'; weak='DOWN' if dom=='UP' else 'UP'
            side=str(r.side)
            fam='STATE_SHAPING' if side==dom else 'PAIR_BALANCE'
            gross=up+dn
            floor=min(up,dn)-cost
            upside=max(up,dn)-cost
            cov=2*min(up,dn)/gross if gross>EPS else 0.0
            sec=(wend[int(mid)]-t)/1000.0
            active=gl[(gl.placement_first_ms<t)&(gl.last_target_ms>=t)&(gl.order_hash.astype(str).str.lower()!=str(r.order_hash).lower())]
            def own(side_):
                z=active[active.target_side.astype(str)==side_]
                commit=real=recent=0.0
                for q in z.itertuples(index=False):
                    c=safe(q.placement_allocated_shares,safe(q.expected_parent_shares))
                    x=byhash.get(str(q.order_hash).lower())
                    rr=rc=0.0
                    if x is not None:
                        tt=x.event_ms.to_numpy(np.int64); qq=x.shares.fillna(0).to_numpy(float)
                        hi=np.searchsorted(tt,t,'left'); lo=np.searchsorted(tt,t-H5,'left')
                        rr=float(qq[:hi].sum()); rc=float(qq[lo:hi].sum())
                    commit+=max(0.0,c); real+=min(max(0.0,rr),max(0.0,c)); recent+=max(0.0,rc)
                return len(z),max(0.0,commit-real),(real/commit if commit>EPS else 0.0),recent
            wa,wu,wp,wf=own(weak); da,du,dp,df=own(dom)
            raw.append({
                'market_id':int(mid),'t':t,'parent_id':str(r.parent_id),'objective_family':fam,
                'shares':safe(r.shares),'price':safe(r.average_price),'seconds_left':sec,
                'abs_gap':abs(gap),'risk_deficit':max(0.0,-floor),'floor':floor,'upside':upside,
                'coverage':cov,'floor_per_gross':floor/gross if gross>EPS else 0.0,
                'weak_active_roots':wa,'dominant_active_roots':da,
                'weak_unresolved_shares':wu,'dominant_unresolved_shares':du,
                'weak_progress_ratio':wp,'dominant_progress_ratio':dp,
                'weak_fill_shares_5s':wf,'dominant_fill_shares_5s':df
            })
    d=pd.DataFrame(raw).sort_values(['market_id','t','parent_id']).reset_index(drop=True)
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); d.to_csv(out,index=False)
    rep={'version':'R4_TARGET_EPISODIC_FRESH_TOPOLOGY_V1','markets':int(d.market_id.nunique()),'rows':int(len(d)),'families':d.objective_family.value_counts().to_dict(),'skippedFlatMakerParents':int(skipped_flat),'missingMarkets':int(missing),'out':str(out)}
    print(json.dumps(rep,indent=2),flush=True)

if __name__=='__main__':
    main()
