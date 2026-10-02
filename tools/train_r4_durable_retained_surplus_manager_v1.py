from __future__ import annotations
import sqlite3,json,statistics,datetime,zoneinfo
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,mean_absolute_error
try:
 from interpret.glassbox import ExplainableBoostingClassifier,ExplainableBoostingRegressor
except Exception:
 ExplainableBoostingClassifier=ExplainableBoostingRegressor=None
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/target_wallet_official_v1.db'; OUT=ROOT/'data/research/r4_v0/hourly/r4_durable_retained_surplus_manager_v1.json'; MOD=ROOT/'data/research/r4_v0/hourly/r4_durable_retained_surplus_manager_v1.joblib'; ROWS=ROOT/'data/research/r4_v0/hourly/r4_durable_retained_surplus_manager_v1_rows.csv'
OUT.parent.mkdir(parents=True,exist_ok=True)
def fee(sh,px): return sh*px*(1-px)*.02*4
def auc(y,p): return float(roc_auc_score(y,p)) if len(set(map(int,y)))>1 else None
c=sqlite3.connect(f"file:{DB.as_posix()}?mode=ro",uri=True);c.row_factory=sqlite3.Row
resolved={}
for r in c.execute("select market_id,resolved_at_ms from target_market_results where asset='BTC'"):
 t=int(r['resolved_at_ms'] or 0)
 if not t: continue
 dt=datetime.datetime.fromtimestamp(t/1000,datetime.timezone.utc).astimezone(zoneinfo.ZoneInfo('Asia/Taipei'))
 if dt.date()==datetime.date(2026,8,16): continue
 resolved[int(r['market_id'])]=t
parents={}
for r in c.execute("select market_id,role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id"):
 mid=int(r['market_id']);
 if mid in resolved: parents.setdefault(mid,[]).append(dict(r))
anchors=[];base_rows={}
for mid,evs in parents.items():
 up=down=cost=fees=0.;tr=[]
 for i,e in enumerate(evs):
  sh=float(e['shares'] or 0);px=float(e['average_price'] or 0);role=str(e['role']);side=str(e['side']);t=int(e['first_event_ms'] or 0)
  if side=='UP':up+=sh
  elif side=='DOWN':down+=sh
  cost+=sh*px
  if role=='TAKER':fees+=fee(sh,px)
  pu=up-cost-fees;pdn=down-cost-fees
  tr.append({'i':i,'t':t,'floor':min(pu,pdn),'upside':max(pu,pdn),'up':up,'down':down,'surplus':abs(up-down),'base':min(up,down),'role':role,'side':side,'price':px,'shares':sh})
 fs=next((x for x in tr if x['floor']>=0),None)
 if not fs or fs['i']==0:continue
 post=[x for x in tr if fs['t']<=x['t']<=fs['t']+15000];dur=int(bool(post and min(x['floor'] for x in post)>=-5 and post[-1]['floor']>=0))
 pre=tr[fs['i']-1];ss='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT';weak='DOWN' if ss=='UP' else 'UP' if ss=='DOWN' else 'FLAT'
 w5=[x for x in tr if pre['t']-5000<=x['t']<=pre['t']];w15=[x for x in tr if pre['t']-15000<=x['t']<=pre['t']]
 def shs(w,side,role='MAKER'):return sum(x['shares'] for x in w if x['side']==side and x['role']==role) if side!='FLAT' else 0.
 # No tuned retained-surplus threshold: use continuous cross surplus and retention ratio. Joint binary only asks whether durable crossing retains any directional surplus (>0).
 retention=float(fs['surplus']/max(pre['surplus'],1e-9)) if pre['surplus']>1e-9 else 0.; joint=int(dur and fs['surplus']>1e-9)
 base_rows[mid]={'market_id':mid,'resolved_at_ms':resolved[mid],'y_durable':dur,'y_joint':joint,'cross_surplus':fs['surplus'],'retention_ratio':retention,'cross_floor':fs['floor'],'pre_floor':pre['floor'],'pre_upside':pre['upside'],'pre_surplus':pre['surplus'],'pre_base':pre['base'],'pre_surplus_ratio':pre['surplus']/(pre['up']+pre['down']+1e-9),'pre_floor_per_base':pre['floor']/(pre['base']+1e-9),'last_price':pre['price'],'last_shares':pre['shares'],'last_role_taker':int(pre['role']=='TAKER'),'weak_parent_shares_5s':shs(w5,weak),'weak_parent_shares_15s':shs(w15,weak),'surplus_parent_shares_15s':shs(w15,ss),'parent_weak_dominance_15s':(shs(w15,weak)+1)/(shs(w15,ss)+1),'events_parent_5s':len(w5),'events_parent_15s':len(w15),'seconds_from_first_event':(pre['t']-tr[0]['t'])/1000.}
 anchors.append((mid,pre['t']-15000,pre['t'],weak,ss))
