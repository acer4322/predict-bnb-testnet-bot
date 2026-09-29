from __future__ import annotations
import sqlite3,json,datetime,zoneinfo,statistics
from pathlib import Path
import numpy as np,pandas as pd,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
try:
 from interpret.glassbox import ExplainableBoostingClassifier
except Exception:
 ExplainableBoostingClassifier=None
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/target_wallet_official_v1.db';OUT=ROOT/'data/research/r4_v0/hourly/r4_formation_path_value_teacher_v1.json';MOD=ROOT/'data/research/r4_v0/hourly/r4_formation_path_value_teacher_v1.joblib';ROWS=ROOT/'data/research/r4_v0/hourly/r4_formation_path_value_teacher_v1_rows.csv'
OUT.parent.mkdir(parents=True,exist_ok=True)
GRID=[165,150,135,120,105,90,75,60]
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
 mid=int(r['market_id'])
 if mid in resolved: parents.setdefault(mid,[]).append(dict(r))
rows=[]; market_desc=[]
for mid,evs in parents.items():
 up=down=cost=fees=0.;tr=[]
 for i,e in enumerate(evs):
  sh=float(e['shares'] or 0);px=float(e['average_price'] or 0);role=str(e['role']);side=str(e['side']);t=int(e['first_event_ms'] or 0)
  if side=='UP':up+=sh
  elif side=='DOWN':down+=sh
  cost+=sh*px
  if role=='TAKER':fees+=fee(sh,px)
  pu=up-cost-fees;pdn=down-cost-fees
  tr.append({'i':i,'t':t,'role':role,'side':side,'price':px,'shares':sh,'up':up,'down':down,'floor':min(pu,pdn),'upside':max(pu,pdn),'surplus':abs(up-down),'base':min(up,down)})
 fs=next((x for x in tr if x['floor']>=0),None)
 if not fs or fs['i']==0: continue
 post=[x for x in tr if fs['t']<=x['t']<=fs['t']+15000];dur=bool(post and min(x['floor'] for x in post)>=-5 and post[-1]['floor']>=0);retained=bool(fs['surplus']>1e-9);y=int(dur and retained)
 retention=float(fs['surplus']/max(tr[fs['i']-1]['surplus'],1e-9)) if tr[fs['i']-1]['surplus']>1e-9 else 0.
 used=0
 for sec in GRID:
  target=resolved[mid]-sec*1000
  candidates=[x for x in tr if x['t']<=target and x['t']<fs['t'] and x['floor']<0]
  if not candidates: continue
  pre=candidates[-1]
  # avoid duplicating same parent checkpoint for neighboring grid slots
  if rows and rows[-1].get('market_id')==mid and rows[-1].get('checkpoint_t')==pre['t']: continue
  ss='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT';weak='DOWN' if ss=='UP' else 'UP' if ss=='DOWN' else 'FLAT'
  def win(ms): return [x for x in tr if pre['t']-ms<=x['t']<=pre['t']]
  w5,w15,w30=win(5000),win(15000),win(30000)
  def shs(w,side,role=None): return sum(x['shares'] for x in w if side!='FLAT' and x['side']==side and (role is None or x['role']==role))
  prev5=w5[0] if w5 else pre;prev15=w15[0] if w15 else pre;gross=pre['up']+pre['down']
  row={'market_id':mid,'resolved_at_ms':resolved[mid],'checkpoint_t':pre['t'],'grid_seconds_left':sec,'seconds_left':max(0.,(resolved[mid]-pre['t'])/1000.),'y':y,'future_cross_retention':retention,'pre_floor':pre['floor'],'pre_upside':pre['upside'],'pre_surplus':pre['surplus'],'pre_base':pre['base'],'pre_surplus_ratio':pre['surplus']/max(gross,1e-9),'pre_floor_per_base':pre['floor']/max(pre['base'],1e-9),'pre_upside_per_surplus':pre['upside']/max(pre['surplus'],1e-9),'last_price':pre['price'],'last_shares':pre['shares'],'last_role_taker':int(pre['role']=='TAKER'),'events_5s':len(w5),'events_15s':len(w15),'events_30s':len(w30),'maker_events_15s':sum(x['role']=='MAKER' for x in w15),'taker_events_15s':sum(x['role']=='TAKER' for x in w15),'weak_maker_shares_5s':shs(w5,weak,'MAKER'),'weak_maker_shares_15s':shs(w15,weak,'MAKER'),'weak_maker_shares_30s':shs(w30,weak,'MAKER'),'surplus_maker_shares_15s':shs(w15,ss,'MAKER'),'surplus_maker_shares_30s':shs(w30,ss,'MAKER'),'weak_taker_shares_15s':shs(w15,weak,'TAKER'),'surplus_taker_shares_15s':shs(w15,ss,'TAKER'),'parent_weak_dominance_15s':(shs(w15,weak,'MAKER')+1)/(shs(w15,ss,'MAKER')+1),'parent_weak_dominance_30s':(shs(w30,weak,'MAKER')+1)/(shs(w30,ss,'MAKER')+1),'floor_change_5s':pre['floor']-prev5['floor'],'floor_change_15s':pre['floor']-prev15['floor'],'upside_change_5s':pre['upside']-prev5['upside'],'upside_change_15s':pre['upside']-prev15['upside'],'surplus_change_5s':pre['surplus']-prev5['surplus'],'surplus_change_15s':pre['surplus']-prev15['surplus'],'seconds_from_first_event':(pre['t']-tr[0]['t'])/1000.}
  rows.append(row);used+=1
 if used: market_desc.append({'marketId':mid,'y':y,'samples':used,'retention':retention})
