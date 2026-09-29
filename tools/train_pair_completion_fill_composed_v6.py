from __future__ import annotations
import json,sys
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,precision_score,recall_score,log_loss,confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
PAIR=D/'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
OOF=D/'pair_completion_fill_expert_oof_v1.csv'
ART=D/'pair_completion_fill_composed_v6.joblib'
REPORT=D/'pair_completion_fill_composed_v6_report.json'
H=(5,10,20); FROZEN_LAST=1511912; FORWARD_FIRST=1520549
BASE_NEED=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty','absTrackingError','actualCombinedGross','secondsLeft','recoverySpreadTicks','pairAskSum','lastMakerFillAgeMs']
BASE_COST=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','absTrackingError','secondsLeft','recoveryAsk','recoverySpreadTicks','pairAskSum','directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery','futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery']
FILL=['pFill1s','pFill3s','pFill5s','fillExpertAvailable','fillExpertHasChild']

def consensus(vals):
 neg=any(float(x)<0 for x in vals);pos=any(float(x)>0 for x in vals)
 if neg and not pos:return 'REPLACE'
 if pos and not neg:return 'KEEP'
 if not neg and not pos:return 'NEUTRAL'
 return 'MIXED'
def pipe():return Pipeline([('imputer',SimpleImputer(strategy='median',add_indicator=True)),('scaler',StandardScaler()),('logit',LogisticRegression(C=.5,max_iter=4000,solver='lbfgs',class_weight='balanced'))])
def enrich(rows):
 o=pd.read_csv(OOF); by={int(r.marketId):r for _,r in o.iterrows()}
 for r in rows:
  q=by.get(int(r['marketId'])); f=r.get('features') or {}
  if q is None:
   f.update({'pFill1s':None,'pFill3s':None,'pFill5s':None,'fillExpertAvailable':0.0,'fillExpertHasChild':float(f.get('workingRecoveryExists') or 0.0)})
  else:
   avail=float(pd.notna(q.pFill5s)); f.update({'pFill1s':float(q.pFill1s) if pd.notna(q.pFill1s) else None,'pFill3s':float(q.pFill3s) if pd.notna(q.pFill3s) else None,'pFill5s':float(q.pFill5s) if pd.notna(q.pFill5s) else None,'fillExpertAvailable':avail,'fillExpertHasChild':float(bool(q.hasChild))})
  r['features']=f
 return rows
def arr(rows,features):return np.asarray([[(r.get('features') or {}).get(k) for k in features] for r in rows],float)
def labeled(rows,kind):
 out=[]
 for r in rows:
  lab=consensus([r[f'deltaTargetErrorArea{h}s'] for h in H]) if kind=='need' else consensus([r[f'deltaCompletionCost{h}s'] for h in H])
  if lab in {'REPLACE','KEEP'}:out.append((r,1 if lab=='REPLACE' else 0))
 return out
def met(model,rows,features,y):
 if not rows:return {'n':0}
 p=model.predict_proba(arr(rows,features))[:,1];z=(p>=.5).astype(int);yy=np.asarray(y,int);cm=confusion_matrix(yy,z,labels=[0,1])
 return {'n':len(y),'positiveRate':float(yy.mean()),'predictedPositiveRate':float(z.mean()),'predictedMean':float(p.mean()),'auc':float(roc_auc_score(yy,p)) if len(set(yy.tolist()))>1 else None,'ap':float(average_precision_score(yy,p)) if len(set(yy.tolist()))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,z)) if len(set(yy.tolist()))>1 else None,'precision':float(precision_score(yy,z,zero_division=0)),'recall':float(recall_score(yy,z,zero_division=0)),'logLoss':float(log_loss(yy,p,labels=[0,1])),'confusionTnFpFnTp':[int(cm[0,0]),int(cm[0,1]),int(cm[1,0]),int(cm[1,1])]}
def fit(rows,features,kind):
 q=labeled(rows,kind);rr=[x[0] for x in q];y=[x[1] for x in q];m=pipe();m.fit(arr(rr,features),np.asarray(y,int));return m,rr,y
def ev(m,rows,features,kind):
 q=labeled(rows,kind);rr=[x[0] for x in q];y=[x[1] for x in q];return met(m,rr,features,y)
