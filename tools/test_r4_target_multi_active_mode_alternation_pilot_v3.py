from __future__ import annotations
import sqlite3,json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_multi_active_mode_alternation_pilot_v3.json'
def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.execute('pragma query_only=on');d=pd.read_sql_query(sql,c,params=params);c.close();return d
def main():
 life_db='data/wallet_maker_book_inference.db';off_db='data/target_wallet_official_v1.db'
 mids=qdf(life_db,"select distinct market_id from maker_book_inference_v21_parent_lifecycles where placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by market_id desc limit 300").market_id.astype(int).tolist();ph=','.join('?'*len(mids))
 l=qdf(life_db,f"select market_id,order_hash,placement_first_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 p=qdf(off_db,f"select market_id,order_hash,side,first_event_ms,shares from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID'",mids).merge(l,on=['market_id','order_hash'],how='inner')
 matched=sorted(set(p.market_id.astype(int))); ph2=','.join('?'*len(matched))
 # Combined realized inventory: MAKER + TAKER BID fills strict-past to parent first_event_ms.
 e=qdf(off_db,f"select market_id,role,side,event_ms,shares from wallet_shadow_target_events where market_id in ({ph2}) and quote_type='BID' order by market_id,event_ms,id",matched) if matched else pd.DataFrame()
 egrp={int(k):g.sort_values('event_ms') for k,g in e.groupby('market_id')};lgrp={int(k):g for k,g in l[l.market_id.isin(matched)].groupby('market_id')};rows=[]
 for mid,gp in p.groupby('market_id'):
  ge=egrp.get(int(mid));gl=lgrp.get(int(mid));
  if ge is None or gl is None:continue
  times=ge.event_ms.to_numpy(np.int64);sides=ge.side.astype(str).to_numpy();sh=ge.shares.to_numpy(float);up=np.cumsum(np.where(sides=='UP',sh,0.));dn=np.cumsum(np.where(sides=='DOWN',sh,0.));starts=gl.placement_first_ms.to_numpy(np.int64);ends=gl.last_target_ms.fillna(gl.placement_first_ms).to_numpy(np.int64)
  for r in gp.sort_values('first_event_ms').itertuples():
   t=int(r.first_event_ms);j=np.searchsorted(times,t,side='left')-1;u=float(up[j]) if j>=0 else 0.;d=float(dn[j]) if j>=0 else 0.;gap=u-d
   if abs(gap)<1e-9:continue
   dom='UP' if gap>0 else 'DOWN'; mode='ADD' if str(r.side)==dom else 'REPAIR'; active=int(np.sum((starts<=t)&(ends>=t))); rows.append({'market_id':int(mid),'t':t,'mode':mode,'active_count':active,'abs_gap':abs(gap),'shares':float(r.shares)})
 d=pd.DataFrame(rows);ev=[]
 for (mid,t),g in d.groupby(['market_id','t'],sort=True):
  w=np.maximum(g.shares.to_numpy(float),1e-9);add=float(np.sum(w*(g['mode'].to_numpy()=='ADD'))/np.sum(w));ev.append({'market_id':int(mid),'t':int(t),'mode':'ADD' if add>=.5 else 'REPAIR','active_count':int(g.active_count.max()),'abs_gap':float(np.average(g.abs_gap,weights=w))})
 x=pd.DataFrame(ev).sort_values(['market_id','t']);x['prev_mode']=x.groupby('market_id').mode.shift(1);x['prev_t']=x.groupby('market_id').t.shift(1);x=x[x.prev_mode.notna()].copy();x['alternates']=(x['mode']!=x.prev_mode).astype(int);x['dt_ms']=x.t-x.prev_t;x['active_bin']=pd.cut(x.active_count,[-1,1,2,999],labels=['0-1','2','3+'])
 by=[]
 for k,g in x.groupby('active_bin',observed=True):by.append({'active':str(k),'n':int(len(g)),'markets':int(g.market_id.nunique()),'alternationRate':float(g.alternates.mean()),'medianDtMs':float(g.dt_ms.median()),'medianAbsGap':float(g.abs_gap.median())})
 x['gap_q']=pd.qcut(x.abs_gap.rank(method='first'),4,labels=['Q1','Q2','Q3','Q4']);strat=[]
 for (qv,ab),g in x.groupby(['gap_q','active_bin'],observed=True):
  if len(g)>=30:strat.append({'gapQ':str(qv),'active':str(ab),'n':int(len(g)),'alternationRate':float(g.alternates.mean())})
 mm=[]
 for mid,g in x.groupby('market_id'):
  if len(g)>=5:mm.append({'market_id':int(mid),'multiShare':float((g.active_count>=2).mean()),'altRate':float(g.alternates.mean())})
 m=pd.DataFrame(mm);corr=float(m.multiShare.corr(m.altRate,method='spearman')) if len(m)>2 else None
 art={'version':'R4_TARGET_MULTI_ACTIVE_MODE_ALTERNATION_PILOT_V3','researchOnly':True,'actionAuthority':False,'coverage':{'selectedLifecycleMarkets':len(mids),'matchedMarkets':len(matched),'matchedParents':int(len(p)),'classifiedParents':int(len(d)),'transitionEligibleEvents':int(len(x)),'markets':int(x.market_id.nunique())},'byActiveCount':by,'gapStratified':strat,'marketLevel':{'markets':int(len(m)),'spearmanMultiActiveShareVsAlternationRate':corr},'guards':['Combined realized inventory uses strict-past MAKER+TAKER BID fills.','Active parent is inferred proxy, not private order ID.','Same-timestamp Maker parents collapsed.','No winner/future/action promotion.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'byActiveCount':by,'marketLevel':art['marketLevel'],'gapStratified':strat},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
