from __future__ import annotations
import sqlite3, json, math
from pathlib import Path
from collections import deque
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data/research/r3_v0/r3_target_post_add_recovery_v1_report.json'
con=sqlite3.connect(DB)
rows=con.execute("select market_id,role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' order by market_id,first_event_ms,parent_id").fetchall()
by={}
for r in rows: by.setdefault(int(r[0]),[]).append(r[1:])
X=[]; y=[]; mids=[]; kinds=[]
for mid,evs in by.items():
 up=down=cost=fees=0.; hist=[]
 states=[]
 for i,(role,side,t,px,sh) in enumerate(evs):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh)
  pre_net=up-down; pre_floor=min(up-cost-fees,down-cost-fees); pre_up=max(up-cost-fees,down-cost-fees)
  if side=='UP': up+=sh
  else: down+=sh
  cost+=px*sh; fees += sh*px*.02 if role=='TAKER' else 0.
  post_floor=min(up-cost-fees,down-cost-fees); post_up=max(up-cost-fees,down-cost-fees); post_net=up-down
  effect=None
  if role=='TAKER' and abs(pre_net)>1e-9:
   effect='ADD' if (pre_net>0 and side=='UP') or (pre_net<0 and side=='DOWN') else 'REPAIR'
  states.append(dict(i=i,t=t,role=role,side=side,shares=sh,preNet=pre_net,postNet=post_net,preFloor=pre_floor,floor=post_floor,upside=post_up,effect=effect))
 for j,s in enumerate(states):
  if s['effect']!='ADD': continue
  # look forward 15s after ADD, label which channel first recovers at least 50% of floor spent
  spent=max(0.,s['preFloor']-s['floor']); target=s['floor']+0.5*spent
  if spent<=1e-6: continue
  lab=None
  for z in states[j+1:]:
   if z['t']-s['t']>15000: break
   if z['floor']>=target:
    lab='REPAIR' if z['effect']=='REPAIR' else ('MAKER' if z['role']=='MAKER' else 'OTHER'); break
  if lab not in {'MAKER','REPAIR'}: continue
  # simple strict-past state at ADD time
  gross=abs(s['postNet'])+2*min(up,down) # scale proxy only
  feats=[s['preFloor'],s['floor'],s['upside'],abs(s['postNet']),spent,spent/max(abs(s['preFloor'])+1e-6,1.),float(s['shares']),j/max(1,len(states)-1)]
  X.append(feats); y.append(1 if lab=='REPAIR' else 0); mids.append(mid); kinds.append(lab)
con.close()
X=np.asarray(X,float); y=np.asarray(y,int); uniq=sorted(set(mids)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); splits={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}
tr=np.asarray([i for i,m in enumerate(mids) if m in splits['train']],int); model=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=40,l2_regularization=2.,random_state=20260825).fit(X[tr],y[tr]); out={}
for nm,ss in splits.items():
 ix=np.asarray([i for i,m in enumerate(mids) if m in ss],int); p=model.predict_proba(X[ix])[:,1]; pred=(p>=.5).astype(int); out[nm]={'n':int(len(ix)),'repairFirstRate':float(y[ix].mean()) if len(ix) else None,'auc':float(roc_auc_score(y[ix],p)) if len(set(y[ix]))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y[ix],pred)) if len(ix) else None}
rep={'version':'R3_TARGET_POST_ADD_RECOVERY_V1','researchOnly':True,'markets':len(uniq),'rows':len(y),'label':'Within 15s after ADD, first channel recovering >=50% of floor spent: REPAIR vs MAKER','features':['preFloor','postAddFloor','postAddUpside','postAddAbsNet','floorSpent','spentVsPreFloorScale','addShares','eventProgress'],'splits':out}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
