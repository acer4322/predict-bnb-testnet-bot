from __future__ import annotations
import sqlite3,json,math
from collections import deque
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,precision_score,recall_score
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'
FEATURES=['floor','upside','abs_net','gross','paired_coverage','imbalance_ratio','surplus_ratio','cost_per_gross','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','floor_change_5s','upside_change_5s','absnet_change_5s','event_index_norm']
def safe_auc(y,p):
 return float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None
def score(m,X,y):
 p=m.predict_proba(X)[:,1]; pred=p>=.5
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'predictedRate':float(pred.mean()),'auc':safe_auc(y,p),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'precision':float(precision_score(y,pred,zero_division=0)),'recall':float(recall_score(y,pred,zero_division=0))}
con=sqlite3.connect(DB); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][-500:]; rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); n=len(ev); up=dn=cost=fees=0.; hist=deque(); snaps=[]; prev=None
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh); pre_net=up-dn; pre_floor=min(up-cost-fees,dn-cost-fees)
  if side=='UP':up+=sh
  else:dn+=sh
  cost+=px*sh; fees+=px*sh*.02 if role=='TAKER' else 0.; pu,pd=up-cost-fees,dn-cost-fees; fl=min(pu,pd); ups=max(pu,pd); net=up-dn; gross=up+dn; ab=abs(net); pair=2*min(up,dn)/gross if gross else 0.; imb=ab/gross if gross else 0.; surplus='UP' if net>0 else 'DOWN' if net<0 else 'FLAT'
  hist.append((t,role,side,sh,fl,ups,ab));
  while hist and t-hist[0][0]>15000:hist.popleft()
  q5=[z for z in hist if t-z[0]<=5000]; q15=list(hist); old=q5[0] if q5 else q15[0]
  f={'floor':fl,'upside':ups,'abs_net':ab,'gross':gross,'paired_coverage':pair,'imbalance_ratio':imb,'surplus_ratio':ab/gross if gross else 0.,'cost_per_gross':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':float(role=='TAKER'),'age_since_last_ms':0. if prev is None else t-prev,'events_5s':len(q5),'events_15s':len(q15),'maker_events_15s':sum(z[1]=='MAKER' for z in q15),'taker_events_15s':sum(z[1]=='TAKER' for z in q15),'same_side_events_15s':sum(surplus!='FLAT' and z[2]==surplus for z in q15),'opp_side_events_15s':sum(surplus!='FLAT' and z[2]!=surplus for z in q15),'same_side_shares_15s':sum(z[3] for z in q15 if surplus!='FLAT' and z[2]==surplus),'opp_side_shares_15s':sum(z[3] for z in q15 if surplus!='FLAT' and z[2]!=surplus),'floor_change_5s':fl-old[4],'upside_change_5s':ups-old[5],'absnet_change_5s':ab-old[6],'event_index_norm':i/max(1,n-1)}
  eff=None
  if role=='TAKER' and abs(pre_net)>1e-9: eff='ADD' if ((pre_net>0 and side=='UP') or (pre_net<0 and side=='DOWN')) else 'REPAIR'
  snaps.append({'t':t,'role':role,'side':side,'sh':sh,'preFloor':pre_floor,'floor':fl,'upside':ups,'absNet':ab,'effect':eff,'x':[float(f[k]) for k in FEATURES]});prev=t
 for j,s in enumerate(snaps):
  if s['effect']!='ADD':continue
  future=[z for z in snaps[j+1:] if z['t']-s['t']<=30000]
  if not future:continue
  min_floor=min([s['floor']]+[z['floor'] for z in future]); max_up=max([s['upside']]+[z['upside'] for z in future]); max_abs=max([s['absNet']]+[z['absNet'] for z in future]); end=future[-1]
  spend=max(0.,s['preFloor']-min_floor); cushion=max(abs(s['preFloor'])+5.,5.); spend_ratio=spend/cushion; recovered=max(0.,end['floor']-min_floor); recovery_frac=min(2.,recovered/max(spend,1e-9)) if spend>1e-9 else 2.; upgain=max(0.,max_up-s['upside'])
  stable=int(spend_ratio<=0.75 and recovery_frac>=0.5 and upgain>spend)
  rows.append({'m':mid,'x':s['x'],'y':stable,'shares':s['sh'],'spendRatio':spend_ratio,'recoveryFrac':recovery_frac,'upGain':upgain,'maxAbsNet30s':max_abs})
con.close(); uniq=sorted(set(r['m'] for r in rows));a=int(.6*len(uniq));b=int(.8*len(uniq));sets={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])};X=np.asarray([r['x'] for r in rows],float);y=np.asarray([r['y'] for r in rows],int);ms=[r['m'] for r in rows];tr=np.asarray([i for i,m in enumerate(ms) if m in sets['train']],int);model=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=17,min_samples_leaf=35,l2_regularization=3.,class_weight='balanced',random_state=20260826).fit(X[tr],y[tr]);rep={'version':'R3_STABLE_ADD_ELIGIBILITY_PILOT500_V1','researchOnly':True,'curriculum':'Target structural ADD only; stable label requires 30s spendRatio<=0.75, recovery>=50%, upsideGain>floorSpend','markets':len(uniq),'rows':len(rows),'positive':int(y.sum()),'features':FEATURES,'splits':{}}
for name,S in sets.items():
 ix=np.asarray([i for i,m in enumerate(ms) if m in S],int);rep['splits'][name]=score(model,X[ix],y[ix])
joblib.dump({'model':model,'features':FEATURES,'version':rep['version']},D/'r3_stable_add_eligibility_pilot500_v1.joblib');(D/'r3_stable_add_eligibility_pilot500_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':'data/research/r3_v0/r3_stable_add_eligibility_pilot500_v1_report.json','markets':len(uniq),'rows':len(rows),'test':rep['splits']['test']}))
