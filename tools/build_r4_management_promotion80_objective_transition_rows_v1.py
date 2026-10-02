from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np, pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SPLIT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_objective_first_postfresh100_split_v1.json'
OFF=ROOT/'data/target_wallet_official_v1.db'
LIFE=ROOT/'data/wallet_maker_book_inference.db'
ROWS=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_promotion80_objective_transition_rows_v1.csv'
REPORT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_promotion80_objective_transition_rows_v1.json'
EPS=1e-9; H5=5000; H15=15000

def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{db.resolve().as_posix()}?mode=ro',uri=True); c.execute('pragma query_only=on'); x=pd.read_sql_query(sql,c,params=params); c.close(); return x

def main():
 split=json.loads(SPLIT.read_text(encoding='utf-8'))
 ids=[int(x) for x in split['untouchedPromotionValidationMarkets']]
 dev=set(int(x) for x in split['developmentBackfillMarkets'])
 if len(ids)!=80 or len(dev)!=80 or not set(ids).isdisjoint(dev): raise RuntimeError('invalid frozen split guard')
 ph=','.join('?'*len(ids))
 life=qdf(LIFE,f"select market_id,lower(order_hash) order_hash,target_side,placement_first_ms,last_target_ms,placement_allocated_shares,expected_parent_shares,confidence,placement_coverage,fill_allocation_coverage from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and last_target_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",ids)
 if set(life.market_id.astype(int).unique()) != set(ids): raise RuntimeError('not all promotion80 have high-confidence lifecycle rows')
 p=qdf(OFF,f"select market_id,parent_id,lower(order_hash) order_hash,side,average_price,shares,first_event_ms from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID' order by market_id,first_event_ms,parent_id",ids)
 p.market_id=p.market_id.astype(int); p.first_event_ms=p.first_event_ms.astype(np.int64)
 p=p.merge(life[['market_id','order_hash']].drop_duplicates(),on=['market_id','order_hash'],how='inner')
 mk=qdf(OFF,f"select market_id,window_end_ms from target_markets where market_id in ({ph})",ids); wend=dict(zip(mk.market_id.astype(int),mk.window_end_ms))
 e=qdf(OFF,f"select market_id,lower(order_hash) order_hash,role,side,event_ms,price,shares from wallet_shadow_target_events where market_id in ({ph}) and quote_type='BID' order by market_id,event_ms,id",ids)
 e.market_id=e.market_id.astype(int); e.event_ms=e.event_ms.astype(np.int64)
 eg={int(k):g.sort_values('event_ms').copy() for k,g in e.groupby('market_id')}; lg={int(k):g.copy() for k,g in life.groupby('market_id')}
 raw=[]
 for mid,gp in p.groupby('market_id'):
  ge=eg.get(int(mid)); gl=lg.get(int(mid))
  if ge is None or gl is None: continue
  times=ge.event_ms.to_numpy(np.int64); sides=ge.side.astype(str).to_numpy(); sh=ge.shares.fillna(0).to_numpy(float); px=ge.price.fillna(0).to_numpy(float)
  up=np.cumsum(np.where(sides=='UP',sh,0.)); dn=np.cumsum(np.where(sides=='DOWN',sh,0.)); cost=np.cumsum(sh*px)
  maker=ge[ge.role.astype(str).str.upper().eq('MAKER')].copy(); byhash={str(h):x.sort_values('event_ms') for h,x in maker.groupby('order_hash') if pd.notna(h)}
  for r in gp.sort_values(['first_event_ms','parent_id']).itertuples():
   t=int(r.first_event_ms); j=np.searchsorted(times,t,side='left')-1
   u=float(up[j]) if j>=0 else 0.; d=float(dn[j]) if j>=0 else 0.; c=float(cost[j]) if j>=0 else 0.; gap=u-d
   if abs(gap)<=EPS: continue
   dom='UP' if gap>0 else 'DOWN'; weak='DOWN' if dom=='UP' else 'UP'; mode='ADD' if str(r.side)==dom else 'REPAIR'; family='STATE_SHAPING' if mode=='ADD' else 'PAIR_BALANCE'; gross=u+d
   floor=min(u,d)-c; upside=max(u,d)-c; absnet=abs(gap); coverage=2*min(u,d)/gross if gross>EPS else 0.; sec=float(wend.get(int(mid))-t)/1000 if pd.notna(wend.get(int(mid),np.nan)) else np.nan
   a=gl[(gl.placement_first_ms<t)&(gl.last_target_ms>=t)&(gl.order_hash.astype(str)!=str(r.order_hash))].copy(); wa=a[a.target_side.astype(str)==weak]; da=a[a.target_side.astype(str)==dom]
   def prog(z):
    commit=real=recent=0.
    for q in z.itertuples():
     cq=float(q.placement_allocated_shares) if pd.notna(q.placement_allocated_shares) and float(q.placement_allocated_shares)>EPS else float(q.expected_parent_shares or 0); x=byhash.get(str(q.order_hash)); rr=rc=0.
     if x is not None:
      tt=x.event_ms.to_numpy(np.int64); qq=x.shares.fillna(0).to_numpy(float); k=np.searchsorted(tt,t,'left'); lo=np.searchsorted(tt,t-H5,'left'); rr=float(qq[:k].sum()); rc=float(qq[lo:k].sum())
     commit+=cq; real+=min(rr,cq); recent+=rc
    return int(len(z)),float(max(0.,commit-real)),float(real/commit) if commit>EPS else 0.,float(recent)
   wr,wu,wp,wf=prog(wa); dr,du,dp,df=prog(da)
   cur=gl[gl.order_hash.astype(str)==str(r.order_hash)]; cur_commit=float(cur.placement_allocated_shares.fillna(0).max()) if len(cur) else 0.
   if cur_commit<=EPS and len(cur): cur_commit=float(cur.expected_parent_shares.fillna(0).max())
   raw.append({'market_id':int(mid),'t':t,'parent_id':str(r.parent_id),'order_hash':str(r.order_hash),'side':str(r.side),'shares':float(r.shares or 0),'price':float(r.average_price or 0),'mode':mode,'objective_family':family,'objective_key':family+'|'+str(r.side),'weak_side':weak,'dominant_side':dom,'seconds_left':sec,'abs_gap':absnet,'risk_deficit':max(0.,-floor),'floor':floor,'upside':upside,'absNet':absnet,'coverage':coverage,'floor_per_gross':floor/gross if gross>EPS else 0.,'weak_active_roots':wr,'dominant_active_roots':dr,'weak_unresolved_shares':wu,'dominant_unresolved_shares':du,'weak_progress_ratio':wp,'dominant_progress_ratio':dp,'weak_fill_shares_5s':wf,'dominant_fill_shares_5s':df,'current_commitment':cur_commit})
 d=pd.DataFrame(raw).sort_values(['market_id','t','parent_id']).reset_index(drop=True)
 mem=[]
 for mid,g in d.groupby('market_id'):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True); ts=g.t.to_numpy(np.int64); fam=g.objective_family.astype(str).to_numpy(); keys=g.objective_key.astype(str).to_numpy()
  for i,r in g.iterrows():
   t=int(r.t); prior=np.where(ts<t)[0]; p5=np.where((ts<t)&(ts>=t-H5))[0]; p15=np.where((ts<t)&(ts>=t-H15))[0]; prev=int(prior[-1]) if len(prior) else None; trans=0
   if len(p15)>1:
    x=fam[p15]; trans=int(np.sum(x[1:]!=x[:-1]))
   age=0.
   if prev is not None:
    k=prev; start=ts[k]; fm=fam[k]
    while k>0 and fam[k-1]==fm and ts[k]-ts[k-1]<=H15: start=ts[k-1]; k-=1
    age=float((t-start)/1000.)
   z=r.to_dict(); z.update({'seconds_since_prev_parent':float((t-ts[prev])/1000.) if prev is not None else 999.,'events_5s':int(len(p5)),'events_15s':int(len(p15)),'transitions_15s':trans,'mode_age_s':age,'recent_pair_balance_events_15s':int(np.sum(fam[p15]=='PAIR_BALANCE')) if len(p15) else 0,'recent_state_shaping_events_15s':int(np.sum(fam[p15]=='STATE_SHAPING')) if len(p15) else 0,'distinct_objective_keys_15s':int(len(set(keys[p15]))) if len(p15) else 0}); mem.append(z)
 d=pd.DataFrame(mem).sort_values(['market_id','t','parent_id']).reset_index(drop=True); d.to_csv(ROWS,index=False)
 rep={'version':'R4_MANAGEMENT_PROMOTION80_OBJECTIVE_TRANSITION_ROWS_V1','researchOnly':True,'marketsRequested':80,'marketsMaterialized':int(d.market_id.nunique()),'rows':int(len(d)),'highConfidenceLifecycleRows':int(len(life)),'promotionValidationMarketsAccessed':True,'guard':'Frozen untouchedPromotionValidationMarkets were queried/materialized only after Student V1 promotion contract freeze. No model/feature/threshold changes are allowed after this access.'}
 REPORT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