c.execute('create temp table a(mid integer primary key,t0 integer,t1 integer,weak text,ss text)');c.executemany('insert into a values(?,?,?,?,?)',anchors)
events=c.execute("select e.market_id,e.side,e.observed_at_ms,e.price,e.shares,a.t1 from wallet_shadow_target_events e join a on a.mid=e.market_id where e.asset='BTC' and e.role='MAKER' and e.observed_at_ms between a.t0 and a.t1 order by e.market_id,e.observed_at_ms,e.id").fetchall();by={}
for r in events:by.setdefault(int(r['market_id']),[]).append(r)
def cadence(L,t1,side,ms):
 z=[r for r in L if r['side']==side and int(r['observed_at_ms'])>=t1-ms] if side!='FLAT' else [];shares=[float(r['shares'] or 0) for r in z];px=[float(r['price'] or 0) for r in z];obs=[int(r['observed_at_ms']) for r in z];g=[(b-a)/1000 for a,b in zip(obs,obs[1:])]
 return {'legs':len(z),'shares':sum(shares),'medSize':statistics.median(shares) if shares else 0.,'priceRange':max(px)-min(px) if px else 0.,'medGap':statistics.median(g) if g else 0.}
rows=[]
for mid,t0,t1,weak,ss in anchors:
 r=dict(base_rows[mid]);L=by.get(mid,[]);w5=cadence(L,t1,weak,5000);w15=cadence(L,t1,weak,15000);s5=cadence(L,t1,ss,5000);s15=cadence(L,t1,ss,15000)
 r.update({'obs_weak_legs_5s':w5['legs'],'obs_weak_shares_5s':w5['shares'],'obs_weak_legs_15s':w15['legs'],'obs_weak_shares_15s':w15['shares'],'obs_weak_med_size_15s':w15['medSize'],'obs_weak_price_range_15s':w15['priceRange'],'obs_weak_med_gap_15s':w15['medGap'],'obs_surplus_legs_5s':s5['legs'],'obs_surplus_shares_5s':s5['shares'],'obs_surplus_legs_15s':s15['legs'],'obs_surplus_shares_15s':s15['shares'],'obs_fill_dominance_5s':(w5['shares']+1)/(s5['shares']+1),'obs_fill_dominance_15s':(w15['shares']+1)/(s15['shares']+1),'obs_leg_dominance_15s':(w15['legs']+1)/(s15['legs']+1)});rows.append(r)
df=pd.DataFrame(rows).sort_values('resolved_at_ms').reset_index(drop=True);df.to_csv(ROWS,index=False)
features=[x for x in df.columns if x not in ['market_id','resolved_at_ms','y_durable','y_joint','cross_surplus','retention_ratio','cross_floor']];X=df[features].replace([np.inf,-np.inf],np.nan).fillna(0.);yj=df.y_joint.astype(int);yr=np.clip(df.retention_ratio.astype(float),0,2);n=len(df);a=int(n*.6);b=int(n*.8)
models={};results={}
def eval_cls(name,m):
 m.fit(X.iloc[:a],yj.iloc[:a]);models[name]=m;res={}
 for s,(lo,hi) in {'val':(a,b),'test':(b,n)}.items():
  p=m.predict_proba(X.iloc[lo:hi])[:,1];yy=yj.iloc[lo:hi];res[s]={'n':hi-lo,'rate':float(yy.mean()),'auc':auc(yy,p),'ap':float(average_precision_score(yy,p)),'logLoss':float(log_loss(yy,p,labels=[0,1]))}
 results[name]=res
