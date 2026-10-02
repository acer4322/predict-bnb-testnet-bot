from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, precision_recall_fscore_support

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']
def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.0

def state(up,down,cost,fees,hist,prev_t,t,i,n):
 pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'
 while hist and t-hist[0][0]>15000: hist.popleft()
 r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old5=r5[0] if r5 else (hist[0] if hist else (t,'', '',0,ss,fl,ups))
 f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,n-1)}
 return f, surplus, fl, ss

def build_market(c,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
 if len(rr)<6:return []
 up=down=cost=fees=0.; hist=deque(); prev_t=None; out=[]
 # Predict the NEXT parent-order channel from state strictly before that order.
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role).upper(); side=str(side).upper(); t=int(t); px=float(px); sh=float(sh)
  if i>0:
   f,surplus,fl,ss=state(up,down,cost,fees,hist,prev_t,t,i,len(rr))
   # channel target: whether the next Target parent action is active Taker rather than Maker.
   y=int(role=='TAKER')
   out.append((mid,t,[float(f[k]) for k in FEATURES],y,role,side,sh,surplus,fl,ss))
  # consume current event after label construction
  if side=='UP': up+=sh
  else: down+=sh
  cost+=px*sh; fees+=fee(sh,px,role)
  pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down)
  hist.append((t,role,side,sh,ss,fl,ups)); prev_t=t
 return out

def metrics(model,X,y):
 p=model.predict_proba(X)[:,1]; pred=(p>=.5).astype(int)
 pr,rc,f1,_=precision_recall_fscore_support(y,pred,average='binary',zero_division=0)
 return {'n':int(len(y)),'positiveRate':float(np.mean(y)),'predictedRate':float(np.mean(pred)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'precision':float(pr),'recall':float(rc),'f1':float(f1)}

def main():
 c=sqlite3.connect(DB); mids=[int(r[0]) for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
 for mid in mids:data.extend(build_market(c,mid))
 c.close(); uniq=sorted(set(x[0] for x in data)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); splits={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}
 mats={}
 for n,ms in splits.items():
  d=[x for x in data if x[0] in ms]; mats[n]=(d,np.asarray([x[2] for x in d],float),np.asarray([x[3] for x in d],int))
 model=HistGradientBoostingClassifier(max_iter=350,learning_rate=.045,max_leaf_nodes=31,min_samples_leaf=60,l2_regularization=3.0,class_weight='balanced',random_state=20260825).fit(mats['train'][1],mats['train'][2])
 joblib.dump({'model':model,'features':FEATURES,'teacherVersion':'R3_TARGET_ACTIVE_HIERARCHY_BIG_V3','meaning':'At next Target parent-order opportunity, choose TAKER vs MAKER. Fixed-18 forbidden.'},OUT/'r3_target_active_channel_hgb_v3.joblib')
 rep={'version':'R3_TARGET_ACTIVE_HIERARCHY_BIG_V3','researchOnly':True,'fixed18Forbidden':True,'fullGapForbidden':True,'teacherSemantics':'Strict-past Target state immediately before each next parent order; classify next role TAKER vs MAKER. This is channel conditional on an observed Target action opportunity, not a continuous-time ACT hazard.','markets':len(uniq),'rows':len(data),'features':FEATURES,'splits':{n:metrics(model,X,y) for n,(d,X,y) in mats.items()}}
 (OUT/'r3_target_active_hierarchy_big_v3_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
