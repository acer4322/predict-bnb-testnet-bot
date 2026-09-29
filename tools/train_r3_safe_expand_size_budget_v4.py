from __future__ import annotations
import json, sqlite3
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'target_wallet_official_v1.db'; OUTDIR=ROOT/'data'/'research'/'r3_v0'; OUTDIR.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']
def fee(sh,px,role): return sh*px*0.02 if str(role).upper()=='TAKER' else 0.0
def build(c,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall();
 if len(rr)<4:return []
 up=down=cost=fees=0.; hist=deque(); prev_t=None; states=[]
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh); up+=sh if side=='UP' else 0.; down+=sh if side=='DOWN' else 0.; cost+=px*sh;fees+=fee(sh,px,role)
  pu=up-cost-fees;pd=down-cost-fees;floor=min(pu,pd);upside=max(pu,pd);surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT';ss=abs(up-down);base=min(up,down);gross=up+down
  hist.append((t,role,side,sh,ss,floor,upside));
  while hist and t-hist[0][0]>15000:hist.popleft()
  r5=[x for x in hist if t-x[0]<=5000];r15=list(hist);old5=r5[0] if r5 else hist[0]
  feat={'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(floor-old5[5]),'upside_change_5s':float(upside-old5[6]),'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.,'floor_per_base_share':floor/base if base>1e-9 else 0.,'upside_per_surplus_share':upside/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)};states.append((i,t,surplus,ss,floor,upside,feat));prev_t=t
 out=[]
 for j,(i,t,surplus,ss,floor,upside,feat) in enumerate(states):
  if surplus=='FLAT' or ss<5 or floor<0:continue
  fut=[s for s in states[j+1:] if s[1]-t<=5000]
  if not fut:continue
  same=[s for s in fut if s[2]==surplus]
  if not same:continue
  expand=max(s[3] for s in same)-ss;min_floor=min(s[4] for s in fut)
  if expand>=max(5.,0.10*ss) and min_floor>=0:out.append({'marketId':mid,'features':feat,'targetExpandShares':float(expand),'targetFloorSpend':float(max(0.,floor-min_floor))})
 return out
def score(model,X,y):
 p=model.predict(X);ae=np.abs(p-y);return {'n':len(y),'mae':float(mean_absolute_error(y,p)),'medianAbsErr':float(np.median(ae)),'targetMedian':float(np.median(y)),'predMedian':float(np.median(p)),'targetP90':float(np.quantile(y,.9)),'predP90':float(np.quantile(p,.9))}
def main():
 c=sqlite3.connect(DB);mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")];data=[]
 for mid in mids:data.extend(build(c,mid))
 c.close();uniq=sorted(set(d['marketId'] for d in data));n=len(uniq);a=int(.6*n);b=int(.8*n);parts=[set(uniq[:a]),set(uniq[a:b]),set(uniq[b:])]
 def mat(ms):
  dd=[d for d in data if d['marketId'] in ms];X=np.asarray([[d['features'][f] for f in FEATURES] for d in dd],float);return X,np.asarray([d['targetExpandShares'] for d in dd]),np.asarray([d['targetFloorSpend'] for d in dd])
 tr=mat(parts[0]);va=mat(parts[1]);te=mat(parts[2]);kw=dict(max_iter=220,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=3.0,random_state=20260824);m1=HistGradientBoostingRegressor(**kw).fit(tr[0],tr[1]);m2=HistGradientBoostingRegressor(**kw).fit(tr[0],tr[2])
 rep={'version':'R3_SAFE_EXPAND_SIZE_BUDGET_V4','markets':len(uniq),'rows':len(data),'expansionSize':{'train':score(m1,tr[0],tr[1]),'validation':score(m1,va[0],va[1]),'test':score(m1,te[0],te[1])},'floorSpend':{'train':score(m2,tr[0],tr[2]),'validation':score(m2,va[0],va[2]),'test':score(m2,te[0],te[2])}}
 joblib.dump({'model':m1,'features':FEATURES},OUTDIR/'r3_safe_expand_size_hgb_v4.joblib');joblib.dump({'model':m2,'features':FEATURES},OUTDIR/'r3_safe_expand_floor_budget_hgb_v4.joblib');(OUTDIR/'r3_safe_expand_size_budget_v4_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
