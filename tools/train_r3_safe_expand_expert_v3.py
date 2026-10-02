from __future__ import annotations
import json,sqlite3,math
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']
def fee(sh,px,role): return sh*px*.02 if role=='TAKER' else 0.0
def build(c,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
 if len(rr)<4:return []
 up=down=cost=fees=0.; hist=deque(); prev_t=None; snaps=[]
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
  if side=='UP': up+=sh
  else: down+=sh
  cost+=px*sh; fees+=fee(sh,px,role); pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'
  hist.append((t,role,side,sh,ss,fl,ups))
  while hist and t-hist[0][0]>15000: hist.popleft()
  r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old5=r5[0] if r5 else hist[0]
  f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
  snaps.append((t,ss,fl,ups,surplus,f)); prev_t=t
 out=[]
 for i,(t,ss,fl,ups,surplus,f) in enumerate(snaps):
  if fl<0 or surplus=='FLAT' or ss<5: continue
  j=i+1
  while j<len(snaps) and snaps[j][0]-t<=5000: j+=1
  if j<=i+1: continue
  fut=snaps[i+1:j]; maxss=max(x[1] for x in fut); minfl=min(x[2] for x in fut); maxups=max(x[3] for x in fut)
  expand=maxss-ss>=max(5.,.1*ss)
  safe=minfl>=0
  label=int(expand and safe)
  out.append((mid,[f[k] for k in FEATURES],label,fl,ups,maxss-ss,minfl,maxups-ups))
 return out

def score(m,X,y):
 p=m.predict_proba(X)[:,1]; return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,(p>=.5).astype(int)))}
def main():
 c=sqlite3.connect(DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
 for mid in mids:data.extend(build(c,mid))
 c.close(); uniq=sorted(set(x[0] for x in data)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); sets=[set(uniq[:a]),set(uniq[a:b]),set(uniq[b:])]
 mats=[]
 for ms in sets:
  dd=[x for x in data if x[0] in ms]; mats.append((np.asarray([x[1] for x in dd],float),np.asarray([x[2] for x in dd],int)))
 Xtr,ytr=mats[0]; model=HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=2.,random_state=20260824).fit(Xtr,ytr)
 rep={'version':'R3_SAFE_EXPAND_HGB_V3','teacher':'Only checkpoints with current floor>=0. Positive iff within 5s surplus expands >=max(5sh,10%) and floor never drops below 0. Winner excluded; no last-side shortcut.','markets':len(uniq),'rows':len(data),'features':FEATURES,'train':score(model,*mats[0]),'validation':score(model,*mats[1]),'test':score(model,*mats[2])}
 joblib.dump({'model':model,'features':FEATURES,'teacherVersion':'R3_SAFE_EXPAND_V3'},OUT/'r3_safe_expand_hgb_v3.joblib'); (OUT/'r3_safe_expand_hgb_v3_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
