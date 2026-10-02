from __future__ import annotations
import json,warnings
from pathlib import Path
import joblib,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/execution_aware_fill_lifecycle_v0';SEED=20260822
base=joblib.load(D/'r2_recent_execution_value_teacher_v0.joblib');FEATURES=list(base['features']);spl=base['splits'];train=set(spl['train']);pr=[]
for p in sorted(D.glob('recent_execution_placement_p*.json')): pr.extend(json.loads(p.read_text(encoding='utf-8'))['placementRows'])
pr=pd.DataFrame(pr);old=pd.read_csv(D/'open_order_fill_lifecycle_v0_dataset.csv');old=old[(old.order_age_ms.fillna(-1)==0)&(old.censored_3s.fillna(0).astype(int)==0)&old.label_fill_3s.notna()].copy();old['label_fill3s']=old.label_fill_3s.astype(int);tr=pr[pr.market_id.astype(int).isin(train)].copy();fit=pd.concat([old,tr],ignore_index=True,sort=False);X=fit[FEATURES].apply(pd.to_numeric,errors='coerce');y=fit.label_fill3s.astype(int);warnings.filterwarnings('ignore')
m=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=4,outer_bags=2,learning_rate=.035,max_rounds=350,early_stopping_rounds=50,min_samples_leaf=18,n_jobs=-2,random_state=SEED+3);m.fit(X,y)
def met(df):
 xx=df[FEATURES].apply(pd.to_numeric,errors='coerce');yy=df.label_fill3s.astype(int);pp=m.predict_proba(xx)[:,1];return {'n':len(yy),'rate':float(yy.mean()),'predMean':float(pp.mean()),'auc':float(roc_auc_score(yy,pp)),'ap':float(average_precision_score(yy,pp)),'logLoss':float(log_loss(yy,pp,labels=[0,1]))}
metrics={k:met(pr[pr.market_id.astype(int).isin(mids)].copy()) for k,mids in [('validation',spl['validation']),('test',spl['test'])]}
joblib.dump({'version':'R2_BROAD_PLACEMENT_FILL3S_V0','features':FEATURES,'model':m,'recentSplits':spl,'runtimeTargetDataAllowed':False,'dreamFillAllowed':False},D/'r2_broad_placement_fill3s_v0.joblib');print(json.dumps({'ok':True,'fitRows':len(fit),'oldAge0Rows':len(old),'metrics':metrics}))
