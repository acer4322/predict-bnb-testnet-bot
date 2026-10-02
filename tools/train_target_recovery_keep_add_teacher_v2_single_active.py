from __future__ import annotations
import json
from pathlib import Path
import joblib,pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder,StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,accuracy_score,log_loss
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=ROOT/'data/research/target_maker_taker_coordination_big_v1/target_maker_resting_order_state_machine_v0_rows.csv'; OUT=D/'target_recovery_keep_add_teacher_v2_single_active_report.json'; ART=D/'target_recovery_keep_add_teacher_v2_single_active.joblib'
x=pd.read_csv(SRC); acts=['KEEP_PROXY','SAME_PRICE_ADD','SAME_NEAR_1_3T_ADD','SAME_FAR_4T_PLUS_ADD']; x=x[x.inventoryRole.eq('MINORITY') & x.action.isin(acts) & x.activeSameCount.eq(1)].copy(); x['label_add']=x.action.ne('KEEP_PROXY').astype(int)
num=['currentAgeMs','quoteOffsetTicks','secondsLeft','absNet','pairedCoverage','directionScore','spotReturn1sBps','futuresReturn1sBps']; cat=['ageBin','quoteOffsetBin','timeBin','absNetBin','directionAlignment','volatilityAlert','side']; features=num+cat
opens=x.groupby('marketId').checkpointMs.min().sort_values(); mids=[int(z) for z in opens.index]; n=len(mids); a=int(n*.70); b=int(n*.85); parts={'train':set(mids[:a]),'validation':set(mids[a:b]),'forward':set(mids[b:])}
pre=ColumnTransformer([('n',Pipeline([('i',SimpleImputer(strategy='median')),('s',StandardScaler())]),num),('c',Pipeline([('i',SimpleImputer(strategy='most_frequent')),('o',OneHotEncoder(handle_unknown='ignore'))]),cat)]); model=Pipeline([('pre',pre),('lr',LogisticRegression(max_iter=2000,class_weight='balanced'))]); tr=x[x.marketId.isin(parts['train'])]; model.fit(tr[features],tr.label_add)
res={}
for k,ms in parts.items():
 d=x[x.marketId.isin(ms)]; p=model.predict_proba(d[features])[:,1]; y=d.label_add.astype(int); pred=(p>=.5).astype(int); res[k]={'markets':len(ms),'rows':len(d),'addRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'accuracy':float(accuracy_score(y,pred)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
joblib.dump({'version':'TARGET_RECOVERY_KEEP_ADD_TEACHER_V2_SINGLE_ACTIVE','model':model,'features':features,'runtimeTargetAllowed':False},ART)
rep={'version':'TARGET_RECOVERY_KEEP_ADD_TEACHER_V2_SINGLE_ACTIVE','researchOnly':True,'liveTradingChanges':False,'targetRuntimeInput':False,'stateMatch':'MINORITY recovery proxy + lifecycle active + exactly one inferred active same-side parent; predicts KEEP_ONLY vs ADD_SECOND_PARENT','coverage':{'rows':len(x),'markets':x.marketId.nunique(),'addRate':float(x.label_add.mean())},'split':'true chronological by per-market first checkpointMs 70/15/15','features':features,'results':res,'artifact':str(ART.relative_to(ROOT)),'promotion':'IMITATION PRIOR ONLY; must transfer monotonically to R2 matched HftBacktest action-value.'}; OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
