from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, balanced_accuracy_score, accuracy_score
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_maker_taker_coordination_big_v1/target_maker_resting_order_state_machine_v0_rows.csv'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/target_minimum_missing_objective_v0_report.json'
x=pd.read_csv(SRC)
# Recovery-side, lifecycle-active decisions only. END excluded; actions collapsed to KEEP / ADD / OPP_FIRST.
x=x[x.inventoryRole.eq('MINORITY') & x.action.isin(['KEEP_PROXY','SAME_PRICE_ADD','SAME_NEAR_1_3T_ADD','SAME_FAR_4T_PLUS_ADD','OPP_FIRST'])].copy()
x['act']=np.where(x.action.eq('KEEP_PROXY'),'KEEP',np.where(x.action.eq('OPP_FIRST'),'OPP_FIRST','ADD'))
# Signed public alpha toward current recovery side.
sign=np.where(x.side.eq('UP'),1.0,-1.0)
for c in ['directionScore','spotReturn1sBps','futuresReturn1sBps']:
    x[c+'_toward_recovery']=pd.to_numeric(x[c],errors='coerce')*sign
# Small interpretable objective basis. These are state proxies, not claimed true rewards.
groups={
 'QUEUE_OPTIONALITY':['currentAgeMs','quoteOffsetTicks','activeSameCount'],
 'INVENTORY_REPAIR':['absNet','pairedCoverage'],
 'ADVERSE_SELECTION':['directionScore_toward_recovery','spotReturn1sBps_toward_recovery','futuresReturn1sBps_toward_recovery','volatilityAlert'],
 'URGENCY':['secondsLeft'],
}
# true chronological split by first observed checkpoint per market
order=x.groupby('marketId').checkpointMs.min().sort_values().index.tolist(); n=len(order); c1=int(n*.70); c2=int(n*.85)
parts={'train':set(order[:c1]),'validation':set(order[c1:c2]),'forward':set(order[c2:])}

def fit_eval(use_groups):
    cols=[]
    for g in use_groups: cols+=groups[g]
    cat=[c for c in cols if c=='volatilityAlert']; num=[c for c in cols if c not in cat]
    trans=[]
    if num: trans.append(('num',Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler())]),num))
    if cat: trans.append(('cat',Pipeline([('imp',SimpleImputer(strategy='most_frequent')),('oh',OneHotEncoder(handle_unknown='ignore'))]),cat))
    pre=ColumnTransformer(trans)
    m=Pipeline([('pre',pre),('lr',LogisticRegression(max_iter=3000,class_weight='balanced',C=1.0))])
    tr=x[x.marketId.isin(parts['train'])]; m.fit(tr[cols],tr.act)
    out={}
    for name,ms in parts.items():
        d=x[x.marketId.isin(ms)]; p=m.predict_proba(d[cols]); pred=m.predict(d[cols])
        out[name]={'rows':len(d),'markets':len(ms),'logLoss':float(log_loss(d.act,p,labels=m.classes_)),'balancedAccuracy':float(balanced_accuracy_score(d.act,pred)),'accuracy':float(accuracy_score(d.act,pred)),'classRates':d.act.value_counts(normalize=True).round(6).to_dict()}
    return out
allg=list(groups)
full=fit_eval(allg)
abl={}
for g in allg:
    r=fit_eval([h for h in allg if h!=g]); abl[g]=r
rep={'version':'TARGET_MINIMUM_MISSING_OBJECTIVE_V0','researchOnly':True,'runtimeAuthority':False,'targetRuntimeInput':False,'question':'Which small interpretable execution-state objective groups are consistently necessary to explain Target recovery-side KEEP/ADD/OPP_FIRST decisions?','basis':groups,'coverage':{'rows':len(x),'markets':x.marketId.nunique(),'classes':x.act.value_counts().to_dict()},'split':'true chronological by per-market minimum checkpointMs 70/15/15','full':full,'leaveOneGroupOut':abl,'deltaLogLossVsFull':{g:{s:abl[g][s]['logLoss']-full[s]['logLoss'] for s in ['validation','forward']} for g in allg},'interpretationRule':'A candidate missing objective is only interesting if removing its group worsens log loss on BOTH validation and forward. This is an inverse-decision screen, not proof of a unique reward function.','next':'Only stable groups may be mapped into Frozen-R2 HFT execution as a bounded residual objective; no Strategy Brain retraining.'}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
