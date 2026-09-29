from __future__ import annotations
import json,math
from pathlib import Path
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SPLITS={'train':('r2_residual_multicheckpoint_train15_v0.json','r2_residual_episode_abort_values_train15_v0.json'),'validation':('r2_residual_multicheckpoint_validation15_v0.json','r2_residual_episode_abort_values_validation15_v0.json'),'forward':('r2_residual_multicheckpoint_forward20_v0.json','r2_residual_episode_abort_values_forward20_v0.json')}
priority={int(r['marketId']):float(r['priorityPnl']) for r in json.loads((D/'r2_residual_opposite_priority_clean_v0.json').read_text())['rows']}
def load(name):
 featf,abortf=SPLITS[name]; fd=json.loads((D/featf).read_text())['rowsData']; ad={int(r['marketId']):r for r in json.loads((D/abortf).read_text())['rows']}
 rows=[]
 for r in fd:
  if int(r.get('candidateDelayMs') or 0)!=0: continue
  mid=int(r['marketId']); f=r.get('features') or {}; ap=float(ad[mid]['abortPnl']); pp=float(priority[mid]); rows.append({'marketId':mid,'features':f,'abortPnl':ap,'priorityPnl':pp,'y':int(ap>pp+1e-9)})
 return rows
allrows={k:load(k) for k in SPLITS}
keys=sorted(set().union(*(set(r['features']) for rows in allrows.values() for r in rows)))
# drop identifiers / obvious non-runtime bookkeeping if present
keys=[k for k in keys if k not in {'marketId','winner','pnl','oraclePnl','baselinePnl'}]
def mat(rows):
 X=[]
 for r in rows:
  X.append([float(r['features'].get(k)) if r['features'].get(k) is not None and isinstance(r['features'].get(k),(int,float)) and math.isfinite(float(r['features'].get(k))) else np.nan for k in keys])
 return np.asarray(X,float),np.asarray([r['y'] for r in rows],int)
def eval_model(model,rows):
 X,y=mat(rows);p=model.predict_proba(X)[:,1];pred=p>=.5
 base=sum(r['priorityPnl'] for r in rows); pol=sum((r['abortPnl'] if z else r['priorityPnl']) for r,z in zip(rows,pred)); oracle=sum(max(r['abortPnl'],r['priorityPnl']) for r in rows)
 return {'n':len(rows),'abortPositive':int(y.sum()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(set(y))>1 else None,'alwaysPriorityPnl':base,'policyPnl':pol,'oraclePnl':oracle,'policyLift':pol-base,'oracleLift':oracle-base,'capture':(pol-base)/(oracle-base) if abs(oracle-base)>1e-9 else None,'predAbort':int(pred.sum()),'rows':[{'marketId':r['marketId'],'y':r['y'],'pAbort':float(q),'chosen':'ABORT' if z else 'CONTINUE','priorityPnl':r['priorityPnl'],'abortPnl':r['abortPnl']} for r,q,z in zip(rows,p,pred)]}
def fit_eval(train_names,test_name):
 tr=sum((allrows[n] for n in train_names),[]);X,y=mat(tr)
 models={'LOGIT':make_pipeline(SimpleImputer(strategy='median'),StandardScaler(),LogisticRegression(C=1.0,class_weight='balanced',max_iter=2000,random_state=1)),'HGB':make_pipeline(SimpleImputer(strategy='median'),HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=7,l2_regularization=1.0,random_state=1))}
 out={}
 for nm,m in models.items():m.fit(X,y);out[nm]={'train':eval_model(m,tr),'test':eval_model(m,allrows[test_name])}
 return out
rep={'version':'R2_RESIDUAL_EPISODE_ABORT_SELECTOR_V0','researchOnly':True,'features':keys,'label':'ABORT iff STOP_NEW_INTENTS final executable PnL > continuous opposite-priority final executable PnL','stage1_train15_validation15':fit_eval(['train'],'validation'),'stage2_train30_forward20':fit_eval(['train','validation'],'forward'),'guards':['no fresh20 used','natural 0.5 threshold only','no hyperparameter sweep','strict-past first residual features only']}
out=D/'r2_residual_episode_abort_selector_v0_report.json';out.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
