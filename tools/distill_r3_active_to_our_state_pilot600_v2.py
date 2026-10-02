from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data'/'research'/'r3_v0'; DB=ROOT/'data'/'target_wallet_official_v1.db'
HZ=joblib.load(D/'r3_target_active_hazard_5s_hgb_v4.joblib'); CH=joblib.load(D/'r3_target_active_channel_hgb_v3.joblib'); RQ=joblib.load(D/'r3_target_active_repair_effect_fraction_hgb_v2_big.joblib'); AQ=joblib.load(D/'r3_target_active_add_effect_fraction_hgb_v2_big.joblib')
OUR=['combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_taker_age_ms','maker_fills_5s','maker_fills_10s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','combined_absnet_change_10s','event_index_norm','last_price','last_shares','last_role_taker']

def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.
def full_values(up,down,cost,fees,hist,t,role,side,px,sh,i,n):
 gross=up+down; net=up-down; ss=abs(net); base=min(up,down); floor=min(up-cost-fees,down-cost-fees); upside=max(up-cost-fees,down-cost-fees); surplus='UP' if net>0 else 'DOWN' if net<0 else 'FLAT'; r5=[x for x in hist if t-x[0]<=5000]; r15=[x for x in hist if t-x[0]<=15000]; old5=r5[0] if r5 else (hist[0] if hist else (t,role,side,sh,ss,floor,upside))
 return {'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if len(hist)<2 else float(t-hist[-2][0]),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':ss-old5[4],'floor_change_5s':floor-old5[5],'upside_change_5s':upside-old5[6],'floor_to_upside_ratio':floor/upside if abs(upside)>1e-9 else 0.,'floor_per_base_share':floor/base if base>1e-9 else 0.,'upside_per_surplus_share':upside/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,n-1),'action_same_as_surplus':0.,'action_side_up':1. if side=='UP' else 0.}
def our_values(up,down,cost,fees,hist,t,role,side,px,sh,i,n):
 gross=up+down; net=up-down; ab=abs(net); floor=min(up-cost-fees,down-cost-fees); best=max(up-cost-fees,down-cost-fees); r5=[x for x in hist if t-x[0]<=5000]; r10=[x for x in hist if t-x[0]<=10000]; lm=[x for x in hist if x[1]=='MAKER']; lt=[x for x in hist if x[1]=='TAKER']; old10=r10[0] if r10 else (hist[0] if hist else (t,role,side,sh,ab,floor,best))
 return {'combined_gross':gross,'combined_net':net,'combined_abs_net':ab,'combined_imbalance_ratio':ab/gross if gross else 0.,'combined_paired_coverage':(2*min(up,down)/gross) if gross else 0.,'worst_case_floor':floor,'best_case_pnl':best,'abs_payoff_gap':abs(best-floor),'last_maker_age_ms':float(t-lm[-1][0]) if lm else 1e6,'last_taker_age_ms':float(t-lt[-1][0]) if lt else 1e6,'maker_fills_5s':sum(x[1]=='MAKER' for x in r5),'maker_fills_10s':sum(x[1]=='MAKER' for x in r10),'taker_fills_5s':sum(x[1]=='TAKER' for x in r5),'taker_fills_10s':sum(x[1]=='TAKER' for x in r10),'maker_shares_5s':sum(x[3] for x in r5 if x[1]=='MAKER'),'maker_shares_10s':sum(x[3] for x in r10 if x[1]=='MAKER'),'taker_shares_5s':sum(x[3] for x in r5 if x[1]=='TAKER'),'taker_shares_10s':sum(x[3] for x in r10 if x[1]=='TAKER'),'combined_absnet_change_10s':ab-old10[4],'event_index_norm':i/max(1,n-1),'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.}
state_mid=[]; state_our=[]; state_full=[]; qty_mid=[]; qty_eff=[]; qty_our=[]; qty_full=[]
con=sqlite3.connect(DB); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][:600]
for mid in mids:
 rr=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); up=down=cost=fees=0.; hist=deque(); N=len(rr)
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
  if hist and role=='TAKER' and abs(up-down)>1e-6:
   pf=full_values(up,down,cost,fees,hist,t,role,side,px,sh,i,N); of=our_values(up,down,cost,fees,hist,t,role,side,px,sh,i,N); surplus='UP' if up>down else 'DOWN'; pf['action_same_as_surplus']=1. if side==surplus else 0.; pf['action_side_up']=1. if side=='UP' else 0.; qty_mid.append(mid); qty_eff.append('ADD' if side==surplus else 'REPAIR'); qty_our.append([of[k] for k in OUR]); qty_full.append(pf)
  if side=='UP': up+=sh
  else: down+=sh
  cost+=px*sh; fees+=fee(sh,px,role); hist.append((t,role,side,sh,abs(up-down),min(up-cost-fees,down-cost-fees),max(up-cost-fees,down-cost-fees)))
  while hist and t-hist[0][0]>15000: hist.popleft()
  ff=full_values(up,down,cost,fees,hist,t,role,side,px,sh,i,N); oo=our_values(up,down,cost,fees,hist,t,role,side,px,sh,i,N); state_mid.append(mid); state_our.append([oo[k] for k in OUR]); state_full.append(ff)
