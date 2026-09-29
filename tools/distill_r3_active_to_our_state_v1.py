from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data'/'research'/'r3_v0'; DB=ROOT/'data'/'target_wallet_official_v1.db'
HZ=joblib.load(D/'r3_target_active_hazard_5s_hgb_v4.joblib'); CH=joblib.load(D/'r3_target_active_channel_hgb_v3.joblib'); RQ=joblib.load(D/'r3_target_active_repair_effect_fraction_hgb_v2_big.joblib'); AQ=joblib.load(D/'r3_target_active_add_effect_fraction_hgb_v2_big.joblib')
OUR=['combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_taker_age_ms','maker_fills_5s','maker_fills_10s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','combined_absnet_change_10s','event_index_norm','last_price','last_shares','last_role_taker']

def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.
def fullf(up,down,cost,fees,hist,t,role,side,px,sh,i,n):
 gross=up+down; net=up-down; ss=abs(net); base=min(up,down); floor=min(up-cost-fees,down-cost-fees); upside=max(up-cost-fees,down-cost-fees); surplus='UP' if net>0 else 'DOWN' if net<0 else 'FLAT'
 r5=[x for x in hist if t-x[0]<=5000]; r15=[x for x in hist if t-x[0]<=15000]; old5=r5[0] if r5 else (hist[0] if hist else (t,role,side,sh,ss,floor,upside))
 return {'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if len(hist)<2 else float(t-hist[-2][0]),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':ss-old5[4],'floor_change_5s':floor-old5[5],'upside_change_5s':upside-old5[6],'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.,'floor_per_base_share':floor/base if base>1e-9 else 0.,'upside_per_surplus_share':upside/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,n-1),'action_same_as_surplus':0.,'action_side_up':1. if side=='UP' else 0.}
def ourf(up,down,cost,fees,hist,t,role,side,px,sh,i,n):
 gross=up+down; net=up-down; ab=abs(net); floor=min(up-cost-fees,down-cost-fees); best=max(up-cost-fees,down-cost-fees); r5=[x for x in hist if t-x[0]<=5000]; r10=[x for x in hist if t-x[0]<=10000]; lm=[x for x in hist if x[1]=='MAKER']; lt=[x for x in hist if x[1]=='TAKER']; old10=r10[0] if r10 else (hist[0] if hist else (t,role,side,sh,ab,floor,best))
 return {'combined_gross':gross,'combined_net':net,'combined_abs_net':ab,'combined_imbalance_ratio':ab/gross if gross else 0.,'combined_paired_coverage':(2*min(up,down)/gross) if gross else 0.,'worst_case_floor':floor,'best_case_pnl':best,'abs_payoff_gap':abs(best-floor),'last_maker_age_ms':float(t-lm[-1][0]) if lm else 1e6,'last_taker_age_ms':float(t-lt[-1][0]) if lt else 1e6,'maker_fills_5s':sum(x[1]=='MAKER' for x in r5),'maker_fills_10s':sum(x[1]=='MAKER' for x in r10),'taker_fills_5s':sum(x[1]=='TAKER' for x in r5),'taker_fills_10s':sum(x[1]=='TAKER' for x in r10),'maker_shares_5s':sum(x[3] for x in r5 if x[1]=='MAKER'),'maker_shares_10s':sum(x[3] for x in r10 if x[1]=='MAKER'),'taker_shares_5s':sum(x[3] for x in r5 if x[1]=='TAKER'),'taker_shares_10s':sum(x[3] for x in r10 if x[1]=='TAKER'),'combined_absnet_change_10s':ab-old10[4],'event_index_norm':i/max(1,n-1),'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.}
con=sqlite3.connect(DB); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
state=[]; qty=[]
for mid in mids:
 rr=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); up=down=cost=fees=0.; hist=deque()
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
  # pre-action quantity state using current action metadata only for side/price
  if hist:
   pf=fullf(up,down,cost,fees,hist,t,role,side,px,sh,i,len(rr)); of=ourf(up,down,cost,fees,hist,t,role,side,px,sh,i,len(rr))
   if role=='TAKER' and abs(up-down)>1e-6:
    surplus='UP' if up>down else 'DOWN'; pf['action_same_as_surplus']=1. if side==surplus else 0.; pf['action_side_up']=1. if side=='UP' else 0.
    eff='ADD' if side==surplus else 'REPAIR'; bundle=AQ if eff=='ADD' else RQ; y=float(np.clip(bundle['model'].predict(np.asarray([[pf.get(k,0.) for k in bundle['features']]],float))[0],0,3)); qty.append((mid,eff,[of[k] for k in OUR],y))
  if side=='UP': up+=sh
  else: down+=sh
  cost+=px*sh; fees+=fee(sh,px,role); hist.append((t,role,side,sh,abs(up-down),min(up-cost-fees,down-cost-fees),max(up-cost-fees,down-cost-fees)))
  while hist and t-hist[0][0]>15000: hist.popleft()
  ff=fullf(up,down,cost,fees,hist,t,role,side,px,sh,i,len(rr)); oo=ourf(up,down,cost,fees,hist,t,role,side,px,sh,i,len(rr))
  yh=float(HZ['model'].predict_proba(np.asarray([[ff.get(k,0.) for k in HZ['features']]],float))[0,1]); yc=float(CH['model'].predict_proba(np.asarray([[ff.get(k,0.) for k in CH['features']]],float))[0,1]); state.append((mid,[oo[k] for k in OUR],yh,yc))
con.close(); uniq=sorted(set(x[0] for x in state)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); sets={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}
def train_soft(rows,yi):
 tr=[r for r in rows if r[0] in sets['train']]; m=HistGradientBoostingRegressor(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=50,l2_regularization=2.,random_state=20260825).fit(np.asarray([r[1] for r in tr]),np.asarray([r[yi] for r in tr])); out={}
 for nm,ms in sets.items():
  z=[r for r in rows if r[0] in ms]; X=np.asarray([r[1] for r in z]); y=np.asarray([r[yi] for r in z]); p=np.clip(m.predict(X),0,1 if yi in [2,3] else 3); out[nm]={'n':len(z),'maeToTeacher':float(mean_absolute_error(y,p)),'teacherMedian':float(np.median(y)),'studentMedian':float(np.median(p)),'corr':float(np.corrcoef(y,p)[0,1]) if len(y)>1 else None}
 return m,out
mh,sh=train_soft(state,2); mc,sc=train_soft(state,3)
qres={}; qmods={}
for eff in ['REPAIR','ADD']:
 zz=[r for r in qty if r[1]==eff]; m,s=train_soft([(r[0],r[2],r[3]) for r in zz],2); qmods[eff]=m; qres[eff]=s
joblib.dump({'features':OUR,'whenModel':mh,'channelModel':mc,'repairQtyModel':qmods['REPAIR'],'addQtyModel':qmods['ADD'],'teacherBundle':'R3_TARGET_ACTIVE_INTERVENTION_TRAINING_BUNDLE_V1'},D/'r3_active_our_state_student_v1.joblib')
rep={'version':'R3_ACTIVE_OUR_STATE_DISTILLATION_V1','researchOnly':True,'markets':len(uniq),'stateRows':len(state),'qtyRows':len(qty),'features':OUR,'when':sh,'channel':sc,'quantity':qres,'boundary':'Student learns Target-teacher soft outputs using only OUR/R3 runtime-compatible own-state features. No winner/future inputs; no action authority; fixed18/full-gap forbidden.'}; (D/'r3_active_our_state_distillation_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
