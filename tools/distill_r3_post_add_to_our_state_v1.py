from __future__ import annotations
import sqlite3,json,math
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'
DU=joblib.load(D/'r3_target_post_add_dual_hazards_v2.joblib'); RA=joblib.load(D/'r3_target_post_add_readd_hazard_v3.joblib')
TF=DU['features']
OUR=['combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_taker_age_ms','maker_fills_5s','maker_fills_10s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','combined_absnet_change_10s','event_index_norm','post_add_floor','post_add_upside','post_add_abs_net','floor_spent','spent_vs_floor_scale','elapsed_since_add_ms','events_since_add','maker_events_since_add','repair_events_since_add','floor_recovered_fraction']
def our(up,dn,cost,fees,hist,t,i,n,ctx):
 g=up+dn; net=up-dn; ab=abs(net); fl=min(up-cost-fees,dn-cost-fees); be=max(up-cost-fees,dn-cost-fees); r5=[x for x in hist if t-x[0]<=5000]; r10=[x for x in hist if t-x[0]<=10000]; lm=[x for x in hist if x[1]=='MAKER'];lt=[x for x in hist if x[1]=='TAKER']; old=r10[0] if r10 else (hist[0] if hist else (t,'','',0,ab,fl,be)); z={'combined_gross':g,'combined_net':net,'combined_abs_net':ab,'combined_imbalance_ratio':ab/g if g else 0.,'combined_paired_coverage':2*min(up,dn)/g if g else 0.,'worst_case_floor':fl,'best_case_pnl':be,'abs_payoff_gap':ab,'last_maker_age_ms':t-lm[-1][0] if lm else 1e6,'last_taker_age_ms':t-lt[-1][0] if lt else 1e6,'maker_fills_5s':sum(x[1]=='MAKER' for x in r5),'maker_fills_10s':sum(x[1]=='MAKER' for x in r10),'taker_fills_5s':sum(x[1]=='TAKER' for x in r5),'taker_fills_10s':sum(x[1]=='TAKER' for x in r10),'maker_shares_5s':sum(x[3] for x in r5 if x[1]=='MAKER'),'maker_shares_10s':sum(x[3] for x in r10 if x[1]=='MAKER'),'taker_shares_5s':sum(x[3] for x in r5 if x[1]=='TAKER'),'taker_shares_10s':sum(x[3] for x in r10 if x[1]=='TAKER'),'combined_absnet_change_10s':ab-old[4],'event_index_norm':i/max(1,n-1)}; z.update(ctx); return [float(z.get(k,0.)) for k in OUR]
con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro&immutable=1',uri=True); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][-300:]; rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall();n=len(ev);up=dn=cost=fees=0.;st=[];hist=[]
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh);pre_net=up-dn;prefl=min(up-cost-fees,dn-cost-fees);preup=max(up-cost-fees,dn-cost-fees)
  if side=='UP':up+=sh
  else:dn+=sh
  cost+=sh*px;fees+=sh*px*.02 if role=='TAKER' else 0.;fl=min(up-cost-fees,dn-cost-fees);ups=max(up-cost-fees,dn-cost-fees);net=up-dn;eff=None
  if role=='TAKER' and abs(pre_net)>1e-9:eff='ADD' if ((pre_net>0 and side=='UP')or(pre_net<0 and side=='DOWN')) else 'REPAIR'
  st.append({'i':i,'t':t,'role':role,'side':side,'sh':sh,'preFloor':prefl,'floor':fl,'upside':ups,'net':net,'effect':eff,'up':up,'dn':dn,'cost':cost,'fees':fees,'hist':list(hist)})
  hist.append((t,role,side,sh,abs(net),fl,ups)); hist=[x for x in hist if t-x[0]<=15000]
 for j,s in enumerate(st):
  if s['effect']!='ADD':continue
  spent=max(0.,s['preFloor']-s['floor']);
  if spent<=1e-6:continue
  mk=rp=0
  for k in range(j,min(n,j+80)):
   z=st[k]
   if z['t']-s['t']>15000:break
   if k>j and z['effect']=='ADD':break
   if k>j:mk+=int(z['role']=='MAKER');rp+=int(z['effect']=='REPAIR')
   rec=max(0.,min(1.5,(z['floor']-s['floor'])/spent)); tf=[s['floor'],s['upside'],abs(s['net']),spent,spent/max(abs(s['preFloor'])+5.,5.),z['t']-s['t'],k-j,mk,rp,rec,z['floor'],z['upside'],abs(z['net']),z['i']/max(1,n-1)]
   ctx={'post_add_floor':s['floor'],'post_add_upside':s['upside'],'post_add_abs_net':abs(s['net']),'floor_spent':spent,'spent_vs_floor_scale':spent/max(abs(s['preFloor'])+5.,5.),'elapsed_since_add_ms':z['t']-s['t'],'events_since_add':k-j,'maker_events_since_add':mk,'repair_events_since_add':rp,'floor_recovered_fraction':rec}; rows.append((mid,our(z['up'],z['dn'],z['cost'],z['fees'],z['hist'],z['t'],z['i'],n,ctx),tf))
con.close(); XT=np.asarray([r[2] for r in rows],float); PM=DU['makerRecoveryModel'].predict_proba(XT)[:,1]; PR=DU['repairEmergencyModel'].predict_proba(XT)[:,1]; PA=RA['model'].predict_proba(XT)[:,1]; rows=[(r[0],r[1],float(PM[i]),float(PR[i]),float(PA[i])) for i,r in enumerate(rows)]; X=np.asarray([r[1] for r in rows],float); ms=[r[0] for r in rows]; uniq=sorted(set(ms));a=int(.6*len(uniq));b=int(.8*len(uniq));ss={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])};tr=np.asarray([i for i,m in enumerate(ms) if m in ss['train']],int)
def fit(col):
 y=np.asarray([r[col] for r in rows],float);m=HistGradientBoostingRegressor(max_iter=260,learning_rate=.045,max_leaf_nodes=25,min_samples_leaf=80,l2_regularization=3.,random_state=20260825).fit(X[tr],y[tr]);o={}
 for nm,S in ss.items():
  ix=np.asarray([i for i,mm in enumerate(ms) if mm in S],int);p=np.clip(m.predict(X[ix]),0,1);yy=y[ix];o[nm]={'n':int(len(ix)),'maeToTeacher':float(mean_absolute_error(yy,p)),'corr':float(np.corrcoef(yy,p)[0,1]),'teacherMean':float(yy.mean()),'studentMean':float(p.mean())}
 return m,o
mm,om=fit(2);mr,orr=fit(3);ma,oa=fit(4);rep={'version':'R3_POST_ADD_OUR_STATE_DISTILL_V1','researchOnly':True,'markets':len(uniq),'rows':len(rows),'features':OUR,'makerRecovery':om,'repairEmergency':orr,'reAdd':oa};joblib.dump({'features':OUR,'makerRecoveryModel':mm,'repairEmergencyModel':mr,'reAddModel':ma,'version':rep['version']},D/'r3_post_add_our_state_student_v1.joblib');(D/'r3_post_add_our_state_distill_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
