from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler,OneHotEncoder
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss,roc_auc_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1]; SRC=ROOT/'data/research/target_maker_taker_coordination_big_v1/target_maker_resting_order_state_machine_v0_rows.csv'; OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/target_inverse_choice_hierarchy_v0_report.json'
x=pd.read_csv(SRC); x=x[x.inventoryRole.eq('MINORITY') & x.action.isin(['KEEP_PROXY','SAME_PRICE_ADD','SAME_NEAR_1_3T_ADD','SAME_FAR_4T_PLUS_ADD','OPP_FIRST'])].copy(); x['coarse']=np.where(x.action.eq('KEEP_PROXY'),'KEEP',np.where(x.action.eq('OPP_FIRST'),'OPP','ADD')); sign=np.where(x.side.eq('UP'),1.,-1.)
for c in ['directionScore','spotReturn1sBps','futuresReturn1sBps']: x[c+'_toward_recovery']=pd.to_numeric(x[c],errors='coerce')*sign
groups={'QUEUE_OPTIONALITY':['currentAgeMs','quoteOffsetTicks','activeSameCount'],'INVENTORY_REPAIR':['absNet','pairedCoverage'],'ADVERSE_SELECTION':['directionScore_toward_recovery','spotReturn1sBps_toward_recovery','futuresReturn1sBps_toward_recovery','volatilityAlert'],'URGENCY':['secondsLeft']}
order=x.groupby('marketId').checkpointMs.min().sort_values().index.tolist(); n=len(order); a=int(n*.70); b=int(n*.85); parts={'train':set(order[:a]),'validation':set(order[a:b]),'forward':set(order[b:])}
def taskframe(task):
 d=x.copy()
 if task=='KEEP_VS_ACT': d['y']=(d.coarse!='KEEP').astype(int)
 else: d=d[d.coarse!='KEEP'].copy(); d['y']=(d.coarse=='ADD').astype(int)
 return d
def run(task,use):
 d=taskframe(task); cols=sum((groups[g] for g in use),[]); cat=[c for c in cols if c=='volatilityAlert']; num=[c for c in cols if c not in cat]; trs=[]
 if num: trs.append(('n',Pipeline([('i',SimpleImputer(strategy='median')),('s',StandardScaler())]),num))
 if cat: trs.append(('c',Pipeline([('i',SimpleImputer(strategy='most_frequent')),('o',OneHotEncoder(handle_unknown='ignore'))]),cat))
 m=Pipeline([('p',ColumnTransformer(trs)),('lr',LogisticRegression(max_iter=3000,class_weight='balanced',C=1.0))]); tr=d[d.marketId.isin(parts['train'])]; m.fit(tr[cols],tr.y)
 out={}
 for s,ms in parts.items():
  z=d[d.marketId.isin(ms)]; p=m.predict_proba(z[cols])[:,1]; pr=(p>=.5).astype(int); out[s]={'rows':len(z),'rate':float(z.y.mean()),'auc':float(roc_auc_score(z.y,p)),'logLoss':float(log_loss(z.y,p,labels=[0,1])),'balancedAccuracy':float(balanced_accuracy_score(z.y,pr))}
 return out
rep={'version':'TARGET_INVERSE_CHOICE_HIERARCHY_V0','researchOnly':True,'runtimeAuthority':False,'basis':groups,'split':'true chronology 70/15/15','tasks':{}}
for task in ['KEEP_VS_ACT','ADD_VS_OPP']:
 full=run(task,list(groups)); ab={g:run(task,[h for h in groups if h!=g]) for g in groups}; rep['tasks'][task]={'full':full,'leaveOneOut':ab,'deltaLogLossVsFull':{g:{s:ab[g][s]['logLoss']-full[s]['logLoss'] for s in ['validation','forward']} for g in groups}}
OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
