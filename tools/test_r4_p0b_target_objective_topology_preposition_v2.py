from pathlib import Path
import sqlite3,json,numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1];R=ROOT/'data/research/r4_v0/p0_provenance_v1';D=R/'r4_p0b_target_objective_topology_rows_v2.csv';OUT=R/'r4_p0b_target_objective_topology_preposition_rows_v2.csv';SUM=R/'r4_p0b_target_objective_topology_preposition_structural_v2.json';H=15000;EPS=1e-9
d=pd.read_csv(D).sort_values(['market_id','t','parent_id']); pb=d[d.objective_family=='PAIR_BALANCE'].copy(); mids=tuple(sorted(d.market_id.astype(int).unique()));ph=','.join('?'*len(mids));c=sqlite3.connect(f'file:{(ROOT/"data/target_wallet_official_v1.db").as_posix()}?mode=ro',uri=True);c.execute('pragma query_only=on');e=pd.read_sql_query(f"select market_id,lower(order_hash) order_hash,event_ms,shares from wallet_shadow_target_events where market_id in ({ph}) and role='MAKER' and quote_type='BID' order by market_id,event_ms",c,params=mids);c.close();idx={}
for (m,h),g in e.groupby(['market_id','order_hash']):
 g=g.sort_values('event_ms');idx[(int(m),str(h))]=(g.event_ms.to_numpy(np.int64),np.cumsum(g.shares.fillna(0).to_numpy(float)))
def conf(m,h,t):
 z=idx.get((int(m),str(h)));
 if z is None:return 0.
 ts,cs=z;k=np.searchsorted(ts,int(t),'left');return float(cs[k-1]) if k else 0.
out=[]
for mid,g in d.groupby('market_id',sort=False):
 g=g.sort_values(['t','parent_id'])
 for _,cur in g[g.objective_family=='PAIR_BALANCE'].iterrows():
  pri=g[(g.t<cur.t)&(g.t>=cur.t-H)&(g.objective_key==cur.objective_key)].drop_duplicates('order_hash');paid=0.;roots=0
  for _,pr in pri.iterrows():
   q=conf(mid,pr.order_hash,cur.t);paid+=q;roots+=int(q>EPS)
  z=cur.to_dict();z.update({'prior_same_objective_roots_15s':int(len(pri)),'prior_same_objective_paid_roots':roots,'prior_same_objective_confirmed_qty':paid,'preposition_like_same_objective':int(paid>EPS),'same_objective_live_overlap':int(cur.weak_active_roots>0),'same_objective_reserved_total':float(cur.current_commitment+cur.weak_unresolved_shares),'reservation_over_current_deficit':float((cur.current_commitment+cur.weak_unresolved_shares)/max(cur.abs_gap,EPS)),'prior_paid_over_current_deficit':float(paid/max(cur.abs_gap,EPS))});out.append(z)
x=pd.DataFrame(out);x.to_csv(OUT,index=False);p=x[x.preposition_like_same_objective==1];s={'pairBalanceOpenings':int(len(x)),'prepositionLikeSameObjective':int(len(p)),'rate':float(len(p)/len(x)),'markets':int(p.market_id.nunique()),'liveOverlapAmongPrepositionRate':float(p.same_objective_live_overlap.mean()) if len(p) else None,'medianPriorConfirmedQty':float(p.prior_same_objective_confirmed_qty.median()) if len(p) else None,'medianPriorPaidOverCurrentDeficit':float(p.prior_paid_over_current_deficit.median()) if len(p) else None,'reservationOverCurrentDeficitRate':float((p.reservation_over_current_deficit>1+1e-9).mean()) if len(p) else None,'medianReservationOverCurrentDeficit':float(p.reservation_over_current_deficit.median()) if len(p) else None};SUM.write_text(json.dumps(s,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(s,ensure_ascii=False))