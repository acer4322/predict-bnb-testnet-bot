from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_event_overlap_resting_responsibility_ceiling_v1.json'
BASE=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']
OWN=['weak_active_owners','dominant_active_owners']
def hgb(seed): return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=260,random_state=seed)
def auc(y,p): return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def ap(y,p): return float(average_precision_score(y,p)) if np.sum(y)>0 else None
def query(db,sql,params=()):
 c=sqlite3.connect(f'file:{db}?mode=ro',uri=True);c.execute('pragma query_only=on');d=pd.read_sql_query(sql,c,params=params);c.close();return d
def timestamp_events(te,pcol):
 ev=[]
 for (mid,t),g in te.groupby(['market_id','first_event_ms'],sort=True):
  w=np.maximum(g.shares.to_numpy(float),1e-9);w=w/w.sum();ev.append({'market_id':int(mid),'t':int(t),'target':int(np.sum(g.build.to_numpy(float)*w)>=.5),'pred':int(np.sum(g[pcol].to_numpy(float)*w)>=.5)})
 return pd.DataFrame(ev).sort_values(['market_id','t'])
def transitions(ev,col):
 o=[]
 for mid,g in ev.groupby('market_id'):
  prev=None
  for r in g.itertuples():
   s=int(getattr(r,col))
   if prev is not None and s!=prev:o.append((int(mid),int(r.t),'A2B' if s else 'B2A'))
   prev=s
 return o
def match(tg,pr,tol=5000):
 by={}
 for p in pr:by.setdefault((p[0],p[2]),[]).append(p)
 used=set();lags=[];n=0
 for t in tg:
  cand=[]
  for p in by.get((t[0],t[2]),[]):
   if p in used:continue
   d=abs(p[1]-t[1])
   if d<=tol:cand.append((d,p))
  if cand:
   _,p=min(cand,key=lambda z:z[0]);used.add(p);n+=1;lags.append(p[1]-t[1])
 rec=n/len(tg) if tg else 0.;prec=n/len(pr) if pr else 0.;f=2*rec*prec/(rec+prec) if rec+prec else 0.
 return {'targetEvents':len(tg),'predEvents':len(pr),'matched':n,'recall':rec,'precision':prec,'f1':f,'medianAbsLagMs':float(np.median(np.abs(lags))) if lags else None}
def add_owner_features(f):
 mids=sorted(set(f.market_id.astype(int)));ph=','.join('?'*len(mids))
 po=query('data/target_wallet_official_v1.db',f"select market_id,parent_id,order_hash from target_parent_orders where market_id in ({ph})",mids)
 life=query('data/wallet_maker_book_inference.db',f"select market_id,order_hash,target_side,placement_first_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 key=po.drop_duplicates(['market_id','parent_id']).set_index(['market_id','parent_id']).order_hash.to_dict();groups={int(m):g for m,g in life.groupby('market_id')};wa=[];da=[];covered=[]
 for r in f.itertuples():
  gl=groups.get(int(r.market_id)); cur=key.get((int(r.market_id),str(r.parent_id)))
  if gl is None:wa.append(np.nan);da.append(np.nan);covered.append(0);continue
  t=int(r.first_event_ms);a=gl[(gl.placement_first_ms<t)&(gl.last_target_ms>=t)]
  if cur is not None:a=a[a.order_hash.astype(str)!=str(cur)]
  wa.append(float((a.target_side.astype(str)==str(r.weak_side)).sum()));da.append(float((a.target_side.astype(str)==str(r.dominant_side)).sum()));covered.append(1)
 z=f.copy();z['weak_active_owners']=wa;z['dominant_active_owners']=da;z['owner_coverage']=covered;return z

def main():
 f=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f=f.dropna(subset=BASE+['first_event_ms','shares','is_add','weak_side','dominant_side','parent_id']).copy();f['build']=(f.is_add.astype(int)==0).astype(int);f=add_owner_features(f);f=f.dropna(subset=OWN).sort_values(['market_id','first_event_ms']).copy()
 ms=f.groupby('market_id').first_event_ms.min().sort_values().index.astype(int).tolist();initial=max(20,int(len(ms)*2/3));rem=len(ms)-initial;sizes=[rem]
 cur=initial;blocks=[];allte=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=f[f.market_id.isin(trm)];te=f[f.market_id.isin(tem)].copy();m0=hgb(9100+bi).fit(tr[BASE],tr.build);m1=hgb(9200+bi).fit(tr[BASE+OWN],tr.build);te['p_base']=m0.predict_proba(te[BASE])[:,1];te['p_owner']=m1.predict_proba(te[BASE+OWN])[:,1]
  e0=timestamp_events(te,'p_base');e1=timestamp_events(te,'p_owner');tg=transitions(e0,'target');p0=transitions(e0,'pred');p1=transitions(e1,'pred');blocks.append({'block':bi,'testMarkets':tem,'rows':int(len(te)),'base':{'auc':auc(te.build,te.p_base),'ap':ap(te.build,te.p_base),'overlap5s':match(tg,p0)},'plusOwners':{'auc':auc(te.build,te.p_owner),'ap':ap(te.build,te.p_owner),'overlap5s':match(tg,p1)}});allte.append(te)
 te=pd.concat(allte,ignore_index=True);e0=timestamp_events(te,'p_base');e1=timestamp_events(te,'p_owner');tg=transitions(e0,'target');p0=transitions(e0,'pred');p1=transitions(e1,'pred');b={'auc':auc(te.build,te.p_base),'ap':ap(te.build,te.p_base),'overlap5s':match(tg,p0)};o={'auc':auc(te.build,te.p_owner),'ap':ap(te.build,te.p_owner),'overlap5s':match(tg,p1)};delta={'auc':o['auc']-b['auc'],'ap':o['ap']-b['ap'],'recall':o['overlap5s']['recall']-b['overlap5s']['recall'],'precision':o['overlap5s']['precision']-b['overlap5s']['precision'],'f1':o['overlap5s']['f1']-b['overlap5s']['f1'],'predTransitions':o['overlap5s']['predEvents']-b['overlap5s']['predEvents']}
 art={'version':'R4_EVENT_OVERLAP_RESTING_RESPONSIBILITY_CEILING_V1','researchOnly':True,'actionAuthority':False,'question':'Does strict-past side-specific resting responsibility occupancy explain incremental Target Formation mode/transition events beyond the current pointwise portable BUILD context?','coverage':{'marketsTotalWithOwnerProxy':int(f.market_id.nunique()),'rows':int(len(f)),'testMarkets':int(te.market_id.nunique()),'testRows':int(len(te))},'features':{'base':BASE,'incremental':OWN},'summary':{'base':b,'plusOwners':o,'delta':delta},'blocks':blocks,'guards':['Owner proxy is strict-past inferred lifecycle, not private exchange order state.','Current parent excluded when order_hash mapping is available.','No threshold sweep; 0.5 natural boundary.','Diagnostic ceiling only; runtime analogue must use OUR own live order ownership state.','No winner/settlement/future input.']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':art['summary'],'blocks':blocks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
