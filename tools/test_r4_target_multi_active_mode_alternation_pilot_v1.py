from __future__ import annotations
import sqlite3,json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_multi_active_mode_alternation_pilot_v1.json'
def main():
 c=sqlite3.connect(':memory:');c.execute("attach database 'data/wallet_maker_book_inference.db' as life");c.execute("attach database 'data/target_wallet_official_v1.db' as off")
 mids=[int(r[0]) for r in c.execute('''select distinct l.market_id from life.maker_book_inference_v21_parent_lifecycles l join off.target_parent_orders p on p.market_id=l.market_id and p.order_hash=l.order_hash where p.role='MAKER' and p.quote_type='BID' and l.placement_first_ms is not null and l.confidence>=.75 and l.placement_coverage>=.85 and l.fill_allocation_coverage>=.70 order by l.market_id desc limit 300''')]
 ph=','.join('?'*len(mids))
 p=pd.read_sql_query(f'''select p.market_id,p.order_hash,p.side,p.first_event_ms,p.shares,l.placement_first_ms,l.last_target_ms from off.target_parent_orders p join life.maker_book_inference_v21_parent_lifecycles l on p.market_id=l.market_id and p.order_hash=l.order_hash where p.market_id in ({ph}) and p.role='MAKER' and p.quote_type='BID' and l.placement_first_ms is not null and l.confidence>=.75 and l.placement_coverage>=.85 and l.fill_allocation_coverage>=.70 order by p.market_id,p.first_event_ms''',c,params=mids)
 e=pd.read_sql_query(f"select market_id,side,event_ms,shares from off.wallet_shadow_target_events where role='MAKER' and quote_type='BID' and market_id in ({ph}) order by market_id,event_ms,id",c,params=mids)
 l=pd.read_sql_query(f"select market_id,placement_first_ms,last_target_ms from life.maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",c,params=mids);c.close()
 egrp={int(k):g.sort_values('event_ms') for k,g in e.groupby('market_id')}; lgrp={int(k):g for k,g in l.groupby('market_id')};rows=[]
 for mid,gp in p.groupby('market_id'):
  ge=egrp.get(int(mid));gl=lgrp.get(int(mid));
  if ge is None or gl is None:continue
  times=ge.event_ms.to_numpy(np.int64);sides=ge.side.astype(str).to_numpy();sh=ge.shares.to_numpy(float);up=np.cumsum(np.where(sides=='UP',sh,0.));dn=np.cumsum(np.where(sides=='DOWN',sh,0.));starts=gl.placement_first_ms.to_numpy(np.int64);ends=gl.last_target_ms.fillna(gl.placement_first_ms).to_numpy(np.int64)
  for r in gp.itertuples():
   t=int(r.first_event_ms);j=np.searchsorted(times,t,side='left')-1;u=float(up[j]) if j>=0 else 0.;d=float(dn[j]) if j>=0 else 0.;gap=u-d
   if abs(gap)<1e-9:continue
   dom='UP' if gap>0 else 'DOWN';mode='ADD' if str(r.side)==dom else 'REPAIR';active=int(np.sum((starts<=t)&(ends>=t)));rows.append({'market_id':int(mid),'t':t,'mode':mode,'active_count':active,'abs_gap':abs(gap),'shares':float(r.shares)})
 d=pd.DataFrame(rows);ev=[]
 for (mid,t),g in d.groupby(['market_id','t'],sort=True):
  w=np.maximum(g.shares.to_numpy(float),1e-9); add=float(np.sum(w*(g.mode.to_numpy()=='ADD'))/np.sum(w));ev.append({'market_id':int(mid),'t':int(t),'mode':'ADD' if add>=.5 else 'REPAIR','active_count':int(g.active_count.max()),'abs_gap':float(np.average(g.abs_gap,weights=w))})
 x=pd.DataFrame(ev).sort_values(['market_id','t']);x['prev_mode']=x.groupby('market_id').mode.shift(1);x['prev_t']=x.groupby('market_id').t.shift(1);x=x[x.prev_mode.notna()].copy();x['alternates']=(x.mode!=x.prev_mode).astype(int);x['dt_ms']=x.t-x.prev_t;x['active_bin']=pd.cut(x.active_count,[-1,1,2,999],labels=['0-1','2','3+'])
 by=[]
 for k,g in x.groupby('active_bin',observed=True):by.append({'active':str(k),'n':int(len(g)),'markets':int(g.market_id.nunique()),'alternationRate':float(g.alternates.mean()),'medianDtMs':float(g.dt_ms.median()),'medianAbsGap':float(g.abs_gap.median())})
 x['gap_q']=pd.qcut(x.abs_gap.rank(method='first'),4,labels=['Q1','Q2','Q3','Q4']); strat=[]
 for (qv,ab),g in x.groupby(['gap_q','active_bin'],observed=True):
  if len(g)>=30:strat.append({'gapQ':str(qv),'active':str(ab),'n':int(len(g)),'alternationRate':float(g.alternates.mean())})
 mm=[]
 for mid,g in x.groupby('market_id'):
  if len(g)>=5:mm.append({'market_id':int(mid),'multiShare':float((g.active_count>=2).mean()),'altRate':float(g.alternates.mean())})
 m=pd.DataFrame(mm);corr=float(m.multiShare.corr(m.altRate,method='spearman')) if len(m)>2 else None
 art={'version':'R4_TARGET_MULTI_ACTIVE_MODE_ALTERNATION_PILOT_V1','researchOnly':True,'actionAuthority':False,'coverage':{'selectedMarkets':len(mids),'classifiedParents':int(len(d)),'transitionEligibleEvents':int(len(x)),'markets':int(x.market_id.nunique())},'byActiveCount':by,'gapStratified':strat,'marketLevel':{'markets':int(len(m)),'spearmanMultiActiveShareVsAlternationRate':corr},'guards':['Recent 300 overlapping markets fixed before outcome inspection.','Active parent is inferred proxy, not private order ID.','Same-timestamp parents collapsed.','Mode from strict-past official Maker inventory; no winner/future.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'byActiveCount':by,'marketLevel':art['marketLevel'],'gapStratified':strat},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
