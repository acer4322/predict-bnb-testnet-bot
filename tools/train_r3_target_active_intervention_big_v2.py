from __future__ import annotations
import sqlite3,json,math
from pathlib import Path
from collections import deque
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingRegressor,HistGradientBoostingClassifier
from sklearn.metrics import mean_absolute_error,roc_auc_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data/research/r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm','action_same_as_surplus','action_side_up']
def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.
def rows_for_market(c,mid):
 rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
 if len(rr)<4:return []
 up=down=cost=fees=0.; hist=deque(); prev_t=None; out=[]
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
  # strict-past state BEFORE current event
  pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'
  r5=[x for x in hist if t-x[0]<=5000]; r15=[x for x in hist if t-x[0]<=15000]; old5=r5[0] if r5 else (hist[0] if hist else (t,'', '',0,ss,fl,ups))
  f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1),'action_same_as_surplus':1. if surplus!='FLAT' and side==surplus else 0.,'action_side_up':1. if side=='UP' else 0.}
  pre_net=up-down
  # apply current event to derive post-hoc effect label only
  nup=up+(sh if side=='UP' else 0); ndn=down+(sh if side=='DOWN' else 0); post_net=nup-ndn
  if role=='TAKER' and abs(pre_net)>1e-9:
   effect='REPAIR_EFFECT' if abs(post_net)<abs(pre_net)-1e-9 else 'ADD_EFFECT' if abs(post_net)>abs(pre_net)+1e-9 else 'NEUTRAL'
   if effect!='NEUTRAL': out.append((mid,t,[f[k] for k in FEATURES],effect,float(sh),float(abs(pre_net)),float(min(2.,sh/abs(pre_net)))))
  # then commit event
  up=nup; down=ndn; cost+=px*sh; fees+=fee(sh,px,role); npu=up-cost-fees; npd=down-cost-fees; nfl=min(npu,npd); nups=max(npu,npd); nss=abs(up-down)
  hist.append((t,role,side,sh,nss,nfl,nups));
  while hist and t-hist[0][0]>15000: hist.popleft()
  prev_t=t
 return out
c=sqlite3.connect(DB); mids=[int(r[0]) for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
for mid in mids:data.extend(rows_for_market(c,mid))
c.close(); usable=sorted(set(x[0] for x in data)); a=int(.6*len(usable)); b=int(.8*len(usable)); sp={'train':set(usable[:a]),'validation':set(usable[a:b]),'test':set(usable[b:])}
rep={'version':'R3_TARGET_ACTIVE_INTERVENTION_BIG_V2','researchOnly':True,'fixed18Forbidden':True,'fullGapForbidden':True,'markets':len(usable),'events':len(data),'features':FEATURES,'splits':{k:len(v) for k,v in sp.items()},'effects':{}}
# effect classifier among taker events
X=np.asarray([x[2] for x in data if x[0] in sp['train']],float); y=np.asarray([1 if x[3]=='REPAIR_EFFECT' else 0 for x in data if x[0] in sp['train']],int)
eff=HistGradientBoostingClassifier(max_iter=240,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=20,l2_regularization=2.,random_state=20260825).fit(X,y)
rep['effectClassifier']={}
for name,ms in sp.items():
 dd=[x for x in data if x[0] in ms]; XX=np.asarray([x[2] for x in dd],float); yy=np.asarray([1 if x[3]=='REPAIR_EFFECT' else 0 for x in dd],int); pp=eff.predict_proba(XX)[:,1]; pr=(pp>=.5).astype(int); rep['effectClassifier'][name]={'n':len(dd),'repairRate':float(yy.mean()),'auc':float(roc_auc_score(yy,pp)) if len(np.unique(yy))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pr))}
joblib.dump({'version':rep['version'],'task':'REPAIR_VS_ADD','features':FEATURES,'model':eff},OUT/'r3_target_active_effect_hgb_v2.joblib')
for effect in ['REPAIR_EFFECT','ADD_EFFECT']:
 dd=[x for x in data if x[3]==effect]; tr=[x for x in dd if x[0] in sp['train']]; med=float(np.median([x[6] for x in tr])); Xtr=np.asarray([x[2] for x in tr],float); ytr=np.asarray([x[6] for x in tr],float)
 m=HistGradientBoostingRegressor(max_iter=280,learning_rate=.04,max_leaf_nodes=19,min_samples_leaf=15,l2_regularization=2.,random_state=20260825).fit(Xtr,ytr)
 er={}
 for name,ms in sp.items():
  q=[x for x in dd if x[0] in ms]; XX=np.asarray([x[2] for x in q],float); yy=np.asarray([x[6] for x in q],float); pred=np.clip(m.predict(XX),0,2); er[name]={'n':len(q),'markets':len(set(x[0] for x in q)),'maeFraction':float(np.mean(np.abs(pred-yy))),'baselineMedianFraction':med,'baselineMaeFraction':float(np.mean(np.abs(med-yy))),'maeLiftVsMedian':float(np.mean(np.abs(med-yy))-np.mean(np.abs(pred-yy))),'labelMedian':float(np.median(yy)),'labelP90':float(np.quantile(yy,.9)),'predMedian':float(np.median(pred))}
 rep['effects'][effect]=er
 joblib.dump({'version':rep['version'],'task':effect+'_FRACTION','features':FEATURES,'model':m,'clip':[0.,2.]},OUT/f'r3_target_active_{effect.lower()}_fraction_hgb_v2_big.joblib')
(OUT/'r3_target_active_intervention_big_v2_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
