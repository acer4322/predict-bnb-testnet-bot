from __future__ import annotations
import json, sqlite3, joblib, os
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, recall_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
CON=P/'r4_state_shaping_authorization_v11_external40_contract.json'
MOD=P/'r4_state_shaping_authorization_v11_portable_shadow.joblib'
OFF=Path(os.environ.get('R4_TARGET_DB',str(ROOT/'data/target_wallet_official_v1.db')))
LIFE=Path(os.environ.get('R4_LIFE_DB',str(ROOT/'data/wallet_maker_book_inference.db')))
OUT=P/'r4_state_shaping_authorization_v11_external40_score.json'
ROWS=P/'r4_state_shaping_authorization_v11_external40_rows.csv'
H5=5000;H15=15000;EPS=1e-9
F=['seconds_left_norm','risk_deficit_gap_ratio','floor_gap_ratio','upside_gap_ratio','coverage','floor_per_gross','events_5s_rate','events_15s_rate','transitions_15s_rate','mode_age_log','weak_active_roots','dominant_active_roots','weak_unresolved_gap_ratio','dominant_unresolved_gap_ratio','weak_progress_ratio','dominant_progress_ratio','weak_fill5_gap_ratio','dominant_fill5_gap_ratio']
Y='future_different_objective_5s'
def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{Path(db).as_posix()}?mode=ro',uri=True);c.execute('pragma query_only=on');x=pd.read_sql_query(sql,c,params=params);c.close();return x
def prep(x):
 x=x.copy();g=np.maximum(x.abs_gap.to_numpy(float),18.0);x['seconds_left_norm']=x.seconds_left/300.;x['risk_deficit_gap_ratio']=x.risk_deficit/g;x['floor_gap_ratio']=x.floor/g;x['upside_gap_ratio']=x.upside/g;x['events_5s_rate']=x.events_5s/5.;x['events_15s_rate']=x.events_15s/15.;x['transitions_15s_rate']=x.transitions_15s/15.;x['mode_age_log']=np.log1p(np.maximum(x.mode_age_s,0));x['weak_unresolved_gap_ratio']=x.weak_unresolved_shares/g;x['dominant_unresolved_gap_ratio']=x.dominant_unresolved_shares/g;x['weak_fill5_gap_ratio']=x.weak_fill_shares_5s/g;x['dominant_fill5_gap_ratio']=x.dominant_fill_shares_5s/g;return x
