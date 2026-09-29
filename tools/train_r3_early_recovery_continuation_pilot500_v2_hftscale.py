from __future__ import annotations
import sqlite3,json
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,precision_score,recall_score
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/r3_v0';DB=ROOT/'data/target_wallet_official_v1.db'
F=['elapsed_s','floor_recovery_frac','absnet_change_frac','paired_coverage','paired_change','maker_events','repair_events','readd_events','gross_change_frac','event_index_norm']
def score(m,X,y):
 p=m.predict_proba(X)[:,1];q=p>=.5;return {'n':len(y),'positiveRate':float(y.mean()),'predictedRate':float(q.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,q)),'precision':float(precision_score(y,q,zero_division=0)),'recall':float(recall_score(y,q,zero_division=0))}
con=sqlite3.connect(DB);mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][-500:];rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall();n=len(ev);up=dn=cost=fees=0.;sn=[]
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh);pre_net=up-dn;pre_floor=min(up-cost-fees,dn-cost-fees)
  if side=='UP':up+=sh
  else:dn+=sh
  cost+=px*sh;fees+=px*sh*.02 if role=='TAKER' else 0.;pu,pd=up-cost-fees,dn-cost-fees;fl=min(pu,pd);net=up-dn;g=up+dn;ab=abs(net);pc=2*min(up,dn)/g if g else 0.;eff=None
  if role=='TAKER' and abs(pre_net)>1e-9:eff='ADD' if ((pre_net>0 and side=='UP')or(pre_net<0 and side=='DOWN')) else 'REPAIR'
  sn.append({'i':i,'t':t,'role':role,'side':side,'floor':fl,'preFloor':pre_floor,'abs':ab,'gross':g,'pc':pc,'effect':eff})
 for j,s in enumerate(sn):
  if s['effect']!='ADD':continue
  fut30=[z for z in sn[j+1:] if z['t']-s['t']<=30000]
  if not fut30:continue
  minfl=min([s['floor']]+[z['floor'] for z in fut30]);sp=max(0.,s['preFloor']-minfl);maxrec=max([s['floor']]+[z['floor'] for z in fut30])-minfl;minabs=min([s['abs']]+[z['abs'] for z in fut30]);label=int(maxrec>=.5*max(sp,1e-9) and minabs<=.9*s['abs'])
  for horizon in (5000,10000,15000):
   past=[z for z in fut30 if z['t']-s['t']<=horizon]
   if not past:continue
   z=past[-1];mk=sum(x['role']=='MAKER' for x in past);rp=sum(x['effect']=='REPAIR' for x in past);ra=sum(x['effect']=='ADD' for x in past);rec=max(0.,z['floor']-s['floor'])/max(sp,1e-9) if sp>1e-9 else 0.;x=[(z['t']-s['t'])/1000.,rec,(z['abs']-s['abs'])/max(s['abs'],1.),z['pc'],z['pc']-s['pc'],mk,rp,ra,(z['gross']-s['gross'])/max(s['gross'],1.),s['i']/max(1,n-1)];rows.append((mid,x,label,horizon))
con.close();uniq=sorted(set(r[0] for r in rows));a=int(.6*len(uniq));b=int(.8*len(uniq));S={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])};X=np.asarray([r[1] for r in rows],float);y=np.asarray([r[2] for r in rows],int);ms=[r[0] for r in rows];tr=np.asarray([i for i,m in enumerate(ms) if m in S['train']],int);m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=17,min_samples_leaf=40,l2_regularization=3.,class_weight='balanced',random_state=20260826).fit(X[tr],y[tr]);rep={'version':'R3_EARLY_RECOVERY_CONTINUATION_PILOT500_V2_HFTSCALE','features':F,'markets':len(uniq),'rows':len(rows),'horizonsMs':[5000,10000,15000],'splits':{}}
for nm,ss in S.items():
 ix=np.asarray([i for i,z in enumerate(ms) if z in ss],int);rep['splits'][nm]=score(m,X[ix],y[ix])
joblib.dump({'model':m,'features':F,'version':rep['version']},D/'r3_early_recovery_continuation_pilot500_v2_hftscale.joblib');(D/'r3_early_recovery_continuation_pilot500_v2_hftscale_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':'data/research/r3_v0/r3_early_recovery_continuation_pilot500_v2_hftscale_report.json','test':rep['splits']['test']}))
