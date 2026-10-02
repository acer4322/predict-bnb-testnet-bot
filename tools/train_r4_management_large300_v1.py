from __future__ import annotations
import json,sqlite3
from pathlib import Path
from datetime import datetime,timezone,timedelta
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1.json'
CACHE=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
PORT=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
FULL=PORT+RESP+MEM
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.execute('pragma query_only=on');d=pd.read_sql_query(sql,c,params=params);c.close();return d
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=35,l2_regularization=1,max_iter=220,random_state=seed)
def binmet(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def multimet(y,p):
 y=np.asarray(y);pred=np.asarray(CLASSES)[np.argmax(p,axis=1)];Y=label_binarize(y,classes=CLASSES);aps=[average_precision_score(Y[:,j],p[:,j]) for j in range(3)];auc=float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro'));rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0);return {'n':int(len(y)),'macroAuc':auc,'macroAp':float(np.mean(aps)),'logLoss':float(log_loss(y,p,labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}
def build_cache():
 life_db='data/wallet_maker_book_inference.db';off_db='data/target_wallet_official_v1.db'
 cand=qdf(life_db,"select distinct market_id from maker_book_inference_v21_parent_lifecycles where placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by market_id desc limit 420").market_id.astype(int).tolist();ph=','.join('?'*len(cand))
 life=qdf(life_db,f"select market_id,order_hash,target_side,placement_first_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",cand)
 po=qdf(off_db,f"select market_id,order_hash,side,first_event_ms,shares from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID'",cand)
 tm=qdf(off_db,f"select market_id,window_end_ms from target_markets where market_id in ({ph})",cand)
 # sealed local 2026-08-16 excluded by parent event timestamp.
 tz=timezone(timedelta(hours=8));seal0=int(datetime(2026,8,16,0,0,tzinfo=tz).timestamp()*1000);seal1=int(datetime(2026,8,17,0,0,tzinfo=tz).timestamp()*1000)
 first=po.groupby('market_id').first_event_ms.min();ok=[int(m) for m,t in first.items() if not (seal0<=int(t)<seal1)];ok=sorted(ok,reverse=True)[:300];okset=set(ok);life=life[life.market_id.isin(okset)].copy();po=po[po.market_id.isin(okset)].merge(life[['market_id','order_hash']].drop_duplicates(),on=['market_id','order_hash'],how='inner');tm=tm[tm.market_id.isin(okset)];ph2=','.join('?'*len(ok));ev=qdf(off_db,f"select market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where market_id in ({ph2}) and quote_type='BID' order by market_id,event_ms,id",ok)
 endmap=tm.set_index('market_id').window_end_ms.to_dict();egrp={int(k):g.sort_values('event_ms') for k,g in ev.groupby('market_id')};lgrp={int(k):g for k,g in life.groupby('market_id')};rows=[]
 for mid,gp in po.groupby('market_id'):
  ge=egrp.get(int(mid));gl=lgrp.get(int(mid));wend=endmap.get(int(mid));
  if ge is None or gl is None or pd.isna(wend):continue
  times=ge.event_ms.to_numpy(np.int64);sides=ge.side.astype(str).to_numpy();sh=ge.shares.to_numpy(float);pr=ge.price.to_numpy(float);up=np.cumsum(np.where(sides=='UP',sh,0.));dn=np.cumsum(np.where(sides=='DOWN',sh,0.));cost=np.cumsum(sh*pr);starts=gl.placement_first_ms.to_numpy(np.int64);ends=gl.last_target_ms.fillna(gl.placement_first_ms).to_numpy(np.int64);lside=gl.target_side.astype(str).to_numpy();lohash=gl.order_hash.astype(str).to_numpy()
  tmp=[]
  for r in gp.sort_values('first_event_ms').itertuples():
   t=int(r.first_event_ms);j=np.searchsorted(times,t,side='left')-1;u=float(up[j]) if j>=0 else 0.;d=float(dn[j]) if j>=0 else 0.;c=float(cost[j]) if j>=0 else 0.;gap=u-d
   if abs(gap)<1e-9:continue
   dom='UP' if gap>0 else 'DOWN';weak='DOWN' if dom=='UP' else 'UP';mode='REPAIR' if str(r.side)==weak else 'ADD';mask=(starts<t)&(ends>=t)&(lohash!=str(r.order_hash));wm=mask&(lside==weak);dm=mask&(lside==dom);wa=int(wm.sum());da=int(dm.sum());wage=float((t-starts[wm]).max()/1000.) if wa else 0.;dage=float((t-starts[dm]).max()/1000.) if da else 0.;gross=u+d;paired=min(u,d);coverage=float((2*paired/gross) if gross>0 else 0.);absratio=float(abs(gap)/gross) if gross>0 else 0.;floor=min(u-c,d-c);risk=max(0.,-floor);fpg=float(floor/gross) if gross>0 else 0.;tmp.append({'market_id':int(mid),'t':t,'mode':mode,'build_now':int(mode=='REPAIR'),'shares':float(r.shares),'seconds_left':max(0.,(float(wend)-t)/1000.),'abs_gap':abs(gap),'risk_deficit':risk,'coverage':coverage,'absnet_ratio':absratio,'floor_per_gross':fpg,'weak_active_owners':wa,'dominant_active_owners':da,'weak_oldest_age_s':wage,'dominant_oldest_age_s':dage})
  if not tmp:continue
  x=pd.DataFrame(tmp);agg=[]
  for t,g in x.groupby('t',sort=True):
   w=np.maximum(g.shares.to_numpy(float),1e-9);w=w/w.sum();r={'market_id':int(mid),'t':int(t),'build_now':int(np.sum(g.build_now.to_numpy()*w)>=.5)}
   for c in PORT+RESP: r[c]=float(np.sum(g[c].to_numpy(float)*w))
   agg.append(r)
  rows.extend(agg)
 d=pd.DataFrame(rows).sort_values(['market_id','t']).reset_index(drop=True)
 for c in MEM+['continue_weak_5s','management_label_5s']:d[c]=0 if c!='management_label_5s' else ''
 for mid,idx in d.groupby('market_id').groups.items():
  g=d.loc[list(idx)].sort_values('t');ts=g.t.to_numpy(np.int64);m=g.build_now.to_numpy(int);run=ts[0];prev=m[0]
  for j,(ii,t,mode) in enumerate(zip(g.index,ts,m)):
   if j and mode!=prev:run=t
   lo5=np.searchsorted(ts,t-5000,side='left');lo15=np.searchsorted(ts,t-15000,side='left');hist=m[lo15:j+1];trans=int(np.sum(hist[1:]!=hist[:-1])) if len(hist)>1 else 0;future=(ts>t)&(ts<=t+5000);d.at[ii,'current_mode_age_s']=(t-run)/1000.;d.at[ii,'events_5s']=j-lo5;d.at[ii,'events_15s']=j-lo15;d.at[ii,'transitions_15s']=trans;d.at[ii,'continue_weak_5s']=int(mode==1 and np.any(m[future]==1))
   if mode==1:
    if j+1<len(g) and ts[j+1]-t<=5000:d.at[ii,'management_label_5s']='CONTINUE_WEAK' if m[j+1]==1 else 'HANDOFF_ALLOW'
    else:d.at[ii,'management_label_5s']='OBSERVE_NO_EVENT'
   prev=mode
 d.to_csv(CACHE,index=False);return d,{'selectedMarkets':len(ok),'rows':int(len(d)),'markets':int(d.market_id.nunique()),'sealed20260816Excluded':True}
def main():
 d,cov=build_cache();d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=FULL).copy();ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=min(200,max(120,int(len(ms)*2/3)));rem=len(ms)-initial;sizes=[rem//4]*4
 for i in range(rem%4):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  if sz<=0:continue
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'rows':int(len(te))}
  # M0 continuation on current BUILD only
  tr0=tr[tr.build_now==1];te0=te[te.build_now==1]
  for j,(name,feats) in enumerate([('M0_PORT',PORT),('M0_RESP',PORT+RESP),('M0_FULL',FULL)]):
   m=hgb(13000+bi*30+j).fit(tr0[feats],tr0.continue_weak_5s.astype(int));b[name]=binmet(te0.continue_weak_5s,m.predict_proba(te0[feats])[:,1])
  # M1 three-class current BUILD
  tr1=tr[(tr.build_now==1)&(tr.management_label_5s!='')];te1=te[(te.build_now==1)&(te.management_label_5s!='')]
  for j,(name,feats) in enumerate([('M1_PORT',PORT),('M1_RESP',PORT+RESP),('M1_FULL',FULL)]):
   m=hgb(14000+bi*30+j).fit(tr1[feats],tr1.management_label_5s);pp=m.predict_proba(te1[feats]);order=list(m.classes_);p=np.column_stack([pp[:,order.index(c)] for c in CLASSES]);b[name]=multimet(te1.management_label_5s,p)
  blocks.append(b)
 def sb(k):
  x=[b[k] for b in blocks];return {'meanAuc':float(np.mean([q['auc'] for q in x])),'worstAuc':float(np.min([q['auc'] for q in x])),'stdAuc':float(np.std([q['auc'] for q in x])),'meanAp':float(np.mean([q['ap'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x]))}
 def sm(k):
  x=[b[k] for b in blocks];return {'meanMacroAuc':float(np.mean([q['macroAuc'] for q in x])),'worstMacroAuc':float(np.min([q['macroAuc'] for q in x])),'stdMacroAuc':float(np.std([q['macroAuc'] for q in x])),'meanMacroAp':float(np.mean([q['macroAp'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x])),'meanBalancedAccuracy':float(np.mean([q['balancedAccuracy'] for q in x])),'meanHandoffRecall':float(np.mean([q['perClassRecall']['HANDOFF_ALLOW'] for q in x])),'worstHandoffRecall':float(np.min([q['perClassRecall']['HANDOFF_ALLOW'] for q in x]))}
 s={k:sb(k) for k in ['M0_PORT','M0_RESP','M0_FULL']};s.update({k:sm(k) for k in ['M1_PORT','M1_RESP','M1_FULL']});s['M0_FULL_DELTA']={'meanAuc':s['M0_FULL']['meanAuc']-s['M0_PORT']['meanAuc'],'worstAuc':s['M0_FULL']['worstAuc']-s['M0_PORT']['worstAuc'],'meanAp':s['M0_FULL']['meanAp']-s['M0_PORT']['meanAp'],'logLossImprovement':s['M0_PORT']['meanLogLoss']-s['M0_FULL']['meanLogLoss']};s['M1_FULL_DELTA']={'meanMacroAuc':s['M1_FULL']['meanMacroAuc']-s['M1_PORT']['meanMacroAuc'],'worstMacroAuc':s['M1_FULL']['worstMacroAuc']-s['M1_PORT']['worstMacroAuc'],'meanMacroAp':s['M1_FULL']['meanMacroAp']-s['M1_PORT']['meanMacroAp'],'logLossImprovement':s['M1_PORT']['meanLogLoss']-s['M1_FULL']['meanLogLoss'],'meanHandoffRecall':s['M1_FULL']['meanHandoffRecall']-s['M1_PORT']['meanHandoffRecall'],'worstHandoffRecall':s['M1_FULL']['worstHandoffRecall']-s['M1_PORT']['worstHandoffRecall']}
 art={'version':'R4_MANAGEMENT_LARGE300_V1','researchOnly':True,'actionAuthority':False,'coverage':cov|{'phaseRows':int(len(d)),'phaseMarkets':int(d.market_id.nunique()),'phase':'60-300s'},'features':{'portfolio':PORT,'responsibility':RESP,'memory':MEM},'summary':s,'blocks':blocks,'cache':str(CACHE.relative_to(ROOT)).replace('\\','/'),'guards':['No public raw Predict/strike; management-only state with direct OUR/R3.1 analogues.','Target future events are labels only.','2026-08-16 local special markets excluded.','Resting owners are high-confidence inferred proxies; runtime analogue must use OUR exact owner ledger.','No action authority, threshold sweep, winner/settlement.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':s,'blocks':[{'block':b['block'],'rows':b['rows'],'M0_FULL':b['M0_FULL'],'M1_FULL':b['M1_FULL']} for b in blocks]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
