from __future__ import annotations
import json,warnings
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier,ExplainableBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,mean_absolute_error,mean_squared_error
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/execution_aware_fill_lifecycle_v0';SEED=20260822
base=joblib.load(D/'r2_recent_execution_value_teacher_v0.joblib');FEATURES=list(base['features']);spl=base['splits'];train=set(spl['train'])
# recent
rs=[];rm=[]
for p in sorted(D.glob('recent_execution_horizon_b*.json')):
 d=json.loads(p.read_text(encoding='utf-8'));rs.extend(d['stateRows']);rm.extend(d['markoutRows'])
rs=pd.DataFrame(rs);rm=pd.DataFrame(rm)
# old working order, uncensored 5s only
old=pd.read_csv(D/'open_order_fill_lifecycle_v0_dataset.csv'); old=old[(old['censored_5s'].fillna(0).astype(int)==0)&old['label_fill_5s'].notna()].copy();old['label_fill5s']=old['label_fill_5s'].astype(int)
train_rs=rs[rs.market_id.astype(int).isin(train)].copy(); fit=pd.concat([old,train_rs],ignore_index=True,sort=False)
X=fit[FEATURES].apply(pd.to_numeric,errors='coerce'); y=fit.label_fill5s.astype(int);warnings.filterwarnings('ignore')
fill=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=4,outer_bags=2,learning_rate=.035,max_rounds=350,early_stopping_rounds=60,min_samples_leaf=18,n_jobs=-2,random_state=SEED);fill.fit(X,y)

def met(df):
 xx=df[FEATURES].apply(pd.to_numeric,errors='coerce');yy=df.label_fill5s.astype(int);pp=fill.predict_proba(xx)[:,1];return {'n':len(yy),'rate':float(yy.mean()),'predMean':float(pp.mean()),'auc':float(roc_auc_score(yy,pp)),'ap':float(average_precision_score(yy,pp)),'logLoss':float(log_loss(yy,pp,labels=[0,1]))}
metrics={k:met(rs[rs.market_id.astype(int).isin(mids)].copy()) for k,mids in [('validation',spl['validation']),('test',spl['test'])]}
joblib.dump({'version':'R2_BROAD_FILL5S_V0','features':FEATURES,'model':fill,'recentSplits':spl,'runtimeTargetDataAllowed':False,'dreamFillAllowed':False},D/'r2_broad_fill5s_v0.joblib')
print(json.dumps({'ok':True,'fitRows':len(fit),'metrics':metrics}))
