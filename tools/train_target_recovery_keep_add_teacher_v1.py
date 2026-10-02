from __future__ import annotations
import json
from pathlib import Path
import joblib, pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, accuracy_score, balanced_accuracy_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_maker_taker_coordination_big_v1/target_maker_resting_order_state_machine_v0_rows.csv'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/target_recovery_keep_add_teacher_v1_report.json'
ART=ROOT/'data/research/execution_aware_fill_lifecycle_v0/target_recovery_keep_add_teacher_v1.joblib'
x=pd.read_csv(SRC)
x=x[x['inventoryRole'].eq('MINORITY') & x['action'].isin(['KEEP_PROXY','SAME_PRICE_ADD','SAME_NEAR_1_3T_ADD','SAME_FAR_4T_PLUS_ADD'])].copy()
x['label_add']=x['action'].ne('KEEP_PROXY').astype(int)
num=['currentAgeMs','quoteOffsetTicks','secondsLeft','absNet','pairedCoverage','directionScore','spotReturn1sBps','futuresReturn1sBps','activeSameCount']
cat=['ageBin','quoteOffsetBin','timeBin','absNetBin','directionAlignment','volatilityAlert','side']
features=num+cat
open_ms=x.groupby('marketId').checkpointMs.min().sort_values()
markets=[int(m) for m in open_ms.index.tolist()]; n=len(markets); c1=int(n*.70); c2=int(n*.85)
parts={'train':set(markets[:c1]),'validation':set(markets[c1:c2]),'forward':set(markets[c2:])}
pre=ColumnTransformer([('num',Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler())]),num),('cat',Pipeline([('imp',SimpleImputer(strategy='most_frequent')),('oh',OneHotEncoder(handle_unknown='ignore'))]),cat)])
model=Pipeline([('pre',pre),('lr',LogisticRegression(max_iter=2000,class_weight='balanced',C=1.0))])
tr=x[x.marketId.isin(parts['train'])]; model.fit(tr[features],tr.label_add)
res={}
for name,ms in parts.items():
 d=x[x.marketId.isin(ms)]; p=model.predict_proba(d[features])[:,1]; pred=(p>=.5).astype(int); majority=int(tr.label_add.mean()>=.5)
 res[name]={'markets':len(ms),'rows':len(d),'startMs':int(d.checkpointMs.min()),'endMs':int(d.checkpointMs.max()),'addRate':float(d.label_add.mean()),'auc':float(roc_auc_score(d.label_add,p)),'accuracy':float(accuracy_score(d.label_add,pred)),'balancedAccuracy':float(balanced_accuracy_score(d.label_add,pred)),'logLoss':float(log_loss(d.label_add,p,labels=[0,1])),'majorityAccuracy':float((d.label_add==majority).mean())}
joblib.dump({'version':'TARGET_RECOVERY_KEEP_ADD_TEACHER_V1','model':model,'features':features,'numFeatures':num,'catFeatures':cat,'runtimeTargetAllowed':False,'splitMarkets':{k:sorted(v) for k,v in parts.items()}},ART)
rep={'version':'TARGET_RECOVERY_KEEP_ADD_TEACHER_V1','researchOnly':True,'liveTradingChanges':False,'targetUsedAtRuntime':False,'question':'Given a Target minority/recovery-side parent that remains in lifecycle, can strict-past state distinguish KEEP old queue from same-side ADD?','label':{'KEEP':'KEEP_PROXY','ADD':['SAME_PRICE_ADD','SAME_NEAR_1_3T_ADD','SAME_FAR_4T_PLUS_ADD'],'excluded':['OPP_FIRST','END_NO_NEW'],'warning':'MINORITY is an observable recovery proxy, not Target private repair intent. SAME_* is inferred parent overlap, not private cancel/replace ground truth.'},'features':features,'split':{'method':'true chronological by per-market minimum checkpointMs, 70/15/15','counts':{k:len(v) for k,v in parts.items()}},'coverage':{'rows':len(x),'markets':x.marketId.nunique(),'addRate':float(x.label_add.mean())},'results':res,'artifact':str(ART.relative_to(ROOT)),'promotion':'IMITATION PRIOR ONLY; must show monotonic relationship to R2 HftBacktest KEEP vs KEEP+ADD action value before any authority.'}
OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