def gate(rows,nm,nf,cm,cf):
 y=[];z=[];selected=[]
 for r in rows:
  f=r.get('features') or {};pn=float(nm.predict_proba(np.asarray([[f.get(k) for k in nf]],float))[:,1][0]);pc=float(cm.predict_proba(np.asarray([[f.get(k) for k in cf]],float))[:,1][0]);zz=int(pn>=.5 and pc>=.5);yy=int(r.get('paretoLabel')=='REPLACE_DOMINATES');y.append(yy);z.append(zz)
  if zz:selected.append({'marketId':int(r['marketId']),'truth':r.get('paretoLabel'),'pNeed':pn,'pCost':pc,'pFill1s':f.get('pFill1s'),'pFill3s':f.get('pFill3s'),'pFill5s':f.get('pFill5s'),'workingRecoveryExists':f.get('workingRecoveryExists')})
 yy=np.asarray(y,int);zz=np.asarray(z,int)
 return {'n':len(rows),'trueReplaceDominates':int(yy.sum()),'selectedAutoReplace':int(zz.sum()),'precision':float(precision_score(yy,zz,zero_division=0)),'recall':float(recall_score(yy,zz,zero_division=0)),'balancedAccuracy':float(balanced_accuracy_score(yy,zz)) if len(set(yy.tolist()))>1 else None,'selected':selected}
def variant(name,tr,va,fw,nf,cf):
 nm,nrr,ny=fit(tr,nf,'need');cm,crr,cy=fit(tr,cf,'cost')
 return {'name':name,'needFeatures':nf,'costFeatures':cf,'needModel':nm,'costModel':cm,'report':{'need':{'train':met(nm,nrr,nf,ny),'validation':ev(nm,va,nf,'need'),'forwardOos':ev(nm,fw,nf,'need')},'cost':{'train':met(cm,crr,cf,cy),'validation':ev(cm,va,cf,'cost'),'forwardOos':ev(cm,fw,cf,'cost')},'gate':{'validation':gate(va,nm,nf,cm,cf),'forwardOos':gate(fw,nm,nf,cm,cf)}}}
def main():
 rows=[json.loads(x) for x in PAIR.read_text(encoding='utf-8').splitlines() if x.strip()];rows.sort(key=lambda r:int(r['checkpointMs']));rows=enrich(rows)
 frozen=[r for r in rows if int(r['marketId'])<=FROZEN_LAST];fw=[r for r in rows if int(r['marketId'])>=FORWARD_FIRST];tr=frozen[:100];va=frozen[100:126]
 base=variant('BASE',tr,va,fw,BASE_NEED,BASE_COST)
 fill_need=variant('FILL_NEED_ONLY',tr,va,fw,BASE_NEED+FILL,BASE_COST)
 fill_both=variant('FILL_BOTH',tr,va,fw,BASE_NEED+FILL,BASE_COST+FILL)
 rep={'version':'PAIR_COMPLETION_FILL_COMPOSED_V6','researchOnly':True,'liveTradingChanges':False,'dreamFillAllowed':False,'split':{'trainFrozen':100,'validationFrozen':26,'forwardOos':23},'fillFeatures':FILL,'base':base['report'],'fillNeedOnly':fill_need['report'],'fillBoth':fill_both['report'],'guardrails':['Pair training pFill features are chronological expanding-window OOF only; earliest 25 Pair rows remain missing.','Frozen validation fill expert trains strictly before validation start.','Forward fill expert trains strictly before Forward start.','Pair Pareto teacher and 0.5 threshold unchanged.','No winner, settlement PnL, Target runtime state, or future fill label is used as a runtime Pair feature.']}
 bundle={'version':rep['version'],'threshold':.5,'researchOnly':True,'fillNeedOnly':{k:v for k,v in fill_need.items() if k!='report'},'fillBoth':{k:v for k,v in fill_both.items() if k!='report'}};joblib.dump(bundle,ART);REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'base':rep['base'],'fillNeedOnly':rep['fillNeedOnly'],'fillBoth':rep['fillBoth']},ensure_ascii=False))
if __name__=='__main__':main()
