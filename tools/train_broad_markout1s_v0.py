from __future__ import annotations
import json,warnings
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingRegressor
from sklearn.metrics import mean_absolute_error,mean_squared_error
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/execution_aware_fill_lifecycle_v0';SEED=20260822
base=joblib.load(D/'r2_recent_execution_value_teacher_v0.joblib');FEATURES=list(base['features']);spl=base['splits'];train=set(spl['train']);rm=[]
for p in sorted(D.glob('recent_execution_horizon_b*.json')): rm.extend(json.loads(p.read_text(encoding='utf-8'))['markoutRows'])
rm=pd.DataFrame(rm);om=pd.read_csv(D/'r2_fill_quality_explicit_v1_dataset.csv');om=om[om.label_markout1s_ticks.notna()].copy();trm=rm[rm.market_id.astype(int).isin(train)&rm.label_markout1s_ticks.notna()].copy();fit=pd.concat([om,trm],ignore_index=True,sort=False);X=fit[FEATURES].apply(pd.to_numeric,errors='coerce');y=fit.label_markout1s_ticks.astype(float);warnings.filterwarnings('ignore')
m=ExplainableBoostingRegressor(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=4,outer_bags=2,learning_rate=.035,max_rounds=350,early_stopping_rounds=50,min_samples_leaf=10,n_jobs=-2,random_state=SEED+1);m.fit(X,y)
def met(df):
 df=df[df.label_markout1s_ticks.notna()];xx=df[FEATURES].apply(pd.to_numeric,errors='coerce');yy=df.label_markout1s_ticks.astype(float);pp=m.predict(xx);return {'n':len(yy),'mae':float(mean_absolute_error(yy,pp)),'rmse':float(mean_squared_error(yy,pp)**.5),'meanTarget':float(yy.mean()),'meanPred':float(pp.mean()),'signAccuracy':float(np.mean(np.sign(yy)==np.sign(pp)))}
metrics={k:met(rm[rm.market_id.astype(int).isin(mids)].copy()) for k,mids in [('validation',spl['validation']),('test',spl['test'])]}
joblib.dump({'version':'R2_BROAD_MARKOUT1S_V0','features':FEATURES,'model':m,'recentSplits':spl,'runtimeTargetDataAllowed':False,'dreamFillAllowed':False},D/'r2_broad_markout1s_v0.joblib');print(json.dumps({'ok':True,'fitRows':len(fit),'metrics':metrics}))
