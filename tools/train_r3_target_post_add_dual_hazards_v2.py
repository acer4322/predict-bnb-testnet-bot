from __future__ import annotations
import sqlite3,json,math
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data/target_wallet_official_v1.db'; D=ROOT/'data/research/r3_v0'
# One row = strict-past checkpoint after an ADD and before next ADD, sampled at parent events.
# Labels are independent future hazards within 10s: Maker floor recovery >=50% of spent cushion; Taker REPAIR occurs.
FEATS=['post_add_floor','post_add_upside','post_add_abs_net','floor_spent','spent_vs_floor_scale','elapsed_since_add_ms','events_since_add','maker_events_since_add','repair_events_since_add','floor_recovered_fraction','current_floor','current_upside','current_abs_net','event_progress']
con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro&immutable=1',uri=True)
mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); n=len(ev)
 up=dn=cost=fees=0.; states=[]
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
  pre_net=up-dn; pre_floor=min(up-cost-fees,dn-cost-fees); pre_up=max(up-cost-fees,dn-cost-fees)
  if side=='UP': up+=sh
  else: dn+=sh
  cost+=sh*px; fees += sh*px*.02 if role=='TAKER' else 0.
  floor=min(up-cost-fees,dn-cost-fees); upside=max(up-cost-fees,dn-cost-fees); post_net=up-dn
  effect=None
  if role=='TAKER' and abs(pre_net)>1e-9: effect='ADD' if ((pre_net>0 and side=='UP') or (pre_net<0 and side=='DOWN')) else 'REPAIR'
  states.append({'i':i,'t':t,'role':role,'effect':effect,'preFloor':pre_floor,'preUpside':pre_up,'floor':floor,'upside':upside,'net':post_net})
 for j,s in enumerate(states):
  if s['effect']!='ADD': continue
  spent=max(0.,s['preFloor']-s['floor'])
  if spent<=1e-6: continue
  # checkpoints after ADD, before next ADD, up to 15s. Include ADD state itself.
  maker_ct=repair_ct=0
  for k in range(j,min(n,j+80)):
   z=states[k]
   if z['t']-s['t']>15000: break
   if k>j and z['effect']=='ADD': break
   if k>j:
    maker_ct += int(z['role']=='MAKER'); repair_ct += int(z['effect']=='REPAIR')
   recovered=max(0.,min(1.5,(z['floor']-s['floor'])/spent))
   # independent future 10s labels strictly after checkpoint
   y_maker=0; y_repair=0
   for q in states[k+1:]:
    if q['t']-z['t']>10000: break
    if q['effect']=='ADD': break
    if q['role']=='MAKER' and q['floor'] >= s['floor'] + .5*spent: y_maker=1
    if q['effect']=='REPAIR': y_repair=1
   feats=[s['floor'],s['upside'],abs(s['net']),spent,spent/max(abs(s['preFloor'])+5.,5.),z['t']-s['t'],k-j,maker_ct,repair_ct,recovered,z['floor'],z['upside'],abs(z['net']),z['i']/max(1,n-1)]
   rows.append((mid,feats,y_maker,y_repair))
con.close()
X=np.asarray([r[1] for r in rows],float); ym=np.asarray([r[2] for r in rows],int); yr=np.asarray([r[3] for r in rows],int); ms=[r[0] for r in rows]
uniq=sorted(set(ms)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); splits={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}
tr=np.asarray([i for i,m in enumerate(ms) if m in splits['train']],int)
def fit(y):
 m=HistGradientBoostingClassifier(max_iter=260,learning_rate=.045,max_leaf_nodes=25,min_samples_leaf=90,l2_regularization=3.,class_weight='balanced',random_state=20260825).fit(X[tr],y[tr]); out={}
 for nm,ss in splits.items():
  ix=np.asarray([i for i,mm in enumerate(ms) if mm in ss],int); yy=y[ix]; p=m.predict_proba(X[ix])[:,1]; pred=(p>=.5).astype(int)
  out[nm]={'n':int(len(ix)),'positiveRate':float(yy.mean()),'predictedRate':float(pred.mean()),'auc':float(roc_auc_score(yy,p)) if len(set(yy))>1 else None,'ap':float(average_precision_score(yy,p)) if len(set(yy))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pred))}
 return m,out
mm,om=fit(ym); mr,orr=fit(yr)
rep={'version':'R3_TARGET_POST_ADD_DUAL_HAZARDS_V2','researchOnly':True,'markets':len(uniq),'rows':len(rows),'features':FEATS,'makerRecovery10s':om,'repairEmergency10s':orr,'semantics':'Independent hazards after ADD. Maker recovery and repair emergency are not mutually exclusive. No winner/PnL labels. fixed18 irrelevant/forbidden.'}
joblib.dump({'features':FEATS,'makerRecoveryModel':mm,'repairEmergencyModel':mr,'version':rep['version']},D/'r3_target_post_add_dual_hazards_v2.joblib'); (D/'r3_target_post_add_dual_hazards_v2_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
