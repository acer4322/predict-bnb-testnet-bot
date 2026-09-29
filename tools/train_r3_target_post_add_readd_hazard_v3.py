from __future__ import annotations
import sqlite3,json
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data/target_wallet_official_v1.db'; D=ROOT/'data/research/r3_v0'
FEATS=['post_add_floor','post_add_upside','post_add_abs_net','floor_spent','spent_vs_floor_scale','elapsed_since_add_ms','events_since_add','maker_events_since_add','repair_events_since_add','floor_recovered_fraction','current_floor','current_upside','current_abs_net','event_progress']
con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro&immutable=1',uri=True); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); n=len(ev); up=dn=cost=fees=0.; st=[]
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh); pre_net=up-dn; pre_floor=min(up-cost-fees,dn-cost-fees); pre_up=max(up-cost-fees,dn-cost-fees)
  if side=='UP':up+=sh
  else:dn+=sh
  cost+=sh*px;fees+=sh*px*.02 if role=='TAKER' else 0.; fl=min(up-cost-fees,dn-cost-fees);ups=max(up-cost-fees,dn-cost-fees);net=up-dn;eff=None
  if role=='TAKER' and abs(pre_net)>1e-9: eff='ADD' if ((pre_net>0 and side=='UP') or(pre_net<0 and side=='DOWN')) else 'REPAIR'
  st.append({'i':i,'t':t,'role':role,'effect':eff,'preFloor':pre_floor,'floor':fl,'upside':ups,'net':net})
 for j,s in enumerate(st):
  if s['effect']!='ADD':continue
  spent=max(0.,s['preFloor']-s['floor'])
  if spent<=1e-6:continue
  mk=rp=0
  for k in range(j,min(n,j+80)):
   z=st[k]
   if z['t']-s['t']>15000:break
   if k>j: mk+=int(z['role']=='MAKER');rp+=int(z['effect']=='REPAIR')
   rec=max(0.,min(1.5,(z['floor']-s['floor'])/spent)); y=0
   for q in st[k+1:]:
    if q['t']-z['t']>10000:break
    if q['effect']=='ADD': y=1;break
   x=[s['floor'],s['upside'],abs(s['net']),spent,spent/max(abs(s['preFloor'])+5.,5.),z['t']-s['t'],k-j,mk,rp,rec,z['floor'],z['upside'],abs(z['net']),z['i']/max(1,n-1)]
   rows.append((mid,x,y))
   if k>j and z['effect']=='ADD':break
con.close(); X=np.asarray([r[1] for r in rows],float); y=np.asarray([r[2] for r in rows],int); ms=[r[0] for r in rows]; uniq=sorted(set(ms));a=int(.6*len(uniq));b=int(.8*len(uniq));ss={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])};tr=np.asarray([i for i,m in enumerate(ms) if m in ss['train']],int)
m=HistGradientBoostingClassifier(max_iter=260,learning_rate=.045,max_leaf_nodes=25,min_samples_leaf=90,l2_regularization=3.,class_weight='balanced',random_state=20260825).fit(X[tr],y[tr]);out={}
for nm,sset in ss.items():
 ix=np.asarray([i for i,mm in enumerate(ms) if mm in sset],int);yy=y[ix];p=m.predict_proba(X[ix])[:,1];pred=(p>=.5).astype(int);out[nm]={'n':int(len(ix)),'positiveRate':float(yy.mean()),'predictedRate':float(pred.mean()),'auc':float(roc_auc_score(yy,p)),'ap':float(average_precision_score(yy,p)),'balancedAccuracy':float(balanced_accuracy_score(yy,pred))}
rep={'version':'R3_TARGET_POST_ADD_READD_HAZARD_V3','researchOnly':True,'markets':len(uniq),'rows':len(rows),'features':FEATS,'splits':out,'label':'Another structural ADD occurs within next 10s from this post-ADD checkpoint. No winner/PnL labels.'};joblib.dump({'features':FEATS,'model':m,'version':rep['version']},D/'r3_target_post_add_readd_hazard_v3.joblib');(D/'r3_target_post_add_readd_hazard_v3_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
