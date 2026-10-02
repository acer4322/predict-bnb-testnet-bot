from __future__ import annotations
import sqlite3,json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_resting_responsibility_coverage_pilot_v1.json'
def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.execute('pragma query_only=on');d=pd.read_sql_query(sql,c,params=params);c.close();return d
def main():
 life_db='data/wallet_maker_book_inference.db';off_db='data/target_wallet_official_v1.db'
 mids=qdf(life_db,"select distinct market_id from maker_book_inference_v21_parent_lifecycles where placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by market_id desc limit 300").market_id.astype(int).tolist();ph=','.join('?'*len(mids))
 l=qdf(life_db,f"select market_id,order_hash,target_side,placement_first_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 p=qdf(off_db,f"select market_id,order_hash,side,first_event_ms,shares from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID'",mids).merge(l[['market_id','order_hash']],on=['market_id','order_hash'],how='inner')
 matched=sorted(set(p.market_id.astype(int)));ph2=','.join('?'*len(matched));e=qdf(off_db,f"select market_id,role,side,event_ms,shares from wallet_shadow_target_events where market_id in ({ph2}) and quote_type='BID' order by market_id,event_ms,id",matched)
 egrp={int(k):g.sort_values('event_ms') for k,g in e.groupby('market_id')};lgrp={int(k):g for k,g in l[l.market_id.isin(matched)].groupby('market_id')};rows=[]
 for mid,gp in p.groupby('market_id'):
  ge=egrp.get(int(mid));gl=lgrp.get(int(mid));
  if ge is None or gl is None:continue
  times=ge.event_ms.to_numpy(np.int64);sides=ge.side.astype(str).to_numpy();sh=ge.shares.to_numpy(float);up=np.cumsum(np.where(sides=='UP',sh,0.));dn=np.cumsum(np.where(sides=='DOWN',sh,0.))
  for r in gp.sort_values('first_event_ms').itertuples():
   t=int(r.first_event_ms);j=np.searchsorted(times,t,side='left')-1;u=float(up[j]) if j>=0 else 0.;d=float(dn[j]) if j>=0 else 0.;gap=u-d
   if abs(gap)<1e-9:continue
   dom='UP' if gap>0 else 'DOWN';weak='DOWN' if dom=='UP' else 'UP'; mode='ADD' if str(r.side)==dom else 'REPAIR'
   a=gl[(gl.placement_first_ms<t)&(gl.last_target_ms>=t)&(gl.order_hash.astype(str)!=str(r.order_hash))]
   weak_active=int((a.target_side.astype(str)==weak).sum());dom_active=int((a.target_side.astype(str)==dom).sum());
   rows.append({'market_id':int(mid),'t':t,'mode':mode,'repair':int(mode=='REPAIR'),'abs_gap':abs(gap),'weak_active':weak_active,'dom_active':dom_active,'any_weak_active':int(weak_active>0),'any_dom_active':int(dom_active>0),'active_total':int(len(a))})
 d=pd.DataFrame(rows);d['weak_bin']=pd.cut(d.weak_active,[-1,0,1,999],labels=['0','1','2+']);d['gap_q']=pd.qcut(d.abs_gap.rank(method='first'),4,labels=['Q1','Q2','Q3','Q4'])
 by=[]
 for k,g in d.groupby('weak_bin',observed=True):by.append({'weakOwners':str(k),'n':int(len(g)),'markets':int(g.market_id.nunique()),'repairRate':float(g.repair.mean()),'medianGap':float(g.abs_gap.median()),'meanDominantOwners':float(g.dom_active.mean())})
 strat=[]
 for (q,b),g in d.groupby(['gap_q','weak_bin'],observed=True):
  if len(g)>=50:strat.append({'gapQ':str(q),'weakOwners':str(b),'n':int(len(g)),'repairRate':float(g.repair.mean())})
 # dominant-owner cross table
 cross=[]
 d['dom_any']=np.where(d.dom_active>0,'DOM_ACTIVE','NO_DOM')
 for (w,da),g in d.groupby(['weak_bin','dom_any'],observed=True):
  if len(g)>=50:cross.append({'weakOwners':str(w),'dominantCoverage':str(da),'n':int(len(g)),'repairRate':float(g.repair.mean())})
 art={'version':'R4_TARGET_RESTING_RESPONSIBILITY_COVERAGE_PILOT_V1','researchOnly':True,'actionAuthority':False,'question':'Does strict-past side-specific resting responsibility coverage change whether the next Target Maker parent expresses REPAIR/BUILD versus ADD, beyond total multi-active count?','coverage':{'markets':int(d.market_id.nunique()),'parents':int(len(d)),'repairRate':float(d.repair.mean())},'byWeakRestingOwners':by,'gapStratified':strat,'coverageCross':cross,'guards':['Current parent excluded from resting-owner counts.','Resting owner is inferred parent lifecycle proxy, not private order ID.','Combined pre-parent inventory uses strict-past MAKER+TAKER BID fills.','No winner/future/action promotion.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'byWeakRestingOwners':by,'gapStratified':strat,'coverageCross':cross},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
