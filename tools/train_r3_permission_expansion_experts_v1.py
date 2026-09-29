from __future__ import annotations
import json, sqlite3
from pathlib import Path
from collections import deque
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, accuracy_score
import joblib
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','event_index_norm']
HORIZON_MS=5000; MIN_SURPLUS=5.0

def fee(sh,p,role): return sh*p*0.02 if role=='TAKER' else 0.0

def build(c,mid):
 r=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
 if len(r)<4:return []
 up=down=cost=fees=0.; hist=deque(); prev=None; s=[]
 for i,(role,side,t,p,sh) in enumerate(r):
  t=int(t);p=float(p);sh=float(sh); up+=sh if side=='UP' else 0; down+=sh if side=='DOWN' else 0; cost+=p*sh; fees+=fee(sh,p,role)
  pu=up-cost-fees;pd=down-cost-fees;floor=min(pu,pd);upside=max(pu,pd);ss=abs(up-down);sur='UP' if up>down else 'DOWN' if down>up else 'FLAT';base=min(up,down);gross=up+down
  hist.append((t,role,side,sh,ss,floor,upside))
  while hist and t-hist[0][0]>15000:hist.popleft()
  r5=[x for x in hist if t-x[0]<=5000];r15=list(hist);old5=r5[0] if r5 else hist[0]
  f={'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':p,'last_shares':sh,'last_role_taker':float(role=='TAKER'),'age_since_last_ms':0. if prev is None else float(t-prev),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(sur!='FLAT' and x[2]==sur for x in r15)),'opp_side_events_15s':float(sum(sur!='FLAT' and x[2]!=sur for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(floor-old5[5]),'upside_change_5s':float(upside-old5[6]),'event_index_norm':i/max(1,len(r)-1)}
  s.append((t,sur,ss,up,down,f));prev=t
 out=[]
 for j,(t,sur,ss,up0,down0,f) in enumerate(s[:-1]):
  if sur=='FLAT' or ss<MIN_SURPLUS:continue
  k=j
  while k+1<len(s) and s[k+1][0]<=t+HORIZON_MS:k+=1
  _,_,_,up1,down1,_=s[k]; cur=up0-down0 if sur=='UP' else down0-up0; fut=up1-down1 if sur=='UP' else down1-up1; d=fut-cur
  allow=int(fut>=cur*.9); expand=int(d>=max(5.,cur*.1))
  out.append({'marketId':mid,'permission':allow,'expansion':expand,'features':f})
 return out

def metrics(m,X,y):
 p=m.predict_proba(X)[:,1];z=(p>=.5).astype(int);return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,z)),'accuracy':float(accuracy_score(y,z))}
def train(name,label,data,tr,va,te):
 def mat(ms):
  d=[x for x in data if x['marketId'] in ms];return np.array([[x['features'][f] for f in FEATURES] for x in d],float),np.array([x[label] for x in d],int)
 Xtr,ytr=mat(tr);Xv,yv=mat(va);Xt,yt=mat(te);m=HistGradientBoostingClassifier(max_iter=200,learning_rate=.055,max_leaf_nodes=15,min_samples_leaf=60,l2_regularization=2.5,random_state=20260824).fit(Xtr,ytr)
 rep={'train':metrics(m,Xtr,ytr),'validation':metrics(m,Xv,yv),'test':metrics(m,Xt,yt)};joblib.dump({'model':m,'features':FEATURES,'label':label},OUT/f'{name}.joblib');return rep

def main():
 c=sqlite3.connect(DB);mids=[x[0] for x in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")];data=[]
 for mid in mids:data.extend(build(c,mid))
 c.close();u=sorted(set(x['marketId'] for x in data));a=int(.6*len(u));b=int(.8*len(u));tr=set(u[:a]);va=set(u[a:b]);te=set(u[b:]);perm=train('r3_surplus_permission_hgb_v1','permission',data,tr,va,te);ed=[x for x in data if x['permission']==1];exp=train('r3_surplus_expansion_hgb_v1','expansion',ed,tr,va,te)
 rep={'version':'R3_PERMISSION_EXPANSION_EXPERTS_V1','markets':len(u),'rows':len(data),'teacher':{'winnerFeature':False,'lastSideShortcut':False,'permission':'5s horizon: surplus-side imbalance not reduced >10%','expansion':'conditional on permission; 5s surplus increase >= max(5 shares,10%)'},'permission':perm,'expansion':exp};(OUT/'r3_permission_expansion_experts_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
