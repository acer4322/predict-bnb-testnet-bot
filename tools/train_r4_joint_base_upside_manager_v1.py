from __future__ import annotations
import sqlite3, math, json
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, balanced_accuracy_score
from interpret.glassbox import ExplainableBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/target_wallet_official_v1.db'; OUT=ROOT/'data/research/r4_v0/hourly/r4_joint_base_upside_manager_v1.json'; MODEL=ROOT/'data/research/r4_v0/hourly/r4_joint_base_upside_manager_v1.joblib'; ROWS=ROOT/'data/research/r4_v0/hourly/r4_joint_base_upside_manager_v1_rows.csv'
EPS=1e-9; FEE_BPS=200; H=15000

def fee(sh,px): return sh*px*(1-px)*(FEE_BPS/10000.0)*4.0

def met(y,p):
 y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1])),'balAcc05':float(balanced_accuracy_score(y,p>=.5))}

c=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro&immutable=1',uri=True); c.row_factory=sqlite3.Row
res={int(r['market_id']):int(r['resolved_at_ms'] or 0) for r in c.execute("select market_id,resolved_at_ms from target_market_results where asset='BTC'")}
markets={}
for r in c.execute("select market_id,role,side,average_price,shares,first_event_ms,parent_id from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id"):
 mid=int(r['market_id']);
 if mid in res: markets.setdefault(mid,[]).append(r)
rows=[]; market_desc=[]
for mid,evs in markets.items():
 rt=res[mid]
 if rt:
  import datetime as dt
  if dt.datetime.fromtimestamp(rt/1000,ZoneInfo('Asia/Taipei')).date().isoformat()=='2026-08-16': continue
 up=dn=cost=fees=0.; tr=[]
 for i,e in enumerate(evs):
  sh=float(e['shares'] or 0); px=float(e['average_price'] or 0); role=str(e['role']); side=str(e['side']); t=int(e['first_event_ms'] or 0)
  if side=='UP': up+=sh
  elif side=='DOWN': dn+=sh
  cost+=sh*px; fees+=fee(sh,px) if role=='TAKER' else 0
  pu=up-cost-fees; pdown=dn-cost-fees
  tr.append({'i':i,'t':t,'role':role,'side':side,'price':px,'shares':sh,'up':up,'down':dn,'floor':min(pu,pdown),'upside':max(pu,pdown),'surplus':abs(up-dn),'base':min(up,dn)})
 if len(tr)<4: continue
 first=tr[0]['t']
 n_joint=n_base=n_up=0
 for j,pre in enumerate(tr[:-1]):
  sec=(rt-pre['t'])/1000 if rt else None
  if sec is None or not (60<=sec<=300): continue
  fut=[x for x in tr[j+1:] if x['t']<=pre['t']+H]
  if not fut: continue
  end=fut[-1]; surplus_side='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'; weak='DOWN' if surplus_side=='UP' else 'UP' if surplus_side=='DOWN' else 'FLAT'
  weak_flow=sum(x['shares'] for x in fut if x['side']==weak) if weak!='FLAT' else 0.; surplus_flow=sum(x['shares'] for x in fut if x['side']==surplus_side) if surplus_side!='FLAT' else 0.
  floor_prog=int(end['floor']>pre['floor']+EPS); upside_prog=int(end['upside']>pre['upside']+EPS)
  joint=int(floor_prog and upside_prog and weak_flow>0 and surplus_flow>0)
  n_joint+=joint; n_base+=floor_prog; n_up+=upside_prog
  def win(ms): return [x for x in tr if pre['t']-ms<=x['t']<=pre['t']]
  w5,w15,w30=win(5000),win(15000),win(30000)
  def shs(w,side,role=None): return sum(x['shares'] for x in w if x['side']==side and (role is None or x['role']==role)) if side!='FLAT' else 0.
  old=w5[0] if w5 else pre; gross=pre['up']+pre['down']
  rows.append({'market_id':mid,'resolved_at_ms':rt,'t':pre['t'],'seconds_left':sec,'y_joint':joint,'y_floor':floor_prog,'y_upside':upside_prog,'floor':pre['floor'],'upside':pre['upside'],'surplus':pre['surplus'],'base':pre['base'],'surplus_ratio':pre['surplus']/(gross+EPS),'floor_per_base':pre['floor']/(pre['base']+EPS),'upside_per_surplus':pre['upside']/(pre['surplus']+EPS),'last_price':pre['price'],'last_shares':pre['shares'],'last_role_taker':int(pre['role']=='TAKER'),'age_since_prev_ms':pre['t']-tr[j-1]['t'] if j>0 else 0,'events_5s':len(w5),'events_15s':len(w15),'events_30s':len(w30),'maker_events_15s':sum(x['role']=='MAKER' for x in w15),'taker_events_15s':sum(x['role']=='TAKER' for x in w15),'weak_maker_shares_5s':shs(w5,weak,'MAKER'),'weak_maker_shares_15s':shs(w15,weak,'MAKER'),'weak_maker_shares_30s':shs(w30,weak,'MAKER'),'surplus_maker_shares_15s':shs(w15,surplus_side,'MAKER'),'surplus_maker_shares_30s':shs(w30,surplus_side,'MAKER'),'weak_taker_shares_15s':shs(w15,weak,'TAKER'),'surplus_taker_shares_15s':shs(w15,surplus_side,'TAKER'),'floor_change_5s':pre['floor']-old['floor'],'upside_change_5s':pre['upside']-old['upside'],'surplus_change_5s':pre['surplus']-old['surplus'],'weak_to_surplus_maker_ratio_15s':(shs(w15,weak,'MAKER')+1)/(shs(w15,surplus_side,'MAKER')+1),'weak_to_surplus_maker_ratio_30s':(shs(w30,weak,'MAKER')+1)/(shs(w30,surplus_side,'MAKER')+1),'seconds_from_first_event':(pre['t']-first)/1000.})
 market_desc.append({'marketId':mid,'jointRows':n_joint,'floorRows':n_base,'upsideRows':n_up})