df=pd.DataFrame(rows).sort_values(['resolved_at_ms','market_id','checkpoint_t']).reset_index(drop=True);df.to_csv(ROWS,index=False)
features=[x for x in df.columns if x not in ['market_id','resolved_at_ms','checkpoint_t','grid_seconds_left','y','future_cross_retention']]
# chronological market split so repeated checkpoints from a market never cross partitions
markets=df[['market_id','resolved_at_ms']].drop_duplicates().sort_values('resolved_at_ms').reset_index(drop=True);nmk=len(markets);a_m=int(nmk*.6);b_m=int(nmk*.8);train=set(markets.market_id.iloc[:a_m]);val=set(markets.market_id.iloc[a_m:b_m]);test=set(markets.market_id.iloc[b_m:])
X=df[features].replace([np.inf,-np.inf],np.nan).fillna(0.);y=df.y.astype(int)
tridx=df.market_id.isin(train);vidx=df.market_id.isin(val);teidx=df.market_id.isin(test)
models={};results={}
def evaluate(name,m):
 m.fit(X.loc[tridx],y.loc[tridx]);models[name]=m;res={}
 for s,idx in [('val',vidx),('test',teidx)]:
  p=m.predict_proba(X.loc[idx])[:,1];yy=y.loc[idx];res[s]={'rows':int(idx.sum()),'markets':int(df.loc[idx,'market_id'].nunique()),'rate':float(yy.mean()),'auc':auc(yy,p),'ap':float(average_precision_score(yy,p)),'logLoss':float(log_loss(yy,p,labels=[0,1]))}
  # market-mean score gives each market equal weight
  z=pd.DataFrame({'market':df.loc[idx,'market_id'].values,'y':yy.values,'p':p}).groupby('market').agg(y=('y','first'),p=('p','mean')).reset_index();res[s]['marketMeanAuc']=auc(z.y,z.p);res[s]['marketMeanAP']=float(average_precision_score(z.y,z.p))
 results[name]=res
h=HistGradientBoostingClassifier(max_depth=4,learning_rate=.05,max_iter=220,l2_regularization=3.,random_state=91,class_weight='balanced');evaluate('HGB',h)
if ExplainableBoostingClassifier:
 e=ExplainableBoostingClassifier(interactions=4,max_bins=64,max_rounds=700,learning_rate=.03,min_samples_leaf=8,random_state=91);evaluate('EBM',e)
joblib.dump({'version':'R4_FORMATION_PATH_VALUE_TEACHER_V1','features':features,'models':models,'actionAuthority':False,'grid':GRID},MOD)
out={'version':'R4_FORMATION_PATH_VALUE_TEACHER_V1','definition':'At fixed 60-165s remaining pre-safe checkpoints with floor<0, predict whether the market path will later reach its first floor>=0 crossing and that crossing will remain durable for 15s while retaining directional surplus. Strict-past Target ordinary BTC; 2026-08-16 excluded; future used only as teacher label.','rows':len(df),'markets':nmk,'positiveMarketRate':float(markets.market_id.map(df.groupby('market_id').y.first()).mean()),'gridSecondsLeft':GRID,'features':features,'splitMarkets':{'train':len(train),'val':len(val),'test':len(test)},'results':results,'artifacts':{'model':str(MOD.relative_to(ROOT)),'rows':str(ROWS.relative_to(ROOT))},'researchOnly':True,'actionAuthority':False}
OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
