from __future__ import annotations
import json,sys
from pathlib import Path
import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,precision_score,recall_score,log_loss,confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
ART=OUT/'pair_completion_regime_experts_v5.joblib'
REPORT=OUT/'pair_completion_regime_experts_v5_report.json'
H=(5,10,20); FROZEN_LAST=1511912; FORWARD_FIRST=1520549

BASE_NEED=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty','absTrackingError','actualCombinedGross','secondsLeft','recoverySpreadTicks','pairAskSum','lastMakerFillAgeMs']
BASE_COST=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','absTrackingError','secondsLeft','recoveryAsk','recoverySpreadTicks','pairAskSum','directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery','spotQueueTowardRecovery','spotTaker1sTowardRecovery','futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery','futuresQueueTowardRecovery','futuresTaker1sTowardRecovery']
REGIME=['makerTargetRealizationRatio','recoveryMakerRealizationRatio','otherMakerRealizationRatio','makerExecutionFillRatio','makerFillEventCount','makerFillShares10s','makerFillShares20s','makerSubmitShares20s','makerRecentFillToSubmitRatio20s','recoveryMakerDeficit','otherMakerDeficit','makerChildrenSubmittedCount']
NEED_REG=BASE_NEED+REGIME
COST_REG=BASE_COST+REGIME+['marginalSurplusChunkShares','marginalSurplusChunkAvgCost','recoveryTakerFeePerShare','lockedPairEdgePerShare']

def consensus(vals):
 neg=any(float(x)<0 for x in vals); pos=any(float(x)>0 for x in vals)
 if neg and not pos:return 'REPLACE'
 if pos and not neg:return 'KEEP'
 if not neg and not pos:return 'NEUTRAL'
 return 'MIXED'

def pipe(): return Pipeline([('imputer',SimpleImputer(strategy='median',add_indicator=True)),('scaler',StandardScaler()),('logit',LogisticRegression(C=.5,max_iter=4000,solver='lbfgs',class_weight='balanced'))])
def arr(rows,features): return np.asarray([[(r.get('features') or {}).get(k) for k in features] for r in rows],float)
def labeled(rows,kind):
 out=[]
 for r in rows:
  lab=consensus([r[f'deltaTargetErrorArea{h}s'] for h in H]) if kind=='need' else consensus([r[f'deltaCompletionCost{h}s'] for h in H])
  if lab in {'REPLACE','KEEP'}: out.append((r,1 if lab=='REPLACE' else 0))
 return out
def met(model,rows,features,y):
 if not rows:return {'n':0}
 p=model.predict_proba(arr(rows,features))[:,1]; z=(p>=.5).astype(int); yy=np.asarray(y,int); cm=confusion_matrix(yy,z,labels=[0,1])
 return {'n':len(y),'positiveRate':float(yy.mean()),'predictedPositiveRate':float(z.mean()),'predictedMean':float(p.mean()),'auc':float(roc_auc_score(yy,p)) if len(set(yy.tolist()))>1 else None,'ap':float(average_precision_score(yy,p)) if len(set(yy.tolist()))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,z)) if len(set(yy.tolist()))>1 else None,'precision':float(precision_score(yy,z,zero_division=0)),'recall':float(recall_score(yy,z,zero_division=0)),'logLoss':float(log_loss(yy,p,labels=[0,1])),'confusionTnFpFnTp':[int(cm[0,0]),int(cm[0,1]),int(cm[1,0]),int(cm[1,1])]}
def fit(rows,features,kind):
 p=labeled(rows,kind); rr=[x[0] for x in p]; y=[x[1] for x in p]; m=pipe(); m.fit(arr(rr,features),np.asarray(y,int)); return m,rr,y
def ev(model,rows,features,kind):
 p=labeled(rows,kind); rr=[x[0] for x in p]; y=[x[1] for x in p]; return met(model,rr,features,y)
def gate(rows,nm,nf,cm,cf):
 y=[];z=[];sel=[]
 for r in rows:
  f=r.get('features') or {}; pn=float(nm.predict_proba(np.asarray([[f.get(k) for k in nf]],float))[:,1][0]); pc=float(cm.predict_proba(np.asarray([[f.get(k) for k in cf]],float))[:,1][0]); zz=int(pn>=.5 and pc>=.5); yy=int(r.get('paretoLabel')=='REPLACE_DOMINATES'); y.append(yy);z.append(zz)
  if zz:sel.append({'marketId':int(r['marketId']),'truth':r.get('paretoLabel'),'pNeed':pn,'pCost':pc})
 yy=np.asarray(y,int);zz=np.asarray(z,int)
 return {'n':len(rows),'trueReplaceDominates':int(yy.sum()),'selectedAutoReplace':int(zz.sum()),'precision':float(precision_score(yy,zz,zero_division=0)),'recall':float(recall_score(yy,zz,zero_division=0)),'balancedAccuracy':float(balanced_accuracy_score(yy,zz)) if len(set(yy.tolist()))>1 else None,'selected':sel}

def main():
 rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]; rows.sort(key=lambda r:int(r['checkpointMs']))
 frozen=[r for r in rows if int(r['marketId'])<=FROZEN_LAST]; fw=[r for r in rows if int(r['marketId'])>=FORWARD_FIRST]; tr=frozen[:100]; va=frozen[100:126]
 variants={}
 for name,nf,cf in [('BASE',BASE_NEED,BASE_COST),('REGIME',NEED_REG,COST_REG)]:
  nm,nrr,ny=fit(tr,nf,'need'); cm,crr,cy=fit(tr,cf,'cost')
  variants[name]={'needModel':nm,'costModel':cm,'needFeatures':nf,'costFeatures':cf,'report':{'need':{'train':met(nm,nrr,nf,ny),'validation':ev(nm,va,nf,'need'),'forwardOos':ev(nm,fw,nf,'need')},'cost':{'train':met(cm,crr,cf,cy),'validation':ev(cm,va,cf,'cost'),'forwardOos':ev(cm,fw,cf,'cost')},'gate':{'validation':gate(va,nm,nf,cm,cf),'forwardOos':gate(fw,nm,nf,cm,cf)}}}
 rep={'version':'PAIR_COMPLETION_REGIME_EXPERTS_V5','researchOnly':True,'liveTradingChanges':False,'dreamFillAllowed':False,'split':{'trainFrozen':100,'validationFrozen':26,'forwardOos':23,'trainMaxCheckpointMs':max(int(r['checkpointMs']) for r in tr),'validationMinCheckpointMs':min(int(r['checkpointMs']) for r in va),'forwardMinCheckpointMs':min(int(r['checkpointMs']) for r in fw)},'regimeFeatures':REGIME,'base':variants['BASE']['report'],'regime':variants['REGIME']['report'],'guardrails':['Same canonical Pareto labels as V3; only runtime features changed.','No threshold sweep; 0.5 natural boundary only.','Regime features are strict-past own execution/target state only.','No winner, settlement PnL, Target runtime data, or future fill features.']}
 bundle={'version':rep['version'],'researchOnly':True,'threshold':.5,'regimeFeatures':REGIME,'base':{k:v for k,v in variants['BASE'].items() if k!='report'},'regime':{k:v for k,v in variants['REGIME'].items() if k!='report'}}
 joblib.dump(bundle,ART); REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'base':rep['base'],'regime':rep['regime']},ensure_ascii=False))
if __name__=='__main__':main()
