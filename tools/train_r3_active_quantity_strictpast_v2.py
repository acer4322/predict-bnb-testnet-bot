from __future__ import annotations
import json,sqlite3,math
from pathlib import Path
from collections import deque
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingRegressor,HistGradientBoostingClassifier
from sklearn.metrics import mean_absolute_error,roc_auc_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/r3_v0';DB=ROOT/'data/target_wallet_official_v1.db'
TF=joblib.load(D/'r3_target_active_repair_effect_fraction_hgb_v2_big.joblib'); AF=joblib.load(D/'r3_target_active_add_effect_fraction_hgb_v2_big.joblib'); TR=joblib.load(D/'r3_target_active_raw_quantity_hgb_v6.joblib')
OUR=['combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_taker_age_ms','maker_fills_5s','maker_fills_10s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','combined_absnet_change_10s','event_index_norm','last_price','last_shares','last_role_taker']
def fee(sh,px,role):return sh*px*.02 if str(role).upper()=='TAKER' else 0.
def prior_state(up,down,cost,fees,hist,t,action_side,i,n):
 gross=up+down;net=up-down;ab=abs(net);base=min(up,down);fl=min(up-cost-fees,down-cost-fees);best=max(up-cost-fees,down-cost-fees);sur='UP' if net>0 else 'DOWN' if net<0 else 'FLAT';r5=[x for x in hist if t-x[0]<=5000];r10=[x for x in hist if t-x[0]<=10000];r15=[x for x in hist if t-x[0]<=15000];old5=r5[0] if r5 else hist[0];old10=r10[0] if r10 else hist[0];lm=[x for x in hist if x[1]=='MAKER'];lt=[x for x in hist if x[1]=='TAKER'];prev=hist[-1]
 full={'floor':fl,'upside':best,'upside_gap':best-fl,'surplus_shares':ab,'base_pair_shares':base,'surplus_ratio':ab/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'age_since_last_ms':float(t-prev[0]),'events_5s':len(r5),'events_15s':len(r15),'maker_events_15s':sum(x[1]=='MAKER' for x in r15),'taker_events_15s':sum(x[1]=='TAKER' for x in r15),'same_side_events_15s':sum(sur!='FLAT' and x[2]==sur for x in r15),'opp_side_events_15s':sum(sur!='FLAT' and x[2]!=sur for x in r15),'same_side_shares_15s':sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur),'opp_side_shares_15s':sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur),'surplus_change_5s':ab-old5[4],'floor_change_5s':fl-old5[5],'upside_change_5s':best-old5[6],'floor_to_upside_ratio':fl/best if abs(best)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':best/ab if ab>1e-9 else 0.,'event_index_norm':i/max(1,n-1),'action_same_as_surplus':1. if action_side==sur else 0.,'action_side_up':1. if action_side=='UP' else 0.}
 our={'combined_gross':gross,'combined_net':net,'combined_abs_net':ab,'combined_imbalance_ratio':ab/gross if gross else 0.,'combined_paired_coverage':2*base/gross if gross else 0.,'worst_case_floor':fl,'best_case_pnl':best,'abs_payoff_gap':abs(best-fl),'last_maker_age_ms':float(t-lm[-1][0]) if lm else 1e6,'last_taker_age_ms':float(t-lt[-1][0]) if lt else 1e6,'maker_fills_5s':sum(x[1]=='MAKER' for x in r5),'maker_fills_10s':sum(x[1]=='MAKER' for x in r10),'taker_fills_5s':sum(x[1]=='TAKER' for x in r5),'taker_fills_10s':sum(x[1]=='TAKER' for x in r10),'maker_shares_5s':sum(x[3] for x in r5 if x[1]=='MAKER'),'maker_shares_10s':sum(x[3] for x in r10 if x[1]=='MAKER'),'taker_shares_5s':sum(x[3] for x in r5 if x[1]=='TAKER'),'taker_shares_10s':sum(x[3] for x in r10 if x[1]=='TAKER'),'combined_absnet_change_10s':ab-old10[4],'event_index_norm':i/max(1,n-1),'last_price':prev[3],'last_shares':prev[4-1] if False else prev[3], 'last_role_taker':1. if prev[1]=='TAKER' else 0.}
 # hist tuple: t,role,side,shares,absnet,floor,best,price; use explicit corrected prior event fields
 our['last_price']=prev[7]; our['last_shares']=prev[3]
 return full,our
con=sqlite3.connect(DB);mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][:1200];rows=[]
for mid in mids:
 rr=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall();up=down=cost=fees=0.;hist=deque();N=len(rr)
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh)
  if hist and role=='TAKER' and abs(up-down)>1e-6:
   full,our=prior_state(up,down,cost,fees,hist,t,side,i,N);eff='ADD' if full['action_same_as_surplus']>.5 else 'REPAIR';rows.append((mid,eff,sh,[our[k] for k in OUR],[full.get(k,0.) for k in (AF if eff=='ADD' else TF)['features']],[full.get(k,0.) for k in TR['features']],abs(up-down)))
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh;fees+=fee(sh,px,role);hist.append((t,role,side,sh,abs(up-down),min(up-cost-fees,down-cost-fees),max(up-cost-fees,down-cost-fees),px))
  while hist and t-hist[0][0]>15000:hist.popleft()