def met(z,p):
 y=z[Y].to_numpy(int);p=np.asarray(p,float);pred=(p>=.5).astype(int);return {'n':int(len(y)),'positiveSupport':int(y.sum()),'negativeSupport':int((1-y).sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(np.unique(y))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(np.unique(y))>1 else None,'positiveRecall':float(recall_score(y,pred,pos_label=1,zero_division=0)),'negativeRecall':float(recall_score(y,pred,pos_label=0,zero_division=0)),'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1])) if len(y) else None,'meanProbability':float(np.mean(p)) if len(p) else None}
def main():
 con=json.loads(CON.read_text(encoding='utf-8'));assert con['status']=='FROZEN_BEFORE_LABEL_EXTRACTION_OR_SCORING';mids=tuple(map(int,con['validationCohort']));ph=','.join('?'*len(mids))
 life=qdf(LIFE,f"select market_id,lower(order_hash) order_hash,target_side,placement_first_ms,last_target_ms,placement_allocated_shares,expected_parent_shares,confidence,placement_coverage,fill_allocation_coverage from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 life.market_id=life.market_id.astype(int);life.placement_first_ms=life.placement_first_ms.astype(np.int64);life.last_target_ms=life.last_target_ms.astype(np.int64)
 parents=qdf(OFF,f"select market_id,parent_id,lower(order_hash) order_hash,side,average_price,shares,first_event_ms from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID' order by market_id,first_event_ms,parent_id",mids)
 parents.market_id=parents.market_id.astype(int);parents.first_event_ms=parents.first_event_ms.astype(np.int64);parents=parents.merge(life[['market_id','order_hash']].drop_duplicates(),on=['market_id','order_hash'],how='inner')
 mk=qdf(OFF,f"select market_id,window_end_ms from target_markets where market_id in ({ph})",mids);wend={int(r.market_id):int(r.window_end_ms) for r in mk.itertuples()}
 events=qdf(OFF,f"select market_id,lower(order_hash) order_hash,role,side,event_ms,price,shares from wallet_shadow_target_events where market_id in ({ph}) and quote_type='BID' order by market_id,event_ms,id",mids);events.market_id=events.market_id.astype(int);events.event_ms=events.event_ms.astype(np.int64)
 eg={int(k):g.sort_values('event_ms').copy() for k,g in events.groupby('market_id')};lg={int(k):g.copy() for k,g in life.groupby('market_id')};raw=[]
 for mid,gp in parents.groupby('market_id'):
  ge=eg.get(int(mid));gl=lg.get(int(mid));
  if ge is None or gl is None:continue
  times=ge.event_ms.to_numpy(np.int64);sides=ge.side.astype(str).to_numpy();sh=ge.shares.fillna(0).to_numpy(float);px=ge.price.fillna(0).to_numpy(float);up=np.cumsum(np.where(sides=='UP',sh,0.));dn=np.cumsum(np.where(sides=='DOWN',sh,0.));cost=np.cumsum(sh*px)
  maker=ge[ge.role.astype(str).str.upper().eq('MAKER')].copy();byhash={str(h):x.sort_values('event_ms') for h,x in maker.groupby('order_hash') if pd.notna(h)}
  for r in gp.sort_values(['first_event_ms','parent_id']).itertuples():
   t=int(r.first_event_ms);j=np.searchsorted(times,t,side='left')-1;u=float(up[j]) if j>=0 else 0.;d=float(dn[j]) if j>=0 else 0.;c=float(cost[j]) if j>=0 else 0.;gap=u-d
   if abs(gap)<=EPS:continue
   dom='UP' if gap>0 else 'DOWN';weak='DOWN' if dom=='UP' else 'UP';mode='ADD' if str(r.side)==dom else 'REPAIR';family='STATE_SHAPING' if mode=='ADD' else 'PAIR_BALANCE';gross=u+d;floor=min(u,d)-c;upside=max(u,d)-c;absnet=abs(gap);coverage=2*min(u,d)/gross if gross>EPS else 0.;sec=(wend[int(mid)]-t)/1000.
   a=gl[(gl.placement_first_ms<t)&(gl.last_target_ms>=t)&(gl.order_hash.astype(str)!=str(r.order_hash))].copy();wa=a[a.target_side.astype(str)==weak];da=a[a.target_side.astype(str)==dom]
   def prog(z):
    commit=real=recent=0.
    for q in z.itertuples():
     cq=float(q.placement_allocated_shares) if pd.notna(q.placement_allocated_shares) and float(q.placement_allocated_shares)>EPS else float(q.expected_parent_shares or 0);x=byhash.get(str(q.order_hash));rr=rc=0.
     if x is not None:
      tt=x.event_ms.to_numpy(np.int64);qq=x.shares.fillna(0).to_numpy(float);k=np.searchsorted(tt,t,'left');lo=np.searchsorted(tt,t-H5,'left');rr=float(qq[:k].sum());rc=float(qq[lo:k].sum())
     commit+=cq;real+=min(rr,cq);recent+=rc
    return int(len(z)),float(max(0.,commit-real)),float(real/commit) if commit>EPS else 0.,float(recent)
   wr,wu,wp,wf=prog(wa);dr,du,dp,df=prog(da)
   raw.append({'market_id':int(mid),'t':t,'parent_id':str(r.parent_id),'objective_family':family,'objective_key':family+'|'+str(r.side),'seconds_left':sec,'abs_gap':absnet,'risk_deficit':max(0.,-floor),'floor':floor,'upside':upside,'coverage':coverage,'floor_per_gross':floor/gross if gross>EPS else 0.,'weak_active_roots':wr,'dominant_active_roots':dr,'weak_unresolved_shares':wu,'dominant_unresolved_shares':du,'weak_progress_ratio':wp,'dominant_progress_ratio':dp,'weak_fill_shares_5s':wf,'dominant_fill_shares_5s':df})
 d=pd.DataFrame(raw).sort_values(['market_id','t','parent_id']).reset_index(drop=True);mem=[]
 for mid,g in d.groupby('market_id'):
  g=g.sort_values(['t','parent_id']).reset_index(drop=True);ts=g.t.to_numpy(np.int64);fam=g.objective_family.astype(str).to_numpy()
  for i,r in g.iterrows():
   t=int(r.t);prior=np.where(ts<t)[0];p5=np.where((ts<t)&(ts>=t-H5))[0];p15=np.where((ts<t)&(ts>=t-H15))[0];prev=int(prior[-1]) if len(prior) else None;trans=0
   if len(p15)>1: x=fam[p15];trans=int(np.sum(x[1:]!=x[:-1]))
   age=0.
   if prev is not None:
    k=prev;start=ts[k];fm=fam[k]
    while k>0 and fam[k-1]==fm and ts[k]-ts[k-1]<=H15:start=ts[k-1];k-=1
    age=(t-start)/1000.
   z=r.to_dict();z.update({'events_5s':int(len(p5)),'events_15s':int(len(p15)),'transitions_15s':trans,'mode_age_s':float(age),'current_pair_balance':int(r.objective_family=='PAIR_BALANCE'),'current_state_shaping':int(r.objective_family=='STATE_SHAPING')});mem.append(z)
 d=pd.DataFrame(mem).sort_values(['market_id','t','parent_id']).reset_index(drop=True);fut=[]
 for mid,g in d.groupby('market_id'):
  for _,r in g.iterrows():
   z=g[(g.t>r.t)&(g.t<=r.t+H5)]
   if not len(z):continue
   same=bool((z.objective_key==r.objective_key).any());diff=bool((z.objective_key!=r.objective_key).any())
   if same==diff:continue
   q=r.to_dict();q[Y]=int(diff);fut.append(q)
 x=prep(pd.DataFrame(fut));x=x[(x.current_pair_balance==1)&(x.current_state_shaping==0)].dropna(subset=F+[Y]).copy();x[Y]=x[Y].astype(int);obj=joblib.load(MOD);assert obj['features']==F;probs=obj['model'].predict_proba(x[F])[:,1];x['p_state_shaping_authorization']=probs;x.to_csv(ROWS,index=False)
 bands={'ALL':x,'FORMATION_180_300':x[(x.seconds_left>180)&(x.seconds_left<=300)],'MANAGEMENT_60_180':x[(x.seconds_left>=60)&(x.seconds_left<=180)]};metrics={k:met(v,obj['model'].predict_proba(v[F])[:,1]) for k,v in bands.items() if len(v)};g=con['primaryGates'];a=metrics['ALL'];f=metrics['FORMATION_180_300'];checks={'overallAuc':a['auc']>=g['overallAucMin'],'overallBA':a['balancedAccuracy']>=g['overallBalancedAccuracyMin'],'formationAuc':f['auc']>=g['formation180to300AucMin'],'formationBA':f['balancedAccuracy']>=g['formation180to300BalancedAccuracyMin'],'formationPositiveSupport':f['positiveSupport']>=g['formationPositiveSupportMin'],'formationNegativeSupport':f['negativeSupport']>=g['formationNegativeSupportMin']};out={'version':'R4_STATE_SHAPING_AUTHORIZATION_V1_1_EXTERNAL40_SCORE','researchOnly':True,'actionAuthority':False,'candidateChangedAfterFreeze':False,'contract':CON.name,'cohort':list(mids),'coverage':{'marketsRequested':len(mids),'marketsWithRows':int(x.market_id.nunique()),'rows':int(len(x))},'metrics':metrics,'gateChecks':checks,'externalValidationPassed':bool(all(checks.values())),'interpretation':'Chronology-disjoint/head-label-disjoint Target external validation. Passing does not grant live/action authority; realistic-HFT whole-market transport remains required.'};OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
