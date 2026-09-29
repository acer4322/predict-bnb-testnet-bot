from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm','action_same_as_surplus','action_side_up']
def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.
def vals(up,down,cost,fees,hist,t,role,side,px,sh,i,n):
 gross=up+down; net=up-down; ss=abs(net); base=min(up,down); floor=min(up-cost-fees,down-cost-fees); ups=max(up-cost-fees,down-cost-fees); surplus='UP' if net>0 else 'DOWN' if net<0 else 'FLAT'; r5=[x for x in hist if t-x[0]<=5000]; r15=[x for x in hist if t-x[0]<=15000]; old5=r5[0] if r5 else (hist[0] if hist else (t,role,side,sh,ss,floor,ups))
 return {'floor':floor,'upside':ups,'upside_gap':ups-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'age_since_last_ms':0. if len(hist)<2 else float(t-hist[-2][0]),'events_5s':len(r5),'events_15s':len(r15),'maker_events_15s':sum(x[1]=='MAKER' for x in r15),'taker_events_15s':sum(x[1]=='TAKER' for x in r15),'same_side_events_15s':sum(surplus!='FLAT' and x[2]==surplus for x in r15),'opp_side_events_15s':sum(surplus!='FLAT' and x[2]!=surplus for x in r15),'same_side_shares_15s':sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus),'opp_side_shares_15s':sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus),'surplus_change_5s':ss-old5[4],'floor_change_5s':floor-old5[5],'upside_change_5s':ups-old5[6],'floor_to_upside_ratio':floor/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':floor/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,n-1),'action_same_as_surplus':1. if side==surplus else 0.,'action_side_up':1. if side=='UP' else 0.}
con=sqlite3.connect(DB); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
for mid in mids:
 rr=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); up=down=cost=fees=0.; hist=deque(); N=len(rr)
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
  if hist and role=='TAKER' and abs(up-down)>1e-6:
   f=vals(up,down,cost,fees,hist,t,role,side,px,sh,i,N); eff='ADD' if f['action_same_as_surplus']>.5 else 'REPAIR'; data.append((mid,eff,[f[k] for k in FEATURES],math.log1p(sh),sh))
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh; fees+=fee(sh,px,role); hist.append((t,role,side,sh,abs(up-down),min(up-cost-fees,down-cost-fees),max(up-cost-fees,down-cost-fees)))
  while hist and t-hist[0][0]>15000: hist.popleft()
con.close(); uniq=sorted(set(m for m,_,_,_,_ in data)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); sets={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}
mods={}; rep={'version':'R3_TARGET_ACTIVE_RAW_QUANTITY_BIG_V6','researchOnly':True,'fixed18Forbidden':True,'markets':len(uniq),'events':len(data),'features':FEATURES,'effects':{}}
for eff in ['REPAIR','ADD']:
 dd=[x for x in data if x[1]==eff]; X=np.asarray([x[2] for x in dd],float); y=np.asarray([x[3] for x in dd],float); sh=np.asarray([x[4] for x in dd],float); mids2=[x[0] for x in dd]; tr=np.asarray([i for i,m in enumerate(mids2) if m in sets['train']],int)
 model=HistGradientBoostingRegressor(max_iter=320,learning_rate=.045,max_leaf_nodes=25,min_samples_leaf=45,l2_regularization=3.,random_state=20260825).fit(X[tr],y[tr]); mods[eff]=model; out={}; med=float(np.median(sh[tr]))
 for nm,ms in sets.items():
  ix=np.asarray([i for i,m in enumerate(mids2) if m in ms],int); pred=np.expm1(model.predict(X[ix])); pred=np.clip(pred,.01,250.); yy=sh[ix]; out[nm]={'n':int(len(ix)),'maeShares':float(mean_absolute_error(yy,pred)),'medianBaselineShares':med,'baselineMaeShares':float(mean_absolute_error(yy,np.full_like(yy,med))),'lift':float(mean_absolute_error(yy,np.full_like(yy,med))-mean_absolute_error(yy,pred)),'labelMedian':float(np.median(yy)),'predMedian':float(np.median(pred)),'labelP90':float(np.quantile(yy,.9)),'predP90':float(np.quantile(pred,.9))}
 rep['effects'][eff]=out
joblib.dump({'features':FEATURES,'repairModel':mods['REPAIR'],'addModel':mods['ADD'],'version':rep['version']},D/'r3_target_active_raw_quantity_hgb_v6.joblib'); (D/'r3_target_active_raw_quantity_big_v6_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
