from __future__ import annotations
import sqlite3,json
from collections import deque
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/r3_v0';DB=ROOT/'data/target_wallet_official_v1.db'
DU=joblib.load(D/'r3_target_post_add_dual_hazards_v2.joblib');RA=joblib.load(D/'r3_target_post_add_readd_hazard_v3.joblib')
OUR=['combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_taker_age_ms','maker_fills_5s','maker_fills_10s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','combined_absnet_change_10s','event_index_norm','post_add_floor','post_add_upside','post_add_abs_net','floor_spent','spent_vs_floor_scale','elapsed_since_add_ms','events_since_add','maker_events_since_add','repair_events_since_add','floor_recovered_fraction']
def state_features(up,dn,cost,fees,hist,t,i,n,ep):
 g=up+dn;net=up-dn;ab=abs(net);fl=min(up-cost-fees,dn-cost-fees);be=max(up-cost-fees,dn-cost-fees)
 r5=[x for x in hist if t-x[0]<=5000];r10=[x for x in hist if t-x[0]<=10000]; lm=max((x[0] for x in hist if x[1]=='MAKER'),default=None);lt=max((x[0] for x in hist if x[1]=='TAKER'),default=None);old=r10[0] if r10 else (hist[0] if hist else (t,'','',0,ab,fl,be))
 ctx={'post_add_floor':ep['floor'],'post_add_upside':ep['upside'],'post_add_abs_net':ep['absnet'],'floor_spent':ep['spent'],'spent_vs_floor_scale':ep['spent_scale'],'elapsed_since_add_ms':t-ep['t'],'events_since_add':ep['events'],'maker_events_since_add':ep['maker'],'repair_events_since_add':ep['repair'],'floor_recovered_fraction':max(0.,min(1.5,(fl-ep['floor'])/ep['spent']))}
 z={'combined_gross':g,'combined_net':net,'combined_abs_net':ab,'combined_imbalance_ratio':ab/g if g else 0.,'combined_paired_coverage':2*min(up,dn)/g if g else 0.,'worst_case_floor':fl,'best_case_pnl':be,'abs_payoff_gap':ab,'last_maker_age_ms':t-lm if lm is not None else 1e6,'last_taker_age_ms':t-lt if lt is not None else 1e6,'maker_fills_5s':sum(x[1]=='MAKER' for x in r5),'maker_fills_10s':sum(x[1]=='MAKER' for x in r10),'taker_fills_5s':sum(x[1]=='TAKER' for x in r5),'taker_fills_10s':sum(x[1]=='TAKER' for x in r10),'maker_shares_5s':sum(x[3] for x in r5 if x[1]=='MAKER'),'maker_shares_10s':sum(x[3] for x in r10 if x[1]=='MAKER'),'taker_shares_5s':sum(x[3] for x in r5 if x[1]=='TAKER'),'taker_shares_10s':sum(x[3] for x in r10 if x[1]=='TAKER'),'combined_absnet_change_10s':ab-old[4],'event_index_norm':i/max(1,n-1)};z.update(ctx)
 tf=[ep['floor'],ep['upside'],ep['absnet'],ep['spent'],ep['spent_scale'],t-ep['t'],ep['events'],ep['maker'],ep['repair'],ctx['floor_recovered_fraction'],fl,be,ab,i/max(1,n-1)]
 return [float(z[k]) for k in OUR],tf
con=sqlite3.connect(f'file:{DB.as_posix()}?mode=ro&immutable=1',uri=True); mids=[int(r[0]) for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; rows=[]
for mid in mids:
 ev=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall();n=len(ev);up=dn=cost=fees=0.;hist=deque();ep=None
 for i,(role,side,t,px,sh) in enumerate(ev):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh);pre_net=up-dn;prefl=min(up-cost-fees,dn-cost-fees);preup=max(up-cost-fees,dn-cost-fees);eff=None
  if role=='TAKER' and abs(pre_net)>1e-9:eff='ADD' if ((pre_net>0 and side=='UP')or(pre_net<0 and side=='DOWN')) else 'REPAIR'
  if ep is not None and (t-ep['t']>15000 or eff=='ADD'): ep=None
  if side=='UP':up+=sh
  else:dn+=sh
  cost+=sh*px;fees+=sh*px*.02 if role=='TAKER' else 0.;fl=min(up-cost-fees,dn-cost-fees);be=max(up-cost-fees,dn-cost-fees);ab=abs(up-dn)
  hist.append((t,role,side,sh,ab,fl,be));
  while hist and t-hist[0][0]>15000:hist.popleft()
  if eff=='ADD':
   spent=max(0.,prefl-fl)
   if spent>1e-6: ep={'t':t,'floor':fl,'upside':be,'absnet':ab,'spent':spent,'spent_scale':spent/max(abs(prefl)+5.,5.),'events':0,'maker':0,'repair':0}; x,tf=state_features(up,dn,cost,fees,hist,t,i,n,ep);rows.append((mid,x,tf))
  elif ep is not None:
   ep['events']+=1; ep['maker']+=int(role=='MAKER'); ep['repair']+=int(eff=='REPAIR'); x,tf=state_features(up,dn,cost,fees,hist,t,i,n,ep);rows.append((mid,x,tf))
con.close();print('compact_rows',len(rows),'markets',len(set(r[0] for r in rows)))
XT=np.asarray([r[2] for r in rows],float); PM=DU['makerRecoveryModel'].predict_proba(XT)[:,1];PR=DU['repairEmergencyModel'].predict_proba(XT)[:,1];PA=RA['model'].predict_proba(XT)[:,1]; X=np.asarray([r[1] for r in rows],float);ms=[r[0] for r in rows];ys=[PM,PR,PA];uniq=sorted(set(ms));a=int(.6*len(uniq));b=int(.8*len(uniq));ss={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])};tr=np.asarray([i for i,m in enumerate(ms) if m in ss['train']],int)
def fit(y):
 m=HistGradientBoostingRegressor(max_iter=240,learning_rate=.045,max_leaf_nodes=25,min_samples_leaf=80,l2_regularization=3.,random_state=20260825).fit(X[tr],y[tr]);o={}
 for nm,S in ss.items():
  ix=np.asarray([i for i,mm in enumerate(ms) if mm in S],int);p=np.clip(m.predict(X[ix]),0,1);yy=y[ix];o[nm]={'n':int(len(ix)),'maeToTeacher':float(mean_absolute_error(yy,p)),'corr':float(np.corrcoef(yy,p)[0,1]),'teacherMean':float(yy.mean()),'studentMean':float(p.mean())}
 return m,o
mm,om=fit(PM);mr,orr=fit(PR);ma,oa=fit(PA);rep={'version':'R3_POST_ADD_OUR_STATE_DISTILL_V2_STREAM','researchOnly':True,'markets':len(uniq),'rows':len(rows),'features':OUR,'makerRecovery':om,'repairEmergency':orr,'reAdd':oa};joblib.dump({'features':OUR,'makerRecoveryModel':mm,'repairEmergencyModel':mr,'reAddModel':ma,'version':rep['version']},D/'r3_post_add_our_state_student_v2_stream.joblib');(D/'r3_post_add_our_state_distill_v2_stream_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
