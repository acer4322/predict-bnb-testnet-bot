from __future__ import annotations
import sqlite3,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_cache_v1.json'
CSV=ROOT/'data/research/r4_v0/hourly/r4_management_cache_v1_rows.csv'
def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.execute('pragma query_only=on');d=pd.read_sql_query(sql,c,params=tuple(int(x) if isinstance(x,np.integer) else x for x in params));c.close();return d
def ordinary(ms): return datetime.fromtimestamp(ms/1000,timezone(timedelta(hours=8))).date().isoformat()!='2026-08-16'
def main():
 life_db='data/wallet_maker_book_inference.db';off_db='data/target_wallet_official_v1.db'
 mids=qdf(life_db,"select distinct market_id from maker_book_inference_v21_parent_lifecycles where placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by market_id desc limit 300").market_id.astype(int).tolist();ph=','.join('?'*len(mids))
 l=qdf(life_db,f"select market_id,order_hash,target_side,placement_first_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 p=qdf(off_db,f"select market_id,order_hash,side,first_event_ms,shares from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID'",mids).merge(l[['market_id','order_hash']],on=['market_id','order_hash'],how='inner')
 m=qdf(off_db,f"select market_id,window_end_ms from target_markets where market_id in ({ph})",mids);wend=dict(zip(m.market_id.astype(int),m.window_end_ms))
 matched=sorted(set(p.market_id.astype(int)));ph2=','.join('?'*len(matched));e=qdf(off_db,f"select market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where market_id in ({ph2}) and quote_type='BID' order by market_id,event_ms,id",matched)
 egrp={int(k):g.sort_values('event_ms') for k,g in e.groupby('market_id')};lgrp={int(k):g for k,g in l[l.market_id.isin(matched)].groupby('market_id')};rows=[]
 for mid,gp in p.groupby('market_id'):
  ge=egrp.get(int(mid));gl=lgrp.get(int(mid));
  if ge is None or gl is None:continue
  times=ge.event_ms.to_numpy(np.int64);sides=ge.side.astype(str).to_numpy();sh=ge.shares.to_numpy(float);px=ge.price.to_numpy(float);up=np.cumsum(np.where(sides=='UP',sh,0.));dn=np.cumsum(np.where(sides=='DOWN',sh,0.));cost=np.cumsum(sh*px)
  for r in gp.sort_values('first_event_ms').itertuples():
   t=int(r.first_event_ms)
   if not ordinary(t):continue
   j=np.searchsorted(times,t,side='left')-1;u=float(up[j]) if j>=0 else 0.;d=float(dn[j]) if j>=0 else 0.;c=float(cost[j]) if j>=0 else 0.;gap=u-d
   if abs(gap)<1e-9:continue
   dom='UP' if gap>0 else 'DOWN';weak='DOWN' if dom=='UP' else 'UP';mode='ADD' if str(r.side)==dom else 'REPAIR'
   a=gl[(gl.placement_first_ms<t)&(gl.last_target_ms>=t)&(gl.order_hash.astype(str)!=str(r.order_hash))].copy();wa=a[a.target_side.astype(str)==weak];da=a[a.target_side.astype(str)==dom]
   gross=u+d;floor=min(u,d)-c;sec=(float(wend.get(int(mid))-t)/1000 if pd.notna(wend.get(int(mid),np.nan)) else np.nan)
   rows.append({'market_id':int(mid),'t':t,'mode':mode,'weak_side':weak,'dominant_side':dom,'shares':float(r.shares),'seconds_left':sec,'abs_gap':abs(gap),'risk_deficit':max(0.,-floor),'coverage':(2*min(u,d)/gross if gross>0 else 0.),'weak_active_owners':int(len(wa)),'dominant_active_owners':int(len(da)),'weak_oldest_age_s':float((t-wa.placement_first_ms.min())/1000) if len(wa) else 0.,'dominant_oldest_age_s':float((t-da.placement_first_ms.min())/1000) if len(da) else 0.})
 d=pd.DataFrame(rows);ev=[]
 for (mid,t),g in d.groupby(['market_id','t'],sort=True):
  w=np.maximum(g.shares.to_numpy(float),1e-9);w=w/w.sum();repair=float(np.sum((g['mode'].to_numpy()=='REPAIR')*w));row={'market_id':int(mid),'t':int(t),'mode':'REPAIR' if repair>=.5 else 'ADD','weak_side':str(g.weak_side.iloc[0]),'dominant_side':str(g.dominant_side.iloc[0])}
  for c in ['seconds_left','abs_gap','risk_deficit','coverage','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']:row[c]=float(np.sum(g[c].to_numpy(float)*w))
  ev.append(row)
 x=pd.DataFrame(ev).sort_values(['market_id','t']).reset_index(drop=True);out=[]
 for mid,g in x.groupby('market_id',sort=False):
  g=g.sort_values('t').reset_index(drop=True);ts=g.t.to_numpy(np.int64);modes=g['mode'].astype(str).to_numpy();weak=g.weak_side.astype(str).to_numpy();run=0
  for i,r in g.iterrows():
   if i==0 or modes[i]!=modes[i-1]:run=i
   t=int(ts[i]);lo5=np.searchsorted(ts,t-5000,'left');lo15=np.searchsorted(ts,t-15000,'left');end=np.searchsorted(ts,t+5000,'right');trans=sum(1 for k in range(max(1,lo15),i+1) if modes[k]!=modes[k-1]);cont=0
   if modes[i]=='REPAIR':
    for k in range(i+1,end):
     if modes[k]=='REPAIR' and weak[k]==weak[i]:cont=1;break
   z=r.to_dict();z.update({'mode_age_s':float((t-ts[run])/1000),'events_5s':int(i-lo5+1),'events_15s':int(i-lo15+1),'transitions_15s':int(trans),'continue_weak_5s':int(cont)});out.append(z)
 z=pd.DataFrame(out);z.to_csv(CSV,index=False)
 art={'version':'R4_MANAGEMENT_CACHE_V1','researchOnly':True,'coverage':{'selectedMarkets':len(mids),'matchedMarkets':len(matched),'eventMarkets':int(z.market_id.nunique()),'rows':int(len(z)),'repairRows':int((z['mode']=='REPAIR').sum())},'columns':z.columns.tolist(),'guards':['Combined strict-past MAKER+TAKER inventory.','Current parent excluded from prior owner counts.','2026-08-16 Asia/Taipei excluded.','Future continuation is label only.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'rowsArtifact':str(CSV.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
