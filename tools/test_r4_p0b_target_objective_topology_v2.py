from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
FULL300=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
JOINT=ROOT/'data/research/r4_v0/hourly/r4_joint_base_upside_manager_v1_rows.csv'
OFF=ROOT/'data/target_wallet_official_v1.db'
LIFE=ROOT/'data/wallet_maker_book_inference.db'
PREREG=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_preregistered_v2.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_v2.json'
ROWS=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_rows_v2.csv'
EPS=1e-9;H5=5000;H15=15000

GEOM=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross']
MEM=['seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s']
ROLEMEM=['recent_pair_balance_events_15s','recent_state_shaping_events_15s','distinct_objective_keys_15s']
OWNER=['weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
CURR=['current_pair_balance','current_state_shaping']


def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True);c.execute('pragma query_only=on');x=pd.read_sql_query(sql,c,params=params);c.close();return x

def ordinary(ms):return dt.datetime.fromtimestamp(int(ms)/1000,ZoneInfo('Asia/Taipei')).date().isoformat()!='2026-08-16'

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);o={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None,'meanProbability':float(p.mean()) if len(p) else None}
 if len(y) and len(np.unique(y))>1:o.update({'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))})
 else:o.update({'auc':None,'ap':None,'logLoss':None})
 return o

def model(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=220,random_state=seed)

def block_summary(blocks,name):
 x=[b[name] for b in blocks if b.get(name,{}).get('auc') is not None]
 if not x:return {'blocks':0}
 return {'blocks':len(x),'meanAuc':float(np.mean([z['auc'] for z in x])),'worstAuc':float(np.min([z['auc'] for z in x])),'meanAp':float(np.mean([z['ap'] for z in x])),'meanLogLoss':float(np.mean([z['logLoss'] for z in x])),'blockAucs':[float(z['auc']) for z in x]}

def chronological(df,label,sets,seed=37000):
 x=df.dropna(subset=[label]).copy();x[label]=x[label].astype(int)
 order=x.groupby('market_id').t.min().sort_values().index.astype(int).tolist()
 if len(order)<12 or x[label].nunique()<2:return {'coverage':{'rows':int(len(x)),'markets':len(order),'rate':float(x[label].mean()) if len(x) else None},'blocks':[],'summary':{}}
 initial=max(40,int(len(order)*.60));initial=min(initial,len(order)-4);rem=len(order)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  if sz<=0:continue
  trm=set(order[:cur]);tem=set(order[cur:cur+sz]);cur+=sz;tr=x[x.market_id.isin(trm)];te=x[x.market_id.isin(tem)];b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':int(len(tr)),'testRows':int(len(te)),'testRate':float(te[label].mean()) if len(te) else None}
  for j,(name,feats) in enumerate(sets.items()):
   use=[c for c in feats if c in x.columns];a=tr.dropna(subset=use+[label]);z=te.dropna(subset=use+[label])
   if len(a)<100 or len(z)<20 or a[label].nunique()<2 or z[label].nunique()<2:b[name]={'n':int(len(z)),'rate':float(z[label].mean()) if len(z) else None,'auc':None,'ap':None,'logLoss':None};continue
   m=model(seed+bi*20+j).fit(a[use],a[label]);b[name]=met(z[label],m.predict_proba(z[use])[:,1])
  blocks.append(b)
 return {'coverage':{'rows':int(len(x)),'markets':len(order),'rate':float(x[label].mean())},'blocks':blocks,'summary':{k:block_summary(blocks,k) for k in sets}}

def psup(a,b):
 a=np.asarray(list(a),float);b=np.asarray(list(b),float);a=a[np.isfinite(a)];b=b[np.isfinite(b)]
 if not len(a) or not len(b):return None
 return float(np.mean(a[:,None]>b[None,:])+.5*np.mean(a[:,None]==b[None,:]))

def main():
 pre=json.loads(PREREG.read_text(encoding='utf-8'))
 src=pd.read_csv(FULL300);mids=tuple(sorted(src.market_id.astype(int).unique().tolist()));ph=','.join('?'*len(mids))
 life=qdf(LIFE,f"select market_id,lower(order_hash) order_hash,target_side,placement_first_ms,last_target_ms,placement_allocated_shares,expected_parent_shares,confidence,placement_coverage,fill_allocation_coverage from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 life.market_id=life.market_id.astype(int);life.placement_first_ms=life.placement_first_ms.astype(np.int64);life.last_target_ms=life.last_target_ms.astype(np.int64)
 p=qdf(OFF,f"select market_id,parent_id,lower(order_hash) order_hash,side,average_price,shares,first_event_ms from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID' order by market_id,first_event_ms,parent_id",mids)
 p.market_id=p.market_id.astype(int);p.first_event_ms=p.first_event_ms.astype(np.int64);p=p.merge(life[['market_id','order_hash']].drop_duplicates(),on=['market_id','order_hash'],how='inner')
 mkts=qdf(OFF,f"select market_id,window_end_ms from target_markets where market_id in ({ph})",mids);wend=dict(zip(mkts.market_id.astype(int),mkts.window_end_ms))
 matched=tuple(sorted(p.market_id.unique().tolist()));ph2=','.join('?'*len(matched))
 e=qdf(OFF,f"select market_id,lower(order_hash) order_hash,role,side,event_ms,price,shares from wallet_shadow_target_events where market_id in ({ph2}) and quote_type='BID' order by market_id,event_ms,id",matched)
 e.market_id=e.market_id.astype(int);e.event_ms=e.event_ms.astype(np.int64)
 eg={int(k):g.sort_values('event_ms').copy() for k,g in e.groupby('market_id')};lg={int(k):g.copy() for k,g in life.groupby('market_id')}
 raw=[];sealed_rows=0
 for mid,gp in p.groupby('market_id'):
  ge=eg.get(int(mid));gl=lg.get(int(mid));
  if ge is None or gl is None:continue
  times=ge.event_ms.to_numpy(np.int64);sides=ge.side.astype(str).to_numpy();sh=ge.shares.fillna(0).to_numpy(float);px=ge.price.fillna(0).to_numpy(float);up=np.cumsum(np.where(sides=='UP',sh,0.));dn=np.cumsum(np.where(sides=='DOWN',sh,0.));cost=np.cumsum(sh*px)
  maker=ge[ge.role.astype(str).str.upper().eq('MAKER')].copy();byhash={str(h):x.sort_values('event_ms') for h,x in maker.groupby('order_hash') if pd.notna(h)}
  for r in gp.sort_values(['first_event_ms','parent_id']).itertuples():
   t=int(r.first_event_ms)
   if not ordinary(t):sealed_rows+=1;continue
   j=np.searchsorted(times,t,side='left')-1;u=float(up[j]) if j>=0 else 0.;d=float(dn[j]) if j>=0 else 0.;c=float(cost[j]) if j>=0 else 0.;gap=u-d
   if abs(gap)<=EPS:continue
   dom='UP' if gap>0 else 'DOWN';weak='DOWN' if dom=='UP' else 'UP';mode='ADD' if str(r.side)==dom else 'REPAIR';family='STATE_SHAPING' if mode=='ADD' else 'PAIR_BALANCE';key=family+'|'+str(r.side);gross=u+d;floor=min(u,d)-c;upside=max(u,d)-c;absnet=abs(gap);coverage=2*min(u,d)/gross if gross>EPS else 0.;sec=float(wend.get(int(mid))-t)/1000 if pd.notna(wend.get(int(mid),np.nan)) else np.nan
   a=gl[(gl.placement_first_ms<t)&(gl.last_target_ms>=t)&(gl.order_hash.astype(str)!=str(r.order_hash))].copy();wa=a[a.target_side.astype(str)==weak];da=a[a.target_side.astype(str)==dom]
   def prog(z):
    commit=real=recent=0.
    for q in z.itertuples():
     cq=float(q.placement_allocated_shares) if pd.notna(q.placement_allocated_shares) and float(q.placement_allocated_shares)>EPS else float(q.expected_parent_shares or 0);x=byhash.get(str(q.order_hash));rr=rc=0.
     if x is not None:
      tt=x.event_ms.to_numpy(np.int64);qq=x.shares.fillna(0).to_numpy(float);k=np.searchsorted(tt,t,'left');lo=np.searchsorted(tt,t-H5,'left');rr=float(qq[:k].sum());rc=float(qq[lo:k].sum())
     commit+=cq;real+=min(rr,cq);recent+=rc
    return {'roots':int(len(z)),'unresolved':float(max(0.,commit-real)),'ratio':float(real/commit) if commit>EPS else 0.,'recent':float(recent)}
   wp=prog(wa);dp=prog(da)
   cur=gl[gl.order_hash.astype(str)==str(r.order_hash)];cur_commit=float(cur.placement_allocated_shares.fillna(0).max()) if len(cur) else 0.
   if cur_commit<=EPS and len(cur):cur_commit=float(cur.expected_parent_shares.fillna(0).max())
   raw.append({'market_id':int(mid),'t':t,'parent_id':str(r.parent_id),'order_hash':str(r.order_hash),'side':str(r.side),'shares':float(r.shares or 0),'price':float(r.average_price or 0),'mode':mode,'objective_family':family,'objective_key':key,'weak_side':weak,'dominant_side':dom,'seconds_left':sec,'abs_gap':absnet,'risk_deficit':max(0.,-floor),'floor':floor,'upside':upside,'absNet':absnet,'coverage':coverage,'floor_per_gross':floor/gross if gross>EPS else 0.,'weak_active_roots':wp['roots'],'dominant_active_roots':dp['roots'],'weak_unresolved_shares':wp['unresolved'],'dominant_unresolved_shares':dp['unresolved'],'weak_progress_ratio':wp['ratio'],'dominant_progress_ratio':dp['ratio'],'weak_fill_shares_5s':wp['recent'],'dominant_fill_shares_5s':dp['recent'],'current_commitment':cur_commit})
 d=pd.DataFrame(raw).sort_values(['market_id','t','parent_id']).reset_index(drop=True)
 # strict-past memory features; same timestamp siblings excluded.
 mem=[]
 for mid,g in d.groupby('market_id'):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True);ts=g.t.to_numpy(np.int64);fam=g.objective_family.astype(str).to_numpy();keys=g.objective_key.astype(str).to_numpy();modes=g['mode'].astype(str).to_numpy()
  for i,r in g.iterrows():
   t=int(r.t);prior=np.where(ts<t)[0];p5=np.where((ts<t)&(ts>=t-H5))[0];p15=np.where((ts<t)&(ts>=t-H15))[0];prev=int(prior[-1]) if len(prior) else None;trans=0
   if len(p15)>1:
    x=fam[p15];trans=int(np.sum(x[1:]!=x[:-1]))
   age=0.
   if prev is not None:
    k=prev;start=ts[k];fm=fam[k]
    while k>0 and fam[k-1]==fm and ts[k]-ts[k-1]<=H15:start=ts[k-1];k-=1
    age=float((t-start)/1000.)
   z=r.to_dict();z.update({'seconds_since_prev_parent':float((t-ts[prev])/1000.) if prev is not None else 999.,'events_5s':int(len(p5)),'events_15s':int(len(p15)),'transitions_15s':trans,'mode_age_s':age,'recent_pair_balance_events_15s':int(np.sum(fam[p15]=='PAIR_BALANCE')) if len(p15) else 0,'recent_state_shaping_events_15s':int(np.sum(fam[p15]=='STATE_SHAPING')) if len(p15) else 0,'distinct_objective_keys_15s':int(len(set(keys[p15]))) if len(p15) else 0,'current_pair_balance':int(r.objective_family=='PAIR_BALANCE'),'current_state_shaping':int(r.objective_family=='STATE_SHAPING')});mem.append(z)
 d=pd.DataFrame(mem).sort_values(['market_id','t','parent_id']).reset_index(drop=True);d.to_csv(ROWS,index=False)
 # event lookup for confirmed prior-root fill at later checkpoint; indexed once for efficiency.
 maker_fill_index={}
 maker=e[e.role.astype(str).str.upper().eq('MAKER')].copy()
 for (mid,h),x in maker.groupby(['market_id','order_hash'],sort=False):
  x=x.sort_values('event_ms');tt=x.event_ms.to_numpy(np.int64);cs=np.cumsum(x.shares.fillna(0).to_numpy(float));maker_fill_index[(int(mid),str(h))]=(tt,cs)
 def confirmed(mid,h,t):
  z=maker_fill_index.get((int(mid),str(h)))
  if z is None:return 0.
  tt,cs=z;k=np.searchsorted(tt,int(t),side='left')-1
  return float(cs[k]) if k>=0 else 0.
 # pair reconstruction
 pairs=[];sim=Counter()
 for mid,g in d.groupby('market_id'):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True);ts=g.t.to_numpy(np.int64)
  for i,cur in g.iterrows():
   same_t=g[(g.t==cur.t)&(g.index!=i)]
   for _,pr in same_t.iterrows():sim['same_objective' if pr.objective_key==cur.objective_key else 'different_objective']+=1
   pri=g[(g.t<cur.t)&(g.t>=cur.t-H15)]
   for _,pr in pri.iterrows():
    cq=confirmed(mid,pr.order_hash,int(cur.t));same=int(pr.objective_key==cur.objective_key);same_side=int(pr.side==cur.side);prior_active=int(((pr.side==cur.weak_side and cur.weak_active_roots>0) or (pr.side==cur.dominant_side and cur.dominant_active_roots>0)))
    pairs.append({'market_id':int(mid),'current_t':int(cur.t),'prior_t':int(pr.t),'current_key':cur.objective_key,'prior_key':pr.objective_key,'current_family':cur.objective_family,'prior_family':pr.objective_family,'current_side':cur.side,'prior_side':pr.side,'same_objective':same,'same_side':same_side,'prior_confirmed_before_current':cq,'prior_active_side_context':prior_active,'age_s':float((cur.t-pr.t)/1000.)})
 pairdf=pd.DataFrame(pairs)
 # preposition-like same-objective credit context on current PAIR_BALANCE roots.
 pre=[]
 for mid,g in d.groupby('market_id'):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True)
  for _,cur in g[g.objective_family=='PAIR_BALANCE'].iterrows():
   pri=g[(g.t<cur.t)&(g.t>=cur.t-H15)&(g.objective_key==cur.objective_key)];paid=0.;paid_roots=0
   for _,pr in pri.drop_duplicates('order_hash').iterrows():
    q=confirmed(mid,pr.order_hash,int(cur.t));paid+=q;paid_roots+=int(q>EPS)
   live_unresolved=float(cur.weak_unresolved_shares);reserved=float(cur.current_commitment+live_unresolved);pre.append({**cur.to_dict(),'prior_same_objective_roots_15s':int(pri.order_hash.nunique()),'prior_same_objective_paid_roots':paid_roots,'prior_same_objective_confirmed_qty':paid,'preposition_like_same_objective':int(paid>EPS),'same_objective_live_overlap':int(cur.weak_active_roots>0),'same_objective_reserved_total':reserved,'reservation_over_current_deficit':float(reserved/max(cur.abs_gap,EPS)),'prior_paid_over_current_deficit':float(paid/max(cur.abs_gap,EPS))})
 predf=pd.DataFrame(pre)
 # state-shaping parallel topology.
 state=[]
 for mid,g in d.groupby('market_id'):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True)
  for _,cur in g[g.objective_family=='STATE_SHAPING'].iterrows():
   pri=g[(g.t<cur.t)&(g.t>=cur.t-H15)&(g.objective_family=='PAIR_BALANCE')];state.append({**cur.to_dict(),'recent_pair_balance_roots_15s':int(pri.order_hash.nunique()),'state_shaping_recent_parallel':int(len(pri)>0),'state_shaping_live_parallel':int(cur.weak_active_roots>0)})
 statedf=pd.DataFrame(state)
 # objective group observation states
 lc=Counter();lm=defaultdict(set)
 for mid,g in d.groupby('market_id'):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True);open_last={}
  for _,r in g.iterrows():
   t=int(r.t);key=str(r.objective_key);same=key in open_last and t-open_last[key]<=H15;other=any(k!=key and t-v<=H15 for k,v in open_last.items())
   name='ACCUMULATE_SAME_OBJECTIVE' if same else 'OPEN_DIFFERENT_OBJECTIVE_PARALLEL' if other else 'OPEN_NEW_OBJECTIVE';lc[name]+=1;lm[name].add(int(mid));open_last[key]=t
  if len(g):
   end=int(g.t.max())
   for k,v in open_last.items():
    if end-v>H15:lc['OBSERVED_MEMORY_RETIRE']+=1;lm['OBSERVED_MEMORY_RETIRE'].add(int(mid))
 # future same-vs-different next-root topology teacher
 fut=[]
 for mid,g in d.groupby('market_id'):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True)
  for _,r in g.iterrows():
   z=g[(g.t>r.t)&(g.t<=r.t+H5)]
   if not len(z):continue
   same=bool((z.objective_key==r.objective_key).any());diff=bool((z.objective_key!=r.objective_key).any())
   if same==diff:continue
   q=r.to_dict();q['future_different_objective_5s']=int(diff);fut.append(q)
 futdf=pd.DataFrame(fut)
 sets={'GEOMETRY':GEOM,'GEOMETRY_MEMORY':GEOM+MEM,'GEOMETRY_MEMORY_OWNER':GEOM+MEM+OWNER,'FULL_WITH_OBJECTIVE_CONTEXT':GEOM+MEM+OWNER+ROLEMEM+CURR}
 future_model=chronological(futdf,'future_different_objective_5s',sets,38000)
 pre_model=chronological(predf,'preposition_like_same_objective',{'GEOMETRY':GEOM,'GEOMETRY_MEMORY':GEOM+MEM,'GEOMETRY_MEMORY_OWNER':GEOM+MEM+OWNER,'FULL_ROLE_MEMORY':GEOM+MEM+OWNER+ROLEMEM},39000)
 state_model=chronological(statedf,'state_shaping_live_parallel',{'GEOMETRY':GEOM,'GEOMETRY_MEMORY':GEOM+MEM,'GEOMETRY_ROLE_MEMORY':GEOM+MEM+ROLEMEM,'FULL_OWNER_PROGRESS':GEOM+MEM+ROLEMEM+OWNER},40000) if len(statedf) and statedf.state_shaping_live_parallel.nunique()>1 else {}
 # structural summaries
 same=int(pairdf.same_objective.sum()) if len(pairdf) else 0;diff=int((1-pairdf.same_objective).sum()) if len(pairdf) else 0
 pair_summary={'rows':int(len(pairdf)),'markets':int(pairdf.market_id.nunique()) if len(pairdf) else 0,'sameObjectivePairs':same,'differentObjectivePairs':diff,'sameSidePairs':int(pairdf.same_side.sum()) if len(pairdf) else 0,'sameSideSameObjective':int(((pairdf.same_side==1)&(pairdf.same_objective==1)).sum()) if len(pairdf) else 0,'sameSideDifferentObjective':int(((pairdf.same_side==1)&(pairdf.same_objective==0)).sum()) if len(pairdf) else 0,'sameTimestampDirectedPairs':int(sum(sim.values())),'sameTimestampSameObjective':int(sim['same_objective']),'sameTimestampDifferentObjective':int(sim['different_objective'])}
 pp=predf[predf.preposition_like_same_objective==1];no=predf[predf.preposition_like_same_objective==0]
 pre_summary={'pairBalanceOpenings':int(len(predf)),'prepositionLikeSameObjective':int(len(pp)),'rate':float(len(pp)/len(predf)) if len(predf) else None,'markets':int(pp.market_id.nunique()) if len(pp) else 0,'liveOverlapAmongPrepositionRate':float(pp.same_objective_live_overlap.mean()) if len(pp) else None,'medianPriorConfirmedQty':float(pp.prior_same_objective_confirmed_qty.median()) if len(pp) else None,'medianPriorPaidOverCurrentDeficit':float(pp.prior_paid_over_current_deficit.median()) if len(pp) else None,'reservationOverCurrentDeficitRate':float((pp.reservation_over_current_deficit>1+1e-9).mean()) if len(pp) else None,'medianReservationOverCurrentDeficit':float(pp.reservation_over_current_deficit.median()) if len(pp) else None}
 sp=statedf[statedf.state_shaping_recent_parallel==1];ss=statedf[statedf.state_shaping_recent_parallel==0]
 state_summary={'stateShapingOpenings':int(len(statedf)),'recentPairBalanceParallel':int(len(sp)),'recentParallelRate':float(len(sp)/len(statedf)) if len(statedf) else None,'recentParallelMarkets':int(sp.market_id.nunique()) if len(sp) else 0,'livePairBalanceParallel':int(statedf.state_shaping_live_parallel.sum()) if len(statedf) else 0,'liveParallelRate':float(statedf.state_shaping_live_parallel.mean()) if len(statedf) else None,'liveParallelMarkets':int(statedf.loc[statedf.state_shaping_live_parallel==1,'market_id'].nunique()) if len(statedf) else 0}
 anatomy={}
 for col in GEOM+MEM+['weak_unresolved_shares','weak_progress_ratio','weak_fill_shares_5s']:
  anatomy[col]={'parallelMedian':float(sp[col].median()) if len(sp) else None,'soloMedian':float(ss[col].median()) if len(ss) else None,'parallelGreaterProbability':psup(sp[col].dropna(),ss[col].dropna())}
 # objective-group root support by family/markets
 fam={str(k):{'rows':int(len(g)),'markets':int(g.market_id.nunique())} for k,g in d.groupby('objective_family')}
 # joint base/upside independent economic replication
 j=pd.read_csv(JOINT).replace([np.inf,-np.inf],np.nan);j.market_id=j.market_id.astype(int);j['flow_context']=np.select([(j.weak_maker_shares_15s>0)&(j.surplus_maker_shares_15s>0),(j.weak_maker_shares_15s>0)&(j.surplus_maker_shares_15s<=0),(j.weak_maker_shares_15s<=0)&(j.surplus_maker_shares_15s>0)],['DUAL_WEAK_SURPLUS','WEAK_ONLY','SURPLUS_ONLY'],default='NONE');jc={}
 for k,g in j.groupby('flow_context'):jc[str(k)]={'rows':int(len(g)),'markets':int(g.market_id.nunique()),'jointOutcomeRate':float(g.y_joint.mean()),'floorProgressRate':float(g.y_floor.mean()),'upsideProgressRate':float(g.y_upside.mean())}
 dual=jc.get('DUAL_WEAK_SURPLUS',{}).get('jointOutcomeRate');none=jc.get('NONE',{}).get('jointOutcomeRate');joint={'rows':int(len(j)),'markets':int(j.market_id.nunique()),'contexts':jc,'dualVsNoneJointRateRatio':float(dual/none) if dual is not None and none not in (None,0) else None}
 # feature-group impacts from narrow teachers
 def deltas(res):
  s=res.get('summary',{});keys=list(s)
  if not keys:return {}
  base=s[keys[0]];o={}
  for k in keys[1:]:
   q=s[k];o[k]={'meanAucDeltaVsGeometry':(q.get('meanAuc')-base.get('meanAuc')) if q.get('meanAuc') is not None and base.get('meanAuc') is not None else None,'worstAucDeltaVsGeometry':(q.get('worstAuc')-base.get('worstAuc')) if q.get('worstAuc') is not None and base.get('worstAuc') is not None else None,'logLossImprovementVsGeometry':(base.get('meanLogLoss')-q.get('meanLogLoss')) if q.get('meanLogLoss') is not None and base.get('meanLogLoss') is not None else None}
  return o
 # lane decision structural, no AUC threshold
 recurring_same=pre_summary['prepositionLikeSameObjective']>=20 and pair_summary['sameObjectivePairs']>=100
 recurring_diff=state_summary['recentPairBalanceParallel']>=20 and pair_summary['differentObjectivePairs']>=100
 side_insufficient=pair_summary['sameSideDifferentObjective']>0
 dual_support=jc.get('DUAL_WEAK_SURPLUS',{}).get('rows',0)>0
 status='KEEP' if recurring_same and recurring_diff and side_insufficient and dual_support else 'INCONCLUSIVE'
 rep={'version':'R4_P0B_TARGET_OBJECTIVE_TOPOLOGY_V2','lane':'E_TARGET_OBJECTIVE_TOPOLOGY','status':status,'researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'boundaryPreflight':'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_boundary_preflight_v1.json','coverage':{'frozenFull300MarketIds':len(mids),'matchedLifecycleMarkets':len(matched),'responsibilityRows':int(len(d)),'responsibilityMarkets':int(d.market_id.nunique()),'sealedRowsExcluded':int(sealed_rows),'familySupport':fam},'sameVsDifferentObjective':pair_summary,'prepositionLikeSameObjective':{'structural':pre_summary,'narrowTeacher':pre_model,'featureGroupDeltas':deltas(pre_model)},'stateShapingParallel':{'structural':state_summary,'strictPastAntecedentAnatomy':anatomy,'liveOverlapTeacher':state_model,'featureGroupDeltas':deltas(state_model)},'futureObjectiveTopology5s':{'narrowTeacher':future_model,'featureGroupDeltas':deltas(future_model)},'objectiveGroupLifecycleObservation':{'counts':{k:int(v) for k,v in lc.items()},'markets':{k:int(len(v)) for k,v in lm.items()},'interpretationGuard':'These are observed 15s-memory objective-group topology states, not evidence of a literal hidden Target objective_id implementation.'},'independentJointBaseUpsideReplication':joint,'decisionChecks':{'recurringSamePurpose':recurring_same,'recurringDifferentPurposeParallel':recurring_diff,'sameSideIdentityInsufficient':side_insufficient,'dualWeakSurplusEconomicSupport':dual_support},'mainObjectiveLedgerTransfer':{'groupKeyCandidate':'objective_family + side, above responsibility_id','pairBalanceCreditRuleCandidate':'Confirmed/remaining quantity from earlier same-objective PAIR_BALANCE roots belongs to one objective budget and must be credited/reserved at group level before admitting later same-purpose work.','stateShapingRuleCandidate':'STATE_SHAPING/ADD is a distinct objective family; do not consume its quantity using generic pair-balance credit merely because side matches after an orientation change.','requiredRuntimeInputs':['strict-past weak/dominant relation','portfolio deficit/absNet','responsibility family/purpose','active root ownership','confirmed and unresolved quantity','objective-group memory/age']},'guards':pre['guards'],'files':{'rows':str(ROWS.relative_to(ROOT)).replace('\\','/')}}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':rep['coverage'],'sameVsDifferent':pair_summary,'preposition':pre_summary,'stateParallel':state_summary,'groupLifecycle':rep['objectiveGroupLifecycleObservation'],'joint':joint,'teacherSummaries':{'preposition':pre_model.get('summary',{}),'state':state_model.get('summary',{}) if state_model else {},'future':future_model.get('summary',{})},'featureDeltas':{'preposition':deltas(pre_model),'state':deltas(state_model),'future':deltas(future_model)}},ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
