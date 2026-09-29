from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score, balanced_accuracy_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_maker_taker_coordination_big_v1/target_maker_resting_order_state_machine_v0_rows.csv'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/target_recovery_keep_add_teacher_v0_report.json'
# Target-only imitation curriculum: minority/recovery side, lifecycle still active, KEEP vs same-side ADD.
x=pd.read_csv(SRC)
x=x[x['inventoryRole'].eq('MINORITY') & x['action'].isin(['KEEP_PROXY','SAME_PRICE_ADD','SAME_NEAR_1_3T_ADD','SAME_FAR_4T_PLUS_ADD'])].copy()
x['label_add']=x['action'].ne('KEEP_PROXY').astype(int)
# strict-past features only
num=['currentAgeMs','quoteOffsetTicks','secondsLeft','absNet','pairedCoverage','directionScore','spotReturn1sBps','futuresReturn1sBps','activeSameCount']
cat=['ageBin','quoteOffsetBin','timeBin','absNetBin','directionAlignment','volatilityAlert','side']
features=num+cat
markets=sorted(x.marketId.unique())
# chronological by market id is only a stable deterministic proxy here; report this limitation explicitly.
n=len(markets); cut1=int(n*.70); cut2=int(n*.85)
parts={'train':set(markets[:cut1]),'validation':set(markets[cut1:cut2]),'forward':set(markets[cut2:])}
pre=ColumnTransformer([('num',Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler())]),num),('cat',Pipeline([('imp',SimpleImputer(strategy='most_frequent')),('oh',OneHotEncoder(handle_unknown='ignore'))]),cat)])
model=Pipeline([('pre',pre),('lr',LogisticRegression(max_iter=2000,class_weight='balanced',C=1.0))])
tr=x[x.marketId.isin(parts['train'])]; model.fit(tr[features],tr.label_add)
res={}
for name,ms in parts.items():
 d=x[x.marketId.isin(ms)]; p=model.predict_proba(d[features])[:,1]; pred=(p>=.5).astype(int); base=int(tr.label_add.mean()>=.5)
 res[name]={'markets':len(ms),'rows':len(d),'addRate':float(d.label_add.mean()),'auc':float(roc_auc_score(d.label_add,p)) if d.label_add.nunique()>1 else None,'accuracy':float(accuracy_score(d.label_add,pred)),'balancedAccuracy':float(balanced_accuracy_score(d.label_add,pred)),'logLoss':float(log_loss(d.label_add,p,labels=[0,1])),'majorityAccuracy':float((d.label_add==base).mean())}
rep={'version':'TARGET_RECOVERY_KEEP_ADD_TEACHER_V0','researchOnly':True,'liveTradingChanges':False,'targetUsedAtRuntime':False,'question':'Given a Target minority/recovery-side parent that remains in lifecycle, can strict-past state distinguish KEEP old queue from same-side ADD?','label':{'KEEP':"KEEP_PROXY",'ADD':['SAME_PRICE_ADD','SAME_NEAR_1_3T_ADD','SAME_FAR_4T_PLUS_ADD'],'excluded':['OPP_FIRST','END_NO_NEW'],'warning':'MINORITY is an observable recovery proxy, not Target private repair intent. SAME_* is inferred parent overlap, not private cancel/replace ground truth.'},'features':features,'split':{'method':'ordered market_id 70/15/15 deterministic proxy; must be replaced by true market-open chronology before promotion','counts':{k:len(v) for k,v in parts.items()}},'coverage':{'rows':len(x),'markets':x.marketId.nunique(),'addRate':float(x.label_add.mean())},'results':res,'promotion':'DIAGNOSTIC_ONLY; Target imitation must be followed by R2 HftBacktest KEEP vs KEEP+ADD action-value validation.'}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
