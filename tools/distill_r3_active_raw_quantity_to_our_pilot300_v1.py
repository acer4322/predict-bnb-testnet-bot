from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'; T=joblib.load(D/'r3_target_active_raw_quantity_hgb_v6.joblib')
OUR=['combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_taker_age_ms','maker_fills_5s','maker_fills_10s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','combined_absnet_change_10s','event_index_norm','last_price','last_shares','last_role_taker']
def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.
def full(up,down,cost,fees,hist,t,role,side,px,sh,i,n):
 gross=up+down; net=up-down; ss=abs(net); base=min(up,down); fl=min(up-cost-fees,down-cost-fees); ups=max(up-cost-fees,down-cost-fees); sur='UP' if net>0 else 'DOWN' if net<0 else 'FLAT'; r5=[x for x in hist if t-x[0]<=5000]; r15=[x for x in hist if t-x[0]<=15000]; old5=r5[0] if r5 else (hist[0] if hist else (t,role,side,sh,ss,fl,ups))
 return {'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'age_since_last_ms':0. if len(hist)<2 else float(t-hist[-2][0]),'events_5s':len(r5),'events_15s':len(r15),'maker_events_15s':sum(x[1]=='MAKER' for x in r15),'taker_events_15s':sum(x[1]=='TAKER' for x in r15),'same_side_events_15s':sum(sur!='FLAT' and x[2]==sur for x in r15),'opp_side_events_15s':sum(sur!='FLAT' and x[2]!=sur for x in r15),'same_side_shares_15s':sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur),'opp_side_shares_15s':sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur),'surplus_change_5s':ss-old5[4],'floor_change_5s':fl-old5[5],'upside_change_5s':ups-old5[6],'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,n-1),'action_same_as_surplus':1. if side==sur else 0.,'action_side_up':1. if side=='UP' else 0.}
def our(up,down,cost,fees,hist,t,role,side,px,sh,i,n):
 gross=up+down; net=up-down; ab=abs(net); fl=min(up-cost-fees,down-cost-fees); best=max(up-cost-fees,down-cost-fees); r5=[x for x in hist if t-x[0]<=5000]; r10=[x for x in hist if t-x[0]<=10000]; lm=[x for x in hist if x[1]=='MAKER']; lt=[x for x in hist if x[1]=='TAKER']; old10=r10[0] if r10 else (hist[0] if hist else (t,role,side,sh,ab,fl,best))
 return {'combined_gross':gross,'combined_net':net,'combined_abs_net':ab,'combined_imbalance_ratio':ab/gross if gross else 0.,'combined_paired_coverage':2*min(up,down)/gross if gross else 0.,'worst_case_floor':fl,'best_case_pnl':best,'abs_payoff_gap':abs(best-fl),'last_maker_age_ms':float(t-lm[-1][0]) if lm else 1e6,'last_taker_age_ms':float(t-lt[-1][0]) if lt else 1e6,'maker_fills_5s':sum(x[1]=='MAKER' for x in r5),'maker_fills_10s':sum(x[1]=='MAKER' for x in r10),'taker_fills_5s':sum(x[1]=='TAKER' for x in r5),'taker_fills_10s':sum(x[1]=='TAKER' for x in r10),'maker_shares_5s':sum(x[3] for x in r5 if x[1]=='MAKER'),'maker_shares_10s':sum(x[3] for x in r10 if x[1]=='MAKER'),'taker_shares_5s':sum(x[3] for x in r5 if x[1]=='TAKER'),'taker_shares_10s':sum(x[3] for x in r10 if x[1]=='TAKER'),'combined_absnet_change_10s':ab-old10[4],'event_index_norm':i/max(1,n-1),'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.}
con=sqlite3.connect(DB); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][:600]; rows=[]
for mid in mids:
 rr=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall(); up=down=cost=fees=0.; hist=deque(); N=len(rr)
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh)
  if hist and role=='TAKER' and abs(up-down)>1e-6:
   ff=full(up,down,cost,fees,hist,t,role,side,px,sh,i,N); oo=our(up,down,cost,fees,hist,t,role,side,px,sh,i,N); eff='ADD' if ff['action_same_as_surplus']>.5 else 'REPAIR'; rows.append((mid,eff,[oo[k] for k in OUR],[ff[k] for k in T['features']]))
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh;fees+=fee(sh,px,role);hist.append((t,role,side,sh,abs(up-down),min(up-cost-fees,down-cost-fees),max(up-cost-fees,down-cost-fees)))
  while hist and t-hist[0][0]>15000:hist.popleft()
con.close()
# Batch teacher inference: avoids one sklearn predict call per Taker event.
packed=[]
for eff in ['REPAIR','ADD']:
 dd=[x for x in rows if x[1]==eff]
 if not dd: continue
 Xteach=np.asarray([x[3] for x in dd],float)
 tm=T['addModel'] if eff=='ADD' else T['repairModel']
 raw=np.expm1(tm.predict(Xteach))
 for x,y0 in zip(dd,raw): packed.append((x[0],eff,x[2],math.log1p(max(.01,float(y0))),float(y0)))
rows=packed
uniq=sorted(set(x[0] for x in rows)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); sets={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}; mods={}; rep={'version':'R3_ACTIVE_RAWQ_OUR_DISTILL_PILOT300_V2_BATCH','markets':len(uniq),'rows':len(rows),'features':OUR,'effects':{}}
for eff in ['REPAIR','ADD']:
 dd=[x for x in rows if x[1]==eff]; X=np.asarray([x[2] for x in dd],float); y=np.asarray([x[3] for x in dd],float); raw=np.asarray([x[4] for x in dd],float); ms=[x[0] for x in dd]; tr=np.asarray([i for i,m in enumerate(ms) if m in sets['train']],int); m=HistGradientBoostingRegressor(max_iter=240,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=45,l2_regularization=2.5,random_state=20260825).fit(X[tr],y[tr]); mods[eff]=m; out={}
 for nm,sset in sets.items():
  ix=np.asarray([i for i,mm in enumerate(ms) if mm in sset],int); pred=np.expm1(m.predict(X[ix])); yy=raw[ix]; out[nm]={'n':int(len(ix)),'maeToTeacherShares':float(mean_absolute_error(yy,pred)),'teacherMedian':float(np.median(yy)),'studentMedian':float(np.median(pred)),'corr':float(np.corrcoef(yy,pred)[0,1]) if len(ix)>1 else None}
 rep['effects'][eff]=out
joblib.dump({'features':OUR,'repairModel':mods['REPAIR'],'addModel':mods['ADD'],'version':rep['version']},D/'r3_active_rawq_our_student_pilot300_v2_batch.joblib'); (D/'r3_active_rawq_our_distill_pilot300_v2_batch_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
