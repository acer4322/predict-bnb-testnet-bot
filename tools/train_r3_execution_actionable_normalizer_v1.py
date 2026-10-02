from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data/research/r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
R=np.random.default_rng(20260825)
N=180000
rows=[]
owners=['CURRENT_CHILD','UNKNOWN_CHILD','CANCEL_PENDING','RELEASED','REPAIR_OBLIGATION_UNOWNED','NONE']
for _ in range(N):
    target=float(np.exp(R.uniform(math.log(.25),math.log(180.0))))
    confirmed=float(target*R.beta(1.4,1.5))
    if R.random()<.12: confirmed=target
    owner=R.choice(owners,p=[.28,.10,.10,.18,.20,.14])
    age=float(R.uniform(0,45000)); partial=float(confirmed>1e-9 and confirmed<target-1e-9)
    terminal=float(owner in {'RELEASED','REPAIR_OBLIGATION_UNOWNED','NONE'})
    uncertain=float(owner in {'UNKNOWN_CHILD','CANCEL_PENDING'})
    live_owned=float(owner=='CURRENT_CHILD')
    unresolved=max(0.,target-confirmed)
    # execution excursion: gap may include unfilled/uncertain amount while economic ownership is still live.
    noise=float(np.exp(R.normal(0,.22)))
    raw_gap=max(.0,(unresolved + R.uniform(0,.8)*target*(uncertain+live_owned))*noise)
    if owner=='REPAIR_OBLIGATION_UNOWNED': actionable=unresolved
    elif owner=='RELEASED': actionable=unresolved
    else: actionable=0.0
    # terminal dust is non-actionable
    dust=max(.01,.0015*target)
    if actionable<=dust: actionable=0.0
    ratio=0.0 if raw_gap<=1e-9 else min(1.0,actionable/raw_gap)
    rows.append([raw_gap,target,confirmed,unresolved,age,partial,terminal,uncertain,live_owned,math.log1p(raw_gap),math.log1p(target),confirmed/max(target,1e-9), ratio])
A=np.asarray(rows,float); X=A[:,:-1]; y=A[:,-1]
# chronological analogue by deterministic generation order 70/15/15
n=len(A); a=int(.7*n); b=int(.85*n)
model=HistGradientBoostingRegressor(max_iter=260,learning_rate=.055,max_leaf_nodes=25,min_samples_leaf=60,l2_regularization=3.0,random_state=20260825).fit(X[:a],y[:a])
def sc(lo,hi):
 p=np.clip(model.predict(X[lo:hi]),0,1); yy=y[lo:hi]; base=np.full_like(yy,np.median(y[:a]))
 return {'n':int(hi-lo),'mae':float(mean_absolute_error(yy,p)),'baselineMae':float(mean_absolute_error(yy,base)),'lift':float(mean_absolute_error(yy,base)-mean_absolute_error(yy,p)),'labelMean':float(yy.mean()),'predMean':float(p.mean()),'p90Pred':float(np.quantile(p,.9)),'zeroLabelRate':float(np.mean(yy==0))}
features=['raw_gap','target_qty','confirmed_qty','unresolved_qty','age_ms','partial','terminal','uncertain','live_owned','log_raw_gap','log_target_qty','fill_ratio']
rep={'version':'R3_EXECUTION_ACTIONABLE_NORMALIZER_V1','researchOnly':True,'fixed18Forbidden':True,'targetQtyDistribution':'log-uniform 0.25..180 shares; no fixed lot','teacherSemantics':'Predict actionable_ratio=actionable_unowned_terminal_obligation/raw_observed_gap. Live-owned/uncertain/cancel-pending residual is NOT actionable. Terminal dust suppressed. R2.1 facts only; no action authority.','features':features,'train':sc(0,a),'validation':sc(a,b),'test':sc(b,n)}
joblib.dump({'model':model,'features':features,'version':rep['version']},OUT/'r3_execution_actionable_normalizer_hgb_v1.joblib')
(OUT/'r3_execution_actionable_normalizer_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
print(json.dumps(rep,indent=2))
