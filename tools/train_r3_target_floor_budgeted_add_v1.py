from __future__ import annotations
import sqlite3,json,math
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data/target_wallet_official_v1.db'; D=ROOT/'data/research/r3_v0'
# Strict-past Target state. Label ADD only if Taker same-side-as-surplus; quantify floor spent per upside gained.
FEATS=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']
def fee(sh,px,r): return sh*px*.02 if r=='TAKER' else 0.
def state(up,dn,cost,fees,hist,t,i,n):
 g=up+dn; net=up-dn; ss=abs(net); base=min(up,dn); fl=min(up-cost-fees,dn-cost-fees); ups=max(up-cost-fees,dn-cost-fees); sur='UP' if net>0 else 'DOWN' if net<0 else 'FLAT'; r5=[x for x in hist if t-x[0]<=5000]; r15=[x for x in hist if t-x[0]<=15000]; old=r5[0] if r5 else (hist[0] if hist else (t,'', '',0,ss,fl,ups))
 return {'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/g if g else 0.,'cost_per_gross_share':(cost+fees)/g if g else 0.,'age_since_last_ms':float(t-hist[-1][0]) if hist else 1e6,'events_5s':len(r5),'events_15s':len(r15),'maker_events_15s':sum(x[1]=='MAKER' for x in r15),'taker_events_15s':sum(x[1]=='TAKER' for x in r15),'same_side_events_15s':sum(sur!='FLAT' and x[2]==sur for x in r15),'opp_side_events_15s':sum(sur!='FLAT' and x[2]!=sur for x in r15),'same_side_shares_15s':sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur),'opp_side_shares_15s':sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur),'surplus_change_5s':ss-old[4],'floor_change_5s':fl-old[5],'upside_change_5s':ups-old[6],'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,n-1)}
con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro&immutable=1',uri=True); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); up=dn=cost=fees=0.; hist=deque(); n=len(ev)
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh); s=state(up,dn,cost,fees,hist,t,i,n); pre_net=up-dn; sur='UP' if pre_net>0 else 'DOWN' if pre_net<0 else 'FLAT'; prefl=s['floor']; preup=s['upside']
  # candidate action opportunities are actual parent events; channel/when handled elsewhere. Here learn ADD permission/intensity conditional on a Taker opportunity.
  is_add=bool(role=='TAKER' and sur!='FLAT' and side==sur)
  if role=='TAKER' and sur!='FLAT':
   up2=up+(sh if side=='UP' else 0); dn2=dn+(sh if side=='DOWN' else 0); c2=cost+sh*px; f2=fees+fee(sh,px,role); postfl=min(up2-c2-f2,dn2-c2-f2); postup=max(up2-c2-f2,dn2-c2-f2); spend=max(0.,prefl-postfl); gain=max(0.,postup-preup); budget_ratio=spend/max(abs(prefl)+5.,5.); rows.append((mid,[s[k] for k in FEATS],int(is_add),budget_ratio,sh,spend,gain,prefl,preup))
  if side=='UP':up+=sh
  else:dn+=sh
  cost+=sh*px;fees+=fee(sh,px,role);hist.append((t,role,side,sh,abs(up-dn),min(up-cost-fees,dn-cost-fees),max(up-cost-fees,dn-cost-fees)))
  while hist and t-hist[0][0]>15000:hist.popleft()
con.close(); uniq=sorted(set(x[0] for x in rows)); a=int(.6*len(uniq));b=int(.8*len(uniq)); sets={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}; X=np.asarray([r[1] for r in rows],float); y=np.asarray([r[2] for r in rows],int); ms=[r[0] for r in rows]
tr=np.asarray([i for i,m in enumerate(ms) if m in sets['train']],int); clf=HistGradientBoostingClassifier(max_iter=240,learning_rate=.05,max_leaf_nodes=25,min_samples_leaf=80,l2_regularization=3.,class_weight='balanced',random_state=20260825).fit(X[tr],y[tr])
# budget model only ADD rows, log1p budget ratio
add_idx=np.asarray([i for i,r in enumerate(rows) if r[2]==1],int); Xa=X[add_idx]; ya=np.log1p(np.asarray([rows[i][3] for i in add_idx],float)); ma=[ms[i] for i in add_idx]; tra=np.asarray([j for j,m in enumerate(ma) if m in sets['train']],int); reg=HistGradientBoostingRegressor(max_iter=240,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=55,l2_regularization=3.,random_state=20260825).fit(Xa[tra],ya[tra])
rep={'version':'R3_TARGET_FLOOR_BUDGETED_ADD_V1','researchOnly':True,'fixed18Forbidden':True,'markets':len(uniq),'takerRows':len(rows),'addRows':int(y.sum()),'features':FEATS,'splits':{}}
for nm,sset in sets.items():
 ix=np.asarray([i for i,m in enumerate(ms) if m in sset],int); p=clf.predict_proba(X[ix])[:,1]; yy=y[ix]; d={'n':len(ix),'addRate':float(yy.mean()),'auc':float(roc_auc_score(yy,p)) if len(set(yy))>1 else None}
 ia=np.asarray([j for j,m in enumerate(ma) if m in sset],int)
 if len(ia):
  pred=np.expm1(reg.predict(Xa[ia])); actual=np.expm1(ya[ia]); d['budget']={'n':len(ia),'mae':float(mean_absolute_error(actual,pred)),'actualMedian':float(np.median(actual)),'predMedian':float(np.median(pred)),'actualP90':float(np.quantile(actual,.9)),'predP90':float(np.quantile(pred,.9))}
 rep['splits'][nm]=d
joblib.dump({'features':FEATS,'addPermissionModel':clf,'budgetRatioModel':reg,'version':rep['version']},D/'r3_target_floor_budgeted_add_v1.joblib');(D/'r3_target_floor_budgeted_add_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
