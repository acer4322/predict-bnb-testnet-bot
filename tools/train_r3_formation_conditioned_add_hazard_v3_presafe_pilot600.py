from __future__ import annotations
import json, sqlite3
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']
def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.0
def build_market(c,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
 if len(rr)<4:return []
 up=down=cost=fees=0.;hist=deque();prev_t=None;snaps=[];first_safe=None
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh)
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh;fees+=fee(sh,px,role);pu=up-cost-fees;pd=down-cost-fees;fl=min(pu,pd);ups=max(pu,pd);ss=abs(up-down);base=min(up,down);gross=up+down;surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'
  if first_safe is None and fl>=0:first_safe=i
  hist.append((t,role,side,sh,ss,fl,ups))
  while hist and t-hist[0][0]>15000:hist.popleft()
  r5=[x for x in hist if t-x[0]<=5000];r15=list(hist);old5=r5[0] if r5 else hist[0]
  f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
  snaps.append((t,fl,ss,surplus,f));prev_t=t
 limit=first_safe if first_safe is not None else len(snaps);out=[]
 for i in range(limit):
  t,fl,ss,surplus,f=snaps[i]
  if fl>=0 or surplus=='FLAT' or ss<5:continue
  j=i+1
  while j<len(snaps) and snaps[j][0]-t<=5000:j+=1
  if j<=i+1:continue
  fut=snaps[i+1:j];last=fut[-1];future_floors=[x[1] for x in fut];contraction=ss-last[2];thr=max(5.,.10*ss)
  
  # structural ADD within 5s: Taker on current surplus side.
  y_add=0
  for q in rr[i+1:min(len(rr),j+1)]:
   if int(q[2])-t>5000: break
   if str(q[0])=='TAKER' and str(q[1])==surplus: y_add=1; break
  out.append((mid,[f[k] for k in FEATURES],y_add))
 return out
def score(m,X,y):
 p=m.predict_proba(X)[:,1];return {'n':len(y),'positiveRate':float(y.mean()),'predictedRate':float((p>=.5).mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,(p>=.5).astype(int)))}
def main():
 c=sqlite3.connect(DB);mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][-600:];data=[]
 for mid in mids:data.extend(build_market(c,mid))
 c.close();uniq=sorted(set(x[0] for x in data));a=int(.6*len(uniq));b=int(.8*len(uniq));sets=[set(uniq[:a]),set(uniq[a:b]),set(uniq[b:])];mats=[]
 for ms in sets:
  dd=[x for x in data if x[0] in ms];mats.append((np.asarray([x[1] for x in dd],float),np.asarray([x[2] for x in dd],int)))
 m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=55,l2_regularization=2.5,class_weight='balanced',random_state=20260825).fit(*mats[0])
 rep={'version':'R3_FORMATION_CONDITIONED_ADD_HAZARD_V3_PRE_SAFE_PILOT600','teacher':'PRE_SAFE Formation checkpoints; label structural Target ADD within next 5s on current surplus side; winner excluded; fixed18 irrelevant/forbidden','markets':len(uniq),'rows':len(data),'features':FEATURES,'train':score(m,*mats[0]),'validation':score(m,*mats[1]),'test':score(m,*mats[2])}
 joblib.dump({'model':m,'features':FEATURES,'version':rep['version']},OUT/'r3_formation_conditioned_add_hazard_v3_presafe_pilot600.joblib');(OUT/'r3_formation_conditioned_add_hazard_v3_presafe_pilot600_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