con.close()
# vectorized teacher labels
Xhz=np.asarray([[f.get(k,0.) for k in HZ['features']] for f in state_full],float); Xch=np.asarray([[f.get(k,0.) for k in CH['features']] for f in state_full],float)
yh=HZ['model'].predict_proba(Xhz)[:,1]; yc=CH['model'].predict_proba(Xch)[:,1]
yq=np.zeros(len(qty_mid),float)
for eff,b in [('REPAIR',RQ),('ADD',AQ)]:
 idx=np.asarray([i for i,e in enumerate(qty_eff) if e==eff],int)
 X=np.asarray([[qty_full[i].get(k,0.) for k in b['features']] for i in idx],float); yq[idx]=np.clip(b['model'].predict(X),0,3)
uniq=sorted(set(state_mid)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); sets={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}
def fit_eval(mid,X,y,clipmax):
 tr=np.asarray([i for i,m in enumerate(mid) if m in sets['train']],int); model=HistGradientBoostingRegressor(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=50,l2_regularization=2.,random_state=20260825).fit(X[tr],y[tr]); out={}
 for nm,ms in sets.items():
  ix=np.asarray([i for i,m in enumerate(mid) if m in ms],int); pred=np.clip(model.predict(X[ix]),0,clipmax); yy=y[ix]; out[nm]={'n':int(len(ix)),'maeToTeacher':float(mean_absolute_error(yy,pred)),'teacherMedian':float(np.median(yy)),'studentMedian':float(np.median(pred)),'corr':float(np.corrcoef(yy,pred)[0,1]) if len(ix)>1 else None}
 return model,out
Xstate=np.asarray(state_our,float); mh,sh=fit_eval(state_mid,Xstate,yh,1); mc,sc=fit_eval(state_mid,Xstate,yc,1)
qmods={}; qrep={}; Xq=np.asarray(qty_our,float)
for eff in ['REPAIR','ADD']:
 idx=np.asarray([i for i,e in enumerate(qty_eff) if e==eff],int); mids2=[qty_mid[i] for i in idx]; m,s=fit_eval(mids2,Xq[idx],yq[idx],3); qmods[eff]=m; qrep[eff]=s
joblib.dump({'features':OUR,'whenModel':mh,'channelModel':mc,'repairQtyModel':qmods['REPAIR'],'addQtyModel':qmods['ADD'],'teacherBundle':'R3_TARGET_ACTIVE_INTERVENTION_TRAINING_BUNDLE_V1'},D/'r3_active_our_state_student_v2_pilot600.joblib')
rep={'version':'R3_ACTIVE_OUR_STATE_DISTILLATION_V2_PILOT600','researchOnly':True,'markets':len(uniq),'stateRows':len(state_mid),'qtyRows':len(qty_mid),'features':OUR,'when':sh,'channel':sc,'quantity':qrep,'boundary':'Target teacher soft-label distillation to runtime-compatible OUR/R3 features only; no action authority; fixed18/full-gap forbidden.'}; (D/'r3_active_our_state_distillation_v2_pilot600_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))

