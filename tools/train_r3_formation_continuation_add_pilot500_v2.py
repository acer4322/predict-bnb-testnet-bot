from __future__ import annotations
import sqlite3,json
from collections import deque
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,precision_score,recall_score
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/r3_v0';DB=ROOT/'data/target_wallet_official_v1.db';BASE=joblib.load(D/'r3_stable_add_eligibility_pilot500_v2_normalized.joblib');F=BASE['features']
def score(m,X,y):
 p=m.predict_proba(X)[:,1];q=p>=.5;return {'n':int(len(y)),'positiveRate':float(y.mean()),'predictedRate':float(q.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,q)),'precision':float(precision_score(y,q,zero_division=0)),'recall':float(recall_score(y,q,zero_division=0))}
con=sqlite3.connect(DB);mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][-500:];rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall();n=len(ev);up=dn=cost=fees=0.;hist=deque();sn=[];prev=None
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh);pre_net=up-dn;pre_floor=min(up-cost-fees,dn-cost-fees)
  if side=='UP':up+=sh
  else:dn+=sh
  cost+=px*sh;fees+=px*sh*.02 if role=='TAKER' else 0.;pu,pd=up-cost-fees,dn-cost-fees;fl=min(pu,pd);ups=max(pu,pd);net=up-dn;g=up+dn;ab=abs(net);sur='UP' if net>0 else 'DOWN' if net<0 else 'FLAT';hist.append((t,role,side,sh,fl,ups,ab))
  while hist and t-hist[0][0]>15000:hist.popleft()
  q5=[z for z in hist if t-z[0]<=5000];q15=list(hist);old=q5[0] if q5 else q15[0];totsh=sum(z[3] for z in q15) or 1.;nev=len(q15) or 1
  v={'paired_coverage':2*min(up,dn)/g if g else 0.,'imbalance_ratio':ab/g if g else 0.,'cost_per_gross':(cost+fees)/g if g else 0.,'floor_per_gross':fl/g if g else 0.,'upside_per_gross':ups/g if g else 0.,'floor_to_upside':fl/ups if abs(ups)>1e-9 else 0.,'last_price':px,'last_role_taker':float(role=='TAKER'),'age_since_last_s':0. if prev is None else (t-prev)/1000.,'events_5s':len(q5),'events_15s':len(q15),'maker_frac_15s':sum(z[1]=='MAKER' for z in q15)/nev,'taker_frac_15s':sum(z[1]=='TAKER' for z in q15)/nev,'same_side_event_frac_15s':sum(sur!='FLAT' and z[2]==sur for z in q15)/nev,'same_side_share_frac_15s':sum(z[3] for z in q15 if sur!='FLAT' and z[2]==sur)/totsh,'opp_side_share_frac_15s':sum(z[3] for z in q15 if sur!='FLAT' and z[2]!=sur)/totsh,'floor_change5_per_gross':(fl-old[4])/g if g else 0.,'upside_change5_per_gross':(ups-old[5])/g if g else 0.,'absnet_change5_per_gross':(ab-old[6])/g if g else 0.,'event_index_norm':i/max(1,n-1)}
  eff=None
  if role=='TAKER' and abs(pre_net)>1e-9:eff='ADD' if ((pre_net>0 and side=='UP')or(pre_net<0 and side=='DOWN')) else 'REPAIR'
  sn.append({'t':t,'role':role,'preFloor':pre_floor,'floor':fl,'upside':ups,'absNet':ab,'effect':eff,'x':[float(v[k]) for k in F]});prev=t
 for j,s in enumerate(sn):
  if s['effect']!='ADD':continue
  fut=[z for z in sn[j+1:] if z['t']-s['t']<=30000]
  if not fut:continue
  minfl=min([s['floor']]+[z['floor'] for z in fut]);maxup=max([s['upside']]+[z['upside'] for z in fut]);end=fut[-1];sp=max(0.,s['preFloor']-minfl);ratio=sp/max(abs(s['preFloor'])+5.,5.);rec=max(0.,end['floor']-minfl);rf=min(2.,rec/max(sp,1e-9)) if sp>1e-9 else 2.;ug=max(0.,maxup-s['upside']);stable=(ratio<=.75 and rf>=.5 and ug>sp)
  maker_after=sum(z['role']=='MAKER' for z in fut); max_floor=max([s['floor']]+[z['floor'] for z in fut]); min_abs=min([s['absNet']]+[z['absNet'] for z in fut]); anchor=int((not stable) and maker_after>=2 and (max_floor-s['floor'])>=0.5*max(sp,1e-9) and min_abs<=0.9*s['absNet'])
  if not stable:rows.append((mid,s['x'],anchor))
con.close();uniq=sorted(set(r[0] for r in rows));a=int(.6*len(uniq));b=int(.8*len(uniq));S={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])};X=np.asarray([r[1] for r in rows],float);y=np.asarray([r[2] for r in rows],int);ms=[r[0] for r in rows];tr=np.asarray([i for i,m in enumerate(ms) if m in S['train']],int);m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=17,min_samples_leaf=35,l2_regularization=3.,class_weight='balanced',random_state=20260826).fit(X[tr],y[tr]);rep={'version':'R3_FORMATION_CONTINUATION_ADD_PILOT500_V2','curriculum':'Target non-Stable structural ADD only; continuation if >=2 Maker events in 30s, max floor recovers >=50% of spend and absNet contracts >=10%','markets':len(uniq),'rows':len(rows),'positive':int(y.sum()),'features':F,'splits':{}}
for nm,ss in S.items():
 ix=np.asarray([i for i,z in enumerate(ms) if z in ss],int);rep['splits'][nm]=score(m,X[ix],y[ix])
joblib.dump({'model':m,'features':F,'version':rep['version']},D/'r3_formation_continuation_add_pilot500_v2.joblib');(D/'r3_formation_continuation_add_pilot500_v2_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':'data/research/r3_v0/r3_formation_continuation_add_pilot500_v2_report.json','test':rep['splits']['test']}))
