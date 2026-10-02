from __future__ import annotations
import sqlite3,json,math
from pathlib import Path
import sys
from collections import deque
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'
SEQ=joblib.load(D/'r3_formation_sequence_hgb_v2.joblib'); SF=SEQ['features']; SM=SEQ['model']; CL=list(SM.classes_)
BASE=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']
FEATS=BASE+['formation_allow_p','formation_build_p','formation_cross_p']
def fee(sh,px,r):return sh*px*.02 if r=='TAKER' else 0.
def state(up,dn,cost,fees,hist,t,i,n):
 g=up+dn;net=up-dn;ss=abs(net);base=min(up,dn);fl=min(up-cost-fees,dn-cost-fees);ups=max(up-cost-fees,dn-cost-fees);sur='UP' if net>0 else 'DOWN' if net<0 else 'FLAT';r5=[x for x in hist if t-x[0]<=5000];r15=[x for x in hist if t-x[0]<=15000];old=r5[0] if r5 else (hist[0] if hist else (t,'','',0,.5,ss,fl,ups));return {'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/g if g else 0.,'cost_per_gross_share':(cost+fees)/g if g else 0.,'last_price':hist[-1][4] if hist else .5,'last_shares':hist[-1][3] if hist else 0.,'last_role_taker':1. if hist and hist[-1][1]=='TAKER' else 0.,'age_since_last_ms':t-hist[-1][0] if hist else 1e6,'events_5s':len(r5),'events_15s':len(r15),'maker_events_15s':sum(x[1]=='MAKER' for x in r15),'taker_events_15s':sum(x[1]=='TAKER' for x in r15),'same_side_events_15s':sum(sur!='FLAT' and x[2]==sur for x in r15),'opp_side_events_15s':sum(sur!='FLAT' and x[2]!=sur for x in r15),'same_side_shares_15s':sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur),'opp_side_shares_15s':sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur),'surplus_change_5s':ss-old[5],'floor_change_5s':fl-old[6],'upside_change_5s':ups-old[7],'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,n-1)},sur
con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro&immutable=1',uri=True); all_mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; shard=int(sys.argv[1]); nsh=int(sys.argv[2]); lo=(len(all_mids)*shard)//nsh; hi=(len(all_mids)*(shard+1))//nsh; mids=all_mids[lo:hi]; rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); n=len(ev);up=dn=cost=fees=0.;hist=deque(); base_rows=[]; surs=[]; times=[]
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh);z,sur=state(up,dn,cost,fees,hist,t,i,n);base_rows.append(z);surs.append(sur);times.append(t)
  if side=='UP':up+=sh
  else:dn+=sh
  cost+=sh*px;fees+=fee(sh,px,role);hist.append((t,role,side,sh,px,abs(up-dn),min(up-cost-fees,dn-cost-fees),max(up-cost-fees,dn-cost-fees)))
  while hist and t-hist[0][0]>15000:hist.popleft()
 if not base_rows: continue
 XF=np.asarray([[z.get(k,0.) for k in SF] for z in base_rows],float); P=SM.predict_proba(XF); cmap={str(c):j for j,c in enumerate(CL)}
 # Precompute structural ADD event indices based on each checkpoint's current surplus side.
 for i,z in enumerate(base_rows):
  z['formation_allow_p']=float(P[i,cmap.get('ALLOW_ASYMMETRY',0)]) if 'ALLOW_ASYMMETRY' in cmap else 0.;z['formation_build_p']=float(P[i,cmap.get('BUILD_WEAK_SIDE',0)]) if 'BUILD_WEAK_SIDE' in cmap else 0.;z['formation_cross_p']=float(P[i,cmap.get('CROSS_SAFE',0)]) if 'CROSS_SAFE' in cmap else 0.
  sur=surs[i]; y=0
  if sur!='FLAT':
   for j in range(i,n):
    if times[j]-times[i]>5000:break
    if str(ev[j][0])=='TAKER' and str(ev[j][1])==sur: y=1;break
  rows.append((mid,[z[k] for k in FEATS],y))

con.close()
import pickle
out=D/f'r3_formation_conditioned_add_rows_shard_{shard}_of_{nsh}.pkl'
with out.open('wb') as f: pickle.dump({'rows':rows,'features':FEATS,'markets':mids},f,protocol=pickle.HIGHEST_PROTOCOL)
print(json.dumps({'shard':shard,'nshards':nsh,'markets':len(mids),'rows':len(rows),'out':str(out)},indent=2))
