from __future__ import annotations
import json,sys
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,f1_score,log_loss
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'research'/'supervisor_options_v0';CSV=OUT/'supervisor_target_act_states_v1.csv';N=int(sys.argv[1]);REPORT=OUT/f'supervisor_target_act_curve_n{N}_v1.json';ART=OUT/'supervisor_target_act_large_v1.joblib'

def weights(y):
 c=y.value_counts();n=len(y);mp={k:np.sqrt(n/max(1,int(v))) for k,v in c.items()};w=y.map(mp).astype(float).to_numpy();return w/w.mean()
d=pd.read_csv(CSV).sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True);meta=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=meta.market_id.astype(int).tolist();train_pool=ids[:489];val_ids=set(ids[489:594]);test_ids=set(ids[594:699]);tr_ids=set(train_pool[:N]);tr=d[d.market_id.astype(int).isin(tr_ids)].copy();va=d[d.market_id.astype(int).isin(val_ids)].copy();te=d[d.market_id.astype(int).isin(test_ids)].copy();features=[c for c in d.columns if c not in {'market_id','market_end_ms','checkpoint_ms','gate_act'}]
# Freeze runtime-safe feature family; all columns originate from strict-past build.
X=tr[features].apply(pd.to_numeric,errors='coerce');y=tr.gate_act.astype(str);m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=70,max_leaf_nodes=15,min_samples_leaf=100,l2_regularization=1.0,early_stopping=True,validation_fraction=.1,n_iter_no_change=15,random_state=20260820);m.fit(X,y,sample_weight=weights(y))
def met(x):
 yy=x.gate_act.astype(str);cl=list(map(str,m.classes_));pi=cl.index('ACT');xx=x[features].apply(pd.to_numeric,errors='coerce');p=m.predict_proba(xx)[:,pi];pred=pd.Series(m.predict(xx),index=x.index).eq('ACT').astype(int);yb=yy.eq('ACT').astype(int);return {'n':len(x),'positiveRate':float(yb.mean()),'predRate':float(pred.mean()),'auc':float(roc_auc_score(yb,p)),'ap':float(average_precision_score(yb,p)),'balancedAccuracy':float(balanced_accuracy_score(yb,pred)),'f1':float(f1_score(yb,pred,zero_division=0)),'logLoss':float(log_loss(yb,np.column_stack([1-p,p]),labels=[0,1]))}
rep={'reportVersion':'SUPERVISOR_TARGET_ACT_LEARNING_CURVE_VARIANT_V1','researchOnly':True,'trainMarkets':N,'trainRows':len(tr),'trainCounts':tr.gate_act.value_counts().to_dict(),'validation':met(va),'test':met(te),'features':len(features),'guards':['No winner/PnL','Ordinary Level-1 only','Same fixed chronological validation/test','No runtime changes']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');
if N==489:joblib.dump({'version':'SUPERVISOR_TARGET_ACT_LARGE_V1','researchOnly':True,'runtimePromotion':False,'model':m,'features':features,'trainingMarkets':sorted(tr_ids),'semantics':'Target-state Level-1 ACT vs HOLD teacher; strict-past only; not directly deployable to OUR state without student-state adapter'},ART)
print(json.dumps(rep,ensure_ascii=False,indent=2))