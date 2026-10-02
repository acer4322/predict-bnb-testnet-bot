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
fill=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=700,early_stopping_rounds=60,min_samples_leaf=18,n_jobs=-2,random_state=SEED);fill.fit(X,y)
# markout old + recent train
om=pd.read_csv(D/'r2_fill_quality_explicit_v1_dataset.csv'); om=om[om.label_markout1s_ticks.notna()].copy(); trm=rm[rm.market_id.astype(int).isin(train)&rm.label_markout1s_ticks.notna()].copy(); mf=pd.concat([om,trm],ignore_index=True,sort=False);Xm=mf[FEATURES].apply(pd.to_numeric,errors='coerce');ym=mf.label_markout1s_ticks.astype(float)
mark=ExplainableBoostingRegressor(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=700,early_stopping_rounds=60,min_samples_leaf=10,n_jobs=-2,random_state=SEED+1);mark.fit(Xm,ym)
def fmet(df):
 xx=df[FEATURES].apply(pd.to_numeric,errors='coerce');yy=df.label_fill5s.astype(int);pp=fill.predict_proba(xx)[:,1];return {'n':len(yy),'rate':float(yy.mean()),'predMean':float(pp.mean()),'auc':float(roc_auc_score(yy,pp)),'ap':float(average_precision_score(yy,pp)),'logLoss':float(log_loss(yy,pp,labels=[0,1]))}
def mmet(df):
 df=df[df.label_markout1s_ticks.notna()];xx=df[FEATURES].apply(pd.to_numeric,errors='coerce');yy=df.label_markout1s_ticks.astype(float);pp=mark.predict(xx);return {'n':len(yy),'mae':float(mean_absolute_error(yy,pp)),'rmse':float(mean_squared_error(yy,pp)**.5),'meanTarget':float(yy.mean()),'meanPred':float(pp.mean()),'signAccuracy':float(np.mean(np.sign(yy)==np.sign(pp)))}
metrics={'fill5s':{},'markout1s':{}}
for k,mids in [('validation',spl['validation']),('test',spl['test'])]:
 z=rs[rs.market_id.astype(int).isin(mids)].copy();metrics['fill5s'][k]=fmet(z);zm=rm[rm.market_id.astype(int).isin(mids)].copy();metrics['markout1s'][k]=mmet(zm)
art={'version':'R2_BROAD_EXECUTION_VALUE_TEACHER_V0','features':FEATURES,'models':{'fill5s':fill,'markout1s':mark},'recentSplits':spl,'trainingMarketsRecent':sorted(train),'includesOldExecutionMarkets':sorted(old.market_id.astype(int).unique().tolist()),'runtimeTargetDataAllowed':False,'dreamFillAllowed':False}
joblib.dump(art,D/'r2_broad_execution_value_teacher_v0.joblib');(D/'r2_broad_execution_value_teacher_v0_report.json').write_text(json.dumps({'metrics':metrics,'fitRowsFill':len(fit),'fitRowsMarkout':len(mf),'recentSplits':spl},indent=2),encoding='utf-8');print(json.dumps({'ok':True,'metrics':metrics,'fitRowsFill':len(fit),'fitRowsMarkout':len(mf)}))
