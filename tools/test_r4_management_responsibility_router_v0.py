from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_router_v0.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_router_v0_rows.csv'
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant','placement_readiness_native_5s']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
FULL=BASE+RESP+MEM

def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=25,l2_regularization=1,max_iter=220,random_state=seed)
def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}
def query(db,sql,params=()):
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.execute('pragma query_only=on');d=pd.read_sql_query(sql,c,params=params);c.close();return d

def event_rows(df):
 out=[]
 for (mid,t),g in df.groupby(['market_id','first_event_ms'],sort=True):
  w=np.maximum(g.shares.fillna(0).to_numpy(float),1e-9);w=w/w.sum()
  r={'market_id':int(mid),'t':int(t),'weak_side':str(g.weak_side.iloc[0]),'dominant_side':str(g.dominant_side.iloc[0]),'build_share':float(np.sum(g.build.to_numpy(float)*w)),'build_now':int(np.sum(g.build.to_numpy(float)*w)>=.5)}
  for c in BASE[:-1]: r[c]=float(np.sum(g[c].to_numpy(float)*w))
  r['placement_readiness_native_5s']=float(np.sum(g.placement_readiness_native_5s.to_numpy(float)*w))
  out.append(r)
 return pd.DataFrame(out).sort_values(['market_id','t']).reset_index(drop=True)

