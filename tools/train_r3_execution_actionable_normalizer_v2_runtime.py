from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data/research/r3_v0'; R=np.random.default_rng(20260825)
N=220000; rows=[]
for _ in range(N):
 target=float(np.exp(R.uniform(math.log(.25),math.log(180.0))))
 confirmed=float(target*R.beta(1.4,1.5));
 if R.random()<.12: confirmed=target
 unresolved=max(0.,target-confirmed)
 mode=int(R.choice(5,p=[.32,.14,.12,.25,.17])) # 0 live,1 partiallive,2 uncertain/cancel,3 released residual,4 none/full
 if mode==0:
  live_remaining=unresolved; active_count=int(R.integers(1,4)); partial=0.; uncertain=0.; terminal=0.; actionable=0.
 elif mode==1:
  live_remaining=unresolved; active_count=int(R.integers(1,3)); partial=float(confirmed/max(target,1e-9)); uncertain=0.; terminal=0.; actionable=0.
 elif mode==2:
  live_remaining=unresolved; active_count=int(R.integers(1,3)); partial=float(confirmed/max(target,1e-9)); uncertain=1.; terminal=0.; actionable=0.
 elif mode==3:
  live_remaining=0.; active_count=0; partial=float(confirmed/max(target,1e-9)); uncertain=0.; terminal=1.; actionable=unresolved
 else:
  live_remaining=0.; active_count=0; partial=1.; uncertain=0.; terminal=1.; actionable=0.
 age=float(R.uniform(0,60000)) if active_count else 0.
 # raw gap is economic observed imbalance plus execution-owned exposure pressure/noise
 raw_gap=max(0., (unresolved + live_remaining*R.uniform(.2,1.2))*np.exp(R.normal(0,.18)))
 dust=max(.01,.0015*target)
 if actionable<=dust: actionable=0.
 ratio=0. if raw_gap<=1e-9 else min(1.,actionable/raw_gap)
 rows.append([raw_gap,live_remaining,active_count,partial,age,uncertain,terminal,math.log1p(raw_gap),math.log1p(live_remaining),live_remaining/max(raw_gap,1e-9),ratio])
A=np.asarray(rows,float); X=A[:,:-1]; y=A[:,-1]; n=len(A); a=int(.7*n); b=int(.85*n)
m=HistGradientBoostingRegressor(max_iter=300,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=70,l2_regularization=3.5,random_state=20260825).fit(X[:a],y[:a])
def sc(lo,hi):
 p=np.clip(m.predict(X[lo:hi]),0,1); yy=y[lo:hi]; base=np.full_like(yy,np.median(y[:a])); bm=mean_absolute_error(yy,base); mm=mean_absolute_error(yy,p)
 return {'n':int(hi-lo),'mae':float(mm),'baselineMae':float(bm),'lift':float(bm-mm),'labelMean':float(yy.mean()),'predMean':float(p.mean()),'zeroLabelRate':float(np.mean(yy==0))}
features=['raw_gap','live_remaining','active_count','partial_ratio','max_age_ms','uncertain','terminal_no_owner','log_raw_gap','log_live_remaining','live_remaining_over_gap']
rep={'version':'R3_EXECUTION_ACTIONABLE_NORMALIZER_V2_RUNTIME','researchOnly':True,'fixed18Forbidden':True,'latentTargetQtyHidden':True,'features':features,'train':sc(0,a),'validation':sc(a,b),'test':sc(b,n),'semantics':'R2.1 lifecycle facts modulate how much raw imbalance is currently unowned/actionable; no action authority.'}
joblib.dump({'model':m,'features':features,'version':rep['version']},OUT/'r3_execution_actionable_normalizer_hgb_v2_runtime.joblib')
(OUT/'r3_execution_actionable_normalizer_v2_runtime_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
print(json.dumps(rep,indent=2))
