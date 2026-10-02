from __future__ import annotations
import json,math,sys
from pathlib import Path
import numpy as np, joblib
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline
from sklearn.ensemble import RandomForestClassifier,ExtraTreesClassifier,HistGradientBoostingClassifier
from sklearn.metrics import accuracy_score,f1_score,confusion_matrix
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=BASE/'theory_execution_handoff_curriculum_v1.jsonl'
OUT=BASE/'r2_dynamic_lifecycle_supervisor_automl_v1_report.json'
MODEL=BASE/'r2_dynamic_lifecycle_supervisor_automl_v1.joblib'
FEATURES=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty','absTrackingError','trackingError','actualMakerNet','actualCombinedGross','secondsLeft','recoveryBid','recoveryAsk','recoverySpreadTicks','pairAskSum','pairBidSum','marginalSurplusChunkAvgCost','lockedPairEdgePerShare','lastMakerFillAgeMs','lastMakerFillSideIsRecovery','directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery','futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery','asymmetryAgeMs','observationDelayMs','hasPriorObservation','elapsedSincePriorMs','recoveryStatusNew','recoveryStatusPartial']
ACTIONS=['WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETURN_TO_CONTROLLER']
def finite(v):
 try:
  x=float(v);return x if math.isfinite(x) else np.nan
 except:return np.nan
def load():
 rows=[json.loads(x) for x in SRC.read_text(encoding='utf8').splitlines() if x.strip()];mids=sorted({int(r['marketId']) for r in rows});n=len(mids);i1=int(n*.7);i2=int(n*.85);split={'train':set(mids[:i1]),'validation':set(mids[i1:i2]),'holdout':set(mids[i2:])};return rows,mids,split
def xy(rows,mset):
 rr=[r for r in rows if int(r['marketId']) in mset];X=np.asarray([[finite((r.get('features') or {}).get(k)) for k in FEATURES] for r in rr],float);y=[str(r['teacherAction']) for r in rr];return rr,X,y
def train_heads(kind,X,y):
 ya=np.asarray([a!='WAIT_FOR_CLARITY' and a!='RETURN_TO_CONTROLLER' for a in y],int);yw=np.asarray([a=='WAIT_FOR_CLARITY' for a in y],int);mask=ya==1;yr=np.asarray([a=='REPLACE_ROUTE' for a in np.asarray(y)[mask]],int)
 def mdl():
  if kind=='hist':return HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=160,l2_regularization=5,random_state=20260823)
  if kind=='rf':return make_pipeline(SimpleImputer(strategy='median'),RandomForestClassifier(n_estimators=300,min_samples_leaf=4,max_features=.7,n_jobs=-1,random_state=20260823,class_weight='balanced'))
  return make_pipeline(SimpleImputer(strategy='median'),ExtraTreesClassifier(n_estimators=300,min_samples_leaf=4,max_features=.8,n_jobs=-1,random_state=20260823,class_weight='balanced'))
 ma,mw,mr=mdl(),mdl(),mdl();ma.fit(X,ya);mw.fit(X,yw);mr.fit(X[mask],yr);return {'act':ma,'wait':mw,'replace':mr}
def pred(models,X):
 pa=models['act'].predict_proba(X)[:,1];pw=models['wait'].predict_proba(X)[:,1];pr=models['replace'].predict_proba(X)[:,1];out=[]
 for a,w,r in zip(pa,pw,pr):
  if a>=.5:out.append('REPLACE_ROUTE' if r>=.5 else 'KEEP_EXECUTING')
  else:out.append('WAIT_FOR_CLARITY' if w>=.5 else 'RETURN_TO_CONTROLLER')
 return out
def metric(y,p):return {'accuracy':accuracy_score(y,p),'macroF1':f1_score(y,p,labels=ACTIONS,average='macro',zero_division=0),'confusion':confusion_matrix(y,p,labels=ACTIONS).tolist(),'support':{a:sum(z==a for z in y) for a in ACTIONS},'pred':{a:sum(z==a for z in p) for a in ACTIONS}}
def main():
 rows,mids,sp=load();data={};X={};Y={}
 for s in sp:data[s],X[s],Y[s]=xy(rows,sp[s])
 cand=[]
 for kind in ('hist','rf','extra'):
  m=train_heads(kind,X['train'],Y['train']);mv=metric(Y['validation'],pred(m,X['validation']));cand.append((mv['macroF1'],mv['accuracy'],kind,m,mv))
 cand.sort(key=lambda z:(z[0],z[1]),reverse=True);_,_,kind,m,mv=cand[0];mh=metric(Y['holdout'],pred(m,X['holdout']))
 rep={'version':'R2_DYNAMIC_LIFECYCLE_SUPERVISOR_AUTOML_V1','researchOnly':True,'runtimeFeatureCompatibleWithV10':True,'rows':len(rows),'markets':len(mids),'features':FEATURES,'split':{k:sorted(v) for k,v in sp.items()},'selection':'validation macroF1 then accuracy; holdout opened once','candidates':[{'kind':z[2],'validation':z[4]} for z in cand],'winner':kind,'validation':mv,'holdout':mh,'decision':'FREEZE_FOR_CLOSED_LOOP_SCREEN' if mh['macroF1']>=.30 and mh['accuracy']>=.40 else 'REJECT'}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf8');joblib.dump({'currentOnly':{'features':FEATURES,'models':m},'thresholds':{'act':.5,'wait':.5,'replace':.5},'version':'R2_DYNAMIC_LIFECYCLE_SUPERVISOR_AUTOML_V1'},MODEL);print(json.dumps({'winner':kind,'validation':mv,'holdout':mh,'decision':rep['decision']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
