from __future__ import annotations
import json,sqlite3,math
from pathlib import Path
from collections import deque
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,accuracy_score
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'
FR=joblib.load(D/'r3_active_our_state_student_v2_pilot600.joblib'); RQ=joblib.load(D/'r3_active_rawq_our_student_pilot300_v2_batch.joblib')
F=FR['features']
def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.
def our(up,down,cost,fees,hist,t,role,side,px,sh,i,n):
 gross=up+down;net=up-down;ab=abs(net);fl=min(up-cost-fees,down-cost-fees);best=max(up-cost-fees,down-cost-fees);r5=[x for x in hist if t-x[0]<=5000];r10=[x for x in hist if t-x[0]<=10000];lm=[x for x in hist if x[1]=='MAKER'];lt=[x for x in hist if x[1]=='TAKER'];old=r10[0] if r10 else (hist[0] if hist else (t,role,side,sh,ab,fl,best))
 return {'combined_gross':gross,'combined_net':net,'combined_abs_net':ab,'combined_imbalance_ratio':ab/gross if gross else 0.,'combined_paired_coverage':2*min(up,down)/gross if gross else 0.,'worst_case_floor':fl,'best_case_pnl':best,'abs_payoff_gap':abs(best-fl),'last_maker_age_ms':float(t-lm[-1][0]) if lm else 1e6,'last_taker_age_ms':float(t-lt[-1][0]) if lt else 1e6,'maker_fills_5s':sum(x[1]=='MAKER' for x in r5),'maker_fills_10s':sum(x[1]=='MAKER' for x in r10),'taker_fills_5s':sum(x[1]=='TAKER' for x in r5),'taker_fills_10s':sum(x[1]=='TAKER' for x in r10),'maker_shares_5s':sum(x[3] for x in r5 if x[1]=='MAKER'),'maker_shares_10s':sum(x[3] for x in r10 if x[1]=='MAKER'),'taker_shares_5s':sum(x[3] for x in r5 if x[1]=='TAKER'),'taker_shares_10s':sum(x[3] for x in r10 if x[1]=='TAKER'),'combined_absnet_change_10s':ab-old[4],'event_index_norm':i/max(1,n-1),'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.}
con=sqlite3.connect(DB); mids=[r[0] for r in con.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")][:1200]; rows=[]
for mid in mids:
 rr=con.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall();up=down=cost=fees=0.;hist=deque();N=len(rr)
 for i,(role,side,t,px,sh) in enumerate(rr):
  role=str(role);side=str(side);t=int(t);px=float(px);sh=float(sh)
  if hist and role=='TAKER' and abs(up-down)>1e-6:
   o=our(up,down,cost,fees,hist,t,role,side,px,sh,i,N); rows.append((mid,side,sh,[o[k] for k in F],abs(up-down)))
  if side=='UP':up+=sh
  else:down+=sh
  cost+=px*sh;fees+=fee(sh,px,role);hist.append((t,role,side,sh,abs(up-down),min(up-cost-fees,down-cost-fees),max(up-cost-fees,down-cost-fees)))
  while hist and t-hist[0][0]>15000:hist.popleft()
con.close(); X=np.asarray([r[3] for r in rows],float); gap=np.asarray([r[4] for r in rows],float); actual=np.asarray([r[2] for r in rows],float)
# infer effect structurally from action side vs current net sign reconstructed from combined_net feature
net_ix=F.index('combined_net'); net=X[:,net_ix]; side=np.asarray([r[1] for r in rows]); repair=((net>0)&(side=='DOWN'))|((net<0)&(side=='UP'))
frac=np.empty(len(rows)); rawq=np.empty(len(rows))
for mask, fm, rm in [(repair,FR['repairQtyModel'],RQ['repairModel']),(~repair,FR['addQtyModel'],RQ['addModel'])]:
 idx=np.where(mask)[0]; frac[idx]=np.clip(fm.predict(X[idx]),0,3)*gap[idx]; rawq[idx]=np.maximum(.01,np.expm1(rm.predict(X[idx])))
y=(np.abs(rawq-actual)<=np.abs(frac-actual)).astype(int)
extra=np.column_stack([frac,rawq,gap,rawq/(frac+1e-6),np.abs(frac-rawq),repair.astype(float)])
GX=np.column_stack([X,extra]); features=F+['fraction_qty','rawq_qty','gap','raw_to_frac','candidate_abs_diff','effect_repair']
uniq=sorted(set(r[0] for r in rows));a=int(.6*len(uniq));b=int(.8*len(uniq));sets={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])}; ms=np.asarray([r[0] for r in rows])
tr=np.where(np.isin(ms,list(sets['train'])))[0]; model=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=55,l2_regularization=3.,random_state=20260825).fit(GX[tr],y[tr])
rep={'version':'R3_ACTIVE_QUANTITY_GATE_V1','researchOnly':True,'fixed18Forbidden':True,'markets':len(uniq),'rows':len(rows),'features':features,'label':'1=rawQ closer/equal to Target actual shares; 0=fraction-derived closer. No winner/PnL label.','splits':{}}
for nm,ss in sets.items():
 ix=np.where(np.isin(ms,list(ss)))[0]; p=model.predict_proba(GX[ix])[:,1]; pr=(p>=.5).astype(int); chosen=np.where(pr==1,rawq[ix],frac[ix]); oracle=np.minimum(np.abs(rawq[ix]-actual[ix]),np.abs(frac[ix]-actual[ix])); rep['splits'][nm]={'n':int(len(ix)),'rawWinsRate':float(y[ix].mean()),'auc':float(roc_auc_score(y[ix],p)) if len(np.unique(y[ix]))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y[ix],pr)),'chosenMaeShares':float(np.mean(np.abs(chosen-actual[ix]))),'rawOnlyMaeShares':float(np.mean(np.abs(rawq[ix]-actual[ix]))),'fractionOnlyMaeShares':float(np.mean(np.abs(frac[ix]-actual[ix]))),'oracleMaeShares':float(np.mean(oracle))}
joblib.dump({'model':model,'features':features,'baseFeatures':F,'version':rep['version']},D/'r3_active_quantity_gate_v1.joblib');(D/'r3_active_quantity_gate_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