c.close()
df=pd.DataFrame(rows).sort_values(['resolved_at_ms','market_id','t']).reset_index(drop=True); ROWS.parent.mkdir(parents=True,exist_ok=True); df.to_csv(ROWS,index=False)
features=[x for x in df.columns if x not in ['market_id','resolved_at_ms','t','y_joint','y_floor','y_upside']]; X=df[features].replace([np.inf,-np.inf],np.nan).fillna(0.0); y=df.y_joint.astype(int)
ms=df.groupby('market_id').resolved_at_ms.first().sort_values().index.tolist(); cuts=[int(len(ms)*.6),int(len(ms)*.8)]; train=set(ms[:cuts[0]]); val=set(ms[cuts[0]:cuts[1]]); test=set(ms[cuts[1]:])
tr=df.market_id.isin(train); va=df.market_id.isin(val); te=df.market_id.isin(test)
models={'EBM':ExplainableBoostingClassifier(interactions=0,max_bins=128,max_rounds=600,learning_rate=.03,random_state=46),'HGB':HistGradientBoostingClassifier(max_depth=5,learning_rate=.05,max_iter=220,l2_regularization=2,random_state=46)}
results={}; fitted={}
for name,m in models.items():
 m.fit(X.loc[tr],y.loc[tr]); fitted[name]=m; results[name]={}
 for split,mask in [('train',tr),('validation',va),('test',te)]: results[name][split]=met(y.loc[mask],m.predict_proba(X.loc[mask])[:,1])
# fixed 50/50, no sweep
for split,mask in [('validation',va),('test',te)]:
 p=.5*fitted['EBM'].predict_proba(X.loc[mask])[:,1]+.5*fitted['HGB'].predict_proba(X.loc[mask])[:,1]; results.setdefault('AVG50',{})[split]=met(y.loc[mask],p)
# descriptive evidence of simultaneous formation around first durable crossing from old teacher compatible quantities
summary={'rows':int(len(df)),'markets':int(df.market_id.nunique()),'jointRate':float(y.mean()),'floorProgressRate':float(df.y_floor.mean()),'upsideProgressRate':float(df.y_upside.mean()),'jointMarkets':int(df.groupby('market_id').y_joint.max().sum()),'ordinaryOnly8_16Excluded':True}
joblib.dump({'version':'R4_JOINT_BASE_UPSIDE_MANAGER_V1','features':features,'EBM':fitted['EBM'],'HGB':fitted['HGB'],'actionAuthority':False},MODEL)
out={'version':'R4_JOINT_BASE_UPSIDE_MANAGER_V1','researchOnly':True,'actionAuthority':False,'definition':'Strict-past 60-300s Target checkpoint predicts whether the next 15s ends with BOTH floor and upside improved while both weak-side and current-surplus-side flow occur. This encodes concurrent safe-base construction plus favorable asymmetry expansion, not sequential base-then-upside.','summary':summary,'features':features,'results':results,'marketSplit':{'train':len(train),'validation':len(val),'test':len(test)},'artifacts':{'model':str(MODEL.relative_to(ROOT)).replace('\\','/'),'rows':str(ROWS.relative_to(ROOT)).replace('\\','/')}}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2))