h=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=180,l2_regularization=3.,random_state=77,class_weight='balanced');eval_cls('HGB_JOINT',h)
if ExplainableBoostingClassifier:
 e=ExplainableBoostingClassifier(interactions=4,max_bins=64,max_rounds=800,learning_rate=.03,min_samples_leaf=8,random_state=77);eval_cls('EBM_JOINT',e)
# continuous retained-surplus head on durable examples only; predicts retained fraction rather than imposing arbitrary threshold.
dur=df.y_durable.astype(bool);train_idx=np.where((np.arange(n)<a)&dur)[0]
rh=HistGradientBoostingRegressor(max_depth=3,learning_rate=.05,max_iter=160,l2_regularization=3.,random_state=88).fit(X.iloc[train_idx],yr.iloc[train_idx]);models['HGB_RETENTION']=rh
for s,(lo,hi) in {'val':(a,b),'test':(b,n)}.items():
 idx=np.where((np.arange(n)>=lo)&(np.arange(n)<hi)&dur)[0];pred=np.clip(rh.predict(X.iloc[idx]),0,2);results.setdefault('HGB_RETENTION',{})[s]={'n':int(len(idx)),'mae':float(mean_absolute_error(yr.iloc[idx],pred)),'actualMedian':float(np.median(yr.iloc[idx])),'predMedian':float(np.median(pred))}
if ExplainableBoostingRegressor:
 er=ExplainableBoostingRegressor(interactions=4,max_bins=64,max_rounds=800,learning_rate=.03,min_samples_leaf=8,random_state=88).fit(X.iloc[train_idx],yr.iloc[train_idx]);models['EBM_RETENTION']=er
 for s,(lo,hi) in {'val':(a,b),'test':(b,n)}.items():
  idx=np.where((np.arange(n)>=lo)&(np.arange(n)<hi)&dur)[0];pred=np.clip(er.predict(X.iloc[idx]),0,2);results.setdefault('EBM_RETENTION',{})[s]={'n':int(len(idx)),'mae':float(mean_absolute_error(yr.iloc[idx],pred)),'actualMedian':float(np.median(yr.iloc[idx])),'predMedian':float(np.median(pred))}
joblib.dump({'version':'R4_DURABLE_RETAINED_SURPLUS_MANAGER_V1','features':features,'models':models,'actionAuthority':False},MOD)
out={'version':'R4_DURABLE_RETAINED_SURPLUS_MANAGER_V1','definition':'At strict-past checkpoint immediately before first floor>=0 crossing, jointly model (1) durable 15s base with any retained directional surplus and (2) continuous retained-surplus ratio at crossing. Ordinary Target BTC; 2026-08-16 excluded; observed fills only after observed_at_ms; no winner/future runtime features.','rows':n,'markets':int(df.market_id.nunique()),'durableRate':float(df.y_durable.mean()),'jointRate':float(df.y_joint.mean()),'durableMedianPreSurplus':float(df[df.y_durable==1].pre_surplus.median()),'durableMedianCrossSurplus':float(df[df.y_durable==1].cross_surplus.median()),'durableMedianRetention':float(df[df.y_durable==1].retention_ratio.median()),'fragileMedianRetention':float(df[df.y_durable==0].retention_ratio.median()),'results':results,'artifacts':{'model':str(MOD.relative_to(ROOT)),'rows':str(ROWS.relative_to(ROOT))},'researchOnly':True,'actionAuthority':False}
OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))