con.close();uniq=sorted(set(r[0] for r in rows));a=int(.6*len(uniq));b=int(.8*len(uniq));sets={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])};mods={};report={'version':'R3_ACTIVE_QUANTITY_STRICTPAST_V2','researchOnly':True,'leakageFix':'quantity OUR last_price/last_shares/last_role_taker use previous completed event only','markets':len(uniq),'rows':len(rows),'effects':{}}
all_gate=[]
for eff in ['REPAIR','ADD']:
 dd=[r for r in rows if r[1]==eff];X=np.asarray([r[3] for r in dd],float);actual=np.asarray([r[2] for r in dd]);ms=np.asarray([r[0] for r in dd]);gap=np.asarray([r[6] for r in dd]);bundle=AF if eff=='ADD' else TF;Xt=np.asarray([r[4] for r in dd],float);frac_teacher=np.clip(bundle['model'].predict(Xt),0,3);Xr=np.asarray([r[5] for r in dd],float);rm=TR['addModel'] if eff=='ADD' else TR['repairModel'];raw_teacher=np.maximum(.01,np.expm1(rm.predict(Xr)))
 tr=np.where(np.isin(ms,list(sets['train'])))[0];fm=HistGradientBoostingRegressor(max_iter=240,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=50,l2_regularization=2.5,random_state=20260825).fit(X[tr],frac_teacher[tr]);rm2=HistGradientBoostingRegressor(max_iter=240,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=50,l2_regularization=2.5,random_state=20260825).fit(X[tr],np.log1p(raw_teacher[tr]));mods[eff]=(fm,rm2);out={}
 frac_pred=np.clip(fm.predict(X),0,3)*gap;raw_pred=np.maximum(.01,np.expm1(rm2.predict(X)))
 for nm,ss in sets.items():
  ix=np.where(np.isin(ms,list(ss)))[0];out[nm]={'n':int(len(ix)),'fractionTeacherCorr':float(np.corrcoef(frac_teacher[ix],np.clip(fm.predict(X[ix]),0,3))[0,1]),'rawTeacherCorr':float(np.corrcoef(raw_teacher[ix],raw_pred[ix])[0,1]),'rawTeacherMaeShares':float(mean_absolute_error(raw_teacher[ix],raw_pred[ix])),'actualMaeFractionShares':float(mean_absolute_error(actual[ix],frac_pred[ix])),'actualMaeRawShares':float(mean_absolute_error(actual[ix],raw_pred[ix]))}
 report['effects'][eff]=out
 for j in range(len(dd)): all_gate.append((ms[j],X[j],actual[j],frac_pred[j],raw_pred[j],gap[j],1. if eff=='REPAIR' else 0.))
# gate
ms=np.asarray([z[0] for z in all_gate]);base=np.asarray([z[1] for z in all_gate]);actual=np.asarray([z[2] for z in all_gate]);fq=np.asarray([z[3] for z in all_gate]);rq=np.asarray([z[4] for z in all_gate]);gap=np.asarray([z[5] for z in all_gate]);er=np.asarray([z[6] for z in all_gate]);y=(np.abs(rq-actual)<=np.abs(fq-actual)).astype(int);extra=np.column_stack([fq,rq,gap,rq/(fq+1e-6),np.abs(fq-rq),er]);GX=np.column_stack([base,extra]);gf=OUR+['fraction_qty','rawq_qty','gap','raw_to_frac','candidate_abs_diff','effect_repair'];tr=np.where(np.isin(ms,list(sets['train'])))[0];gm=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=55,l2_regularization=3.,random_state=20260825).fit(GX[tr],y[tr]);report['gate']={}
for nm,ss in sets.items():
 ix=np.where(np.isin(ms,list(ss)))[0];p=gm.predict_proba(GX[ix])[:,1];pr=(p>=.5);chosen=np.where(pr,rq[ix],fq[ix]);report['gate'][nm]={'n':int(len(ix)),'rawWinsRate':float(y[ix].mean()),'auc':float(roc_auc_score(y[ix],p)),'balancedAccuracy':float(balanced_accuracy_score(y[ix],pr)),'chosenMaeShares':float(mean_absolute_error(actual[ix],chosen)),'rawMaeShares':float(mean_absolute_error(actual[ix],rq[ix])),'fractionMaeShares':float(mean_absolute_error(actual[ix],fq[ix]))}
joblib.dump({'features':OUR,'repairFractionModel':mods['REPAIR'][0],'addFractionModel':mods['ADD'][0],'repairRawModel':mods['REPAIR'][1],'addRawModel':mods['ADD'][1],'gateModel':gm,'gateFeatures':gf,'version':report['version']},D/'r3_active_quantity_strictpast_v2.joblib');(D/'r3_active_quantity_strictpast_v2_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
