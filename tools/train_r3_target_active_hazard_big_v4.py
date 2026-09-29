from __future__ import annotations
import json, sqlite3
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, average_precision_score, precision_recall_fscore_support
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']
def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.0
def build(c,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); n=len(rr)
 if n<6:return []
 up=down=cost=fees=0.; hist=deque(); prev_t=None; states=[]
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role).upper(); side=str(side).upper(); t=int(t); px=float(px); sh=float(sh)
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh; fees+=fee(sh,px,role); pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'
  hist.append((t,role,side,sh,ss,fl,ups));
  while hist and t-hist[0][0]>15000:hist.popleft()
  r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old5=r5[0] if r5 else hist[0]
  f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,n-1)}
  states.append((mid,t,[float(f[k]) for k in FEATURES])); prev_t=t
 # Strict future label only for training: any Taker in (t,t+5s].
 takers=[int(r[2]) for r in rr if str(r[0]).upper()=='TAKER']
 out=[]; j=0
 for mid,t,x in states:
  while j<len(takers) and takers[j]<=t:j+=1
  y=int(j<len(takers) and takers[j]-t<=5000); out.append((mid,t,x,y))
 return out
def score(m,X,y):
 p=m.predict_proba(X)[:,1]; pred=(p>=.5).astype(int); pr,rc,f1,_=precision_recall_fscore_support(y,pred,average='binary',zero_division=0)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'predictedRate':float(pred.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(np.unique(y))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'precision':float(pr),'recall':float(rc),'f1':float(f1)}
def main():
 c=sqlite3.connect(DB); mids=[int(r[0]) for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
 for mid in mids:data.extend(build(c,mid))
 c.close(); uniq=sorted(set(x[0] for x in data)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); ss={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}; mats={}
 for n,ms in ss.items():
  d=[x for x in data if x[0] in ms]; mats[n]=(np.asarray([x[2] for x in d],float),np.asarray([x[3] for x in d],int))
 m=HistGradientBoostingClassifier(max_iter=450,learning_rate=.035,max_leaf_nodes=31,min_samples_leaf=70,l2_regularization=3.5,class_weight='balanced',random_state=20260825).fit(*mats['train'])
 joblib.dump({'model':m,'features':FEATURES,'teacherVersion':'R3_TARGET_ACTIVE_HAZARD_BIG_V4','horizonMs':5000},OUT/'r3_target_active_hazard_5s_hgb_v4.joblib')
 rep={'version':'R3_TARGET_ACTIVE_HAZARD_BIG_V4','researchOnly':True,'fixed18Forbidden':True,'teacher':'Strict-past state AFTER each Target parent event; label any future Taker in next 5s. Future label training-only.','markets':len(uniq),'rows':len(data),'horizonMs':5000,'features':FEATURES,'splits':{n:score(m,*xy) for n,xy in mats.items()}}
 (OUT/'r3_target_active_hazard_big_v4_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