def add_owner(ev):
 mids=sorted(int(x) for x in ev.market_id.unique());ph=','.join('?'*len(mids))
 life=query('data/wallet_maker_book_inference.db',f"select market_id,target_side,placement_first_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 groups={int(m):g for m,g in life.groupby('market_id')};vals=[]
 for r in ev.itertuples():
  g=groups.get(int(r.market_id));
  if g is None: vals.append((np.nan,np.nan,np.nan,np.nan,0));continue
  a=g[(g.placement_first_ms<int(r.t))&(g.last_target_ms>=int(r.t))]
  w=a[a.target_side.astype(str)==str(r.weak_side)];d=a[a.target_side.astype(str)==str(r.dominant_side)]
  wa=len(w);da=len(d);wage=((int(r.t)-w.placement_first_ms).max()/1000. if wa else 0.);dage=((int(r.t)-d.placement_first_ms).max()/1000. if da else 0.)
  vals.append((float(wa),float(da),float(wage),float(dage),1))
 z=ev.copy();z[['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','owner_coverage']]=pd.DataFrame(vals,index=z.index);return z

def add_memory_labels(ev):
 z=ev.copy();
 for c in ['current_mode_age_s','events_5s','events_15s','transitions_15s','need_weak_5s','continue_weak_5s']: z[c]=0.0
 for mid,idx in z.groupby('market_id').groups.items():
  ids=list(idx);g=z.loc[ids].sort_values('t'); ts=g.t.to_numpy(np.int64); modes=g.build_now.to_numpy(int); bshare=g.build_share.to_numpy(float)
  run_start=ts[0];prev=modes[0]
  for j,(ii,t,m) in enumerate(zip(g.index,ts,modes)):
   if j and m!=prev: run_start=t
   lo5=np.searchsorted(ts,t-5000,side='left');lo15=np.searchsorted(ts,t-15000,side='left')
   histm=modes[lo15:j+1]; trans=int(np.sum(histm[1:]!=histm[:-1])) if len(histm)>1 else 0
   future=(ts>t)&(ts<=t+5000); future_repair=bool(np.any(bshare[future]>0))
   z.at[ii,'current_mode_age_s']=(t-run_start)/1000.;z.at[ii,'events_5s']=j-lo5;z.at[ii,'events_15s']=j-lo15;z.at[ii,'transitions_15s']=trans;z.at[ii,'need_weak_5s']=int(future_repair);z.at[ii,'continue_weak_5s']=int(m==1 and future_repair)
   prev=m
 return z

def main():
 p0=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p0.market_id=p0.market_id.astype(int);p0['predict_edge']=(p0.predict_up_mid-.5).abs();lab='label_next_inferred_placement_any_5s';p0=p0.dropna(subset=NATIVE+[lab]).copy();src=hgb(60999);src.fit(p0[NATIVE],p0[lab].astype(int))
 f=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f['build']=(f.is_add.astype(int)==0).astype(int);f=f.dropna(subset=['market_id','first_event_ms','shares','weak_side','dominant_side']+BASE[:-1]).copy();f['placement_readiness_native_5s']=src.predict_proba(f[NATIVE])[:,1];f=f.dropna(subset=BASE).copy()
 ev=event_rows(f);ev=add_owner(ev);ev=ev[ev.owner_coverage==1].copy();ev=add_memory_labels(ev);ev=ev.dropna(subset=FULL).copy();ev.to_csv(ROWS,index=False)
 ms=ev.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=max(18,int(len(ms)*.58));rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=ev[ev.market_id.isin(trm)];te=ev[ev.market_id.isin(tem)];b={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'rows':int(len(te)),'heads':{}}
  for target in ['need_weak_5s','continue_weak_5s']:
   trh=tr.copy();teh=te.copy()
   if target=='continue_weak_5s': trh=trh[trh.build_now==1];teh=teh[teh.build_now==1]
   if len(teh)==0 or trh[target].nunique()<2 or teh[target].nunique()<2: continue
   head={}
   for j,(name,feats) in enumerate([('BASE',BASE),('PLUS_RESP',BASE+RESP),('PLUS_RESP_MEMORY',FULL)]):
    m=hgb(61000+bi*20+j+(0 if target=='need_weak_5s' else 10));m.fit(trh[feats],trh[target]);p=m.predict_proba(teh[feats])[:,1];head[name]=metric(teh[target],p)
   b['heads'][target]=head
  blocks.append(b)
 def summarize(target,name):
  x=[b['heads'][target][name] for b in blocks if target in b['heads'] and name in b['heads'][target]]
  if not x:return None
  return {'blocks':len(x),'meanAuc':float(np.mean([q['auc'] for q in x])),'worstAuc':float(np.min([q['auc'] for q in x])),'stdAuc':float(np.std([q['auc'] for q in x])),'meanAp':float(np.mean([q['ap'] for q in x])),'meanLogLoss':float(np.mean([q['logLoss'] for q in x]))}
 summary={}
 for t in ['need_weak_5s','continue_weak_5s']:
  summary[t]={n:summarize(t,n) for n in ['BASE','PLUS_RESP','PLUS_RESP_MEMORY']}
  if summary[t]['BASE'] and summary[t]['PLUS_RESP_MEMORY']:
   a=summary[t]['BASE'];q=summary[t]['PLUS_RESP_MEMORY'];summary[t]['deltaFullVsBase']={'meanAuc':q['meanAuc']-a['meanAuc'],'worstAuc':q['worstAuc']-a['worstAuc'],'stdAuc':q['stdAuc']-a['stdAuc'],'meanAp':q['meanAp']-a['meanAp'],'logLossImprovement':a['meanLogLoss']-q['meanLogLoss']}
 art={'version':'R4_MANAGEMENT_RESPONSIBILITY_ROUTER_V0','researchOnly':True,'actionAuthority':False,'purpose':'Pilot a management-layer responsibility router that predicts whether weak-side responsibility remains/appears in the next 5s using semantic beliefs plus strict-past side-specific ownership and lifecycle memory, without predicting order price/size/channel.','coverage':{'markets':int(ev.market_id.nunique()),'rows':int(len(ev)),'ownerProxy':'Target inferred resting parent lifecycle; runtime analogue is OUR exact owner ledger/R3.1 child state'},'heads':{'NEED_WEAK_5S':'future 5s contains any Target REPAIR/weak responsibility event','CONTINUE_WEAK_5S':'conditional on current BUILD, future 5s contains another REPAIR responsibility event'},'features':{'BASE':BASE,'RESPONSIBILITY':RESP,'MEMORY':MEM},'summary':summary,'blocks':blocks,'guards':['Strict-past features only; future is label only.','R3.1 remains information-only; no R3.1 action authority implied.','No order price/size/channel label.','No threshold or hyperparameter sweep.','Small pilot because retained Target resting-owner overlap is limited.'],'rowsArtifact':str(ROWS.relative_to(ROOT)).replace('\\','/')};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
