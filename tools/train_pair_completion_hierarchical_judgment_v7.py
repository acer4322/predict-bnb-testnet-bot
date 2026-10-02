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
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'; PAIR=D/'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'; OOF=D/'pair_completion_fill_expert_oof_v1.csv'; ART=D/'pair_completion_hierarchical_judgment_v7.joblib'; REPORT=D/'pair_completion_hierarchical_judgment_v7_report.json'
FROZEN_LAST=1511912; FORWARD_FIRST=1520549
BASE=['workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks','workingRecoveryRemainingQty','absTrackingError','actualCombinedGross','secondsLeft','recoveryAsk','recoverySpreadTicks','pairAskSum','lastMakerFillAgeMs','marginalSurplusChunkShares','marginalSurplusChunkAvgCost','recoveryTakerFeePerShare','lockedPairEdgePerShare']
FILL=['pFill1s','pFill3s','pFill5s','fillExpertAvailable','fillExpertHasChild']
def pipe():return Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('sc',StandardScaler()),('lr',LogisticRegression(C=.5,max_iter=4000,class_weight='balanced',solver='lbfgs'))])
def enrich(rows):
 o=pd.read_csv(OOF);by={int(x.marketId):x for _,x in o.iterrows()}
 for r in rows:
  q=by.get(int(r['marketId']));f=r.get('features') or {}
  if q is None:f.update({'pFill1s':None,'pFill3s':None,'pFill5s':None,'fillExpertAvailable':0.0,'fillExpertHasChild':float(f.get('workingRecoveryExists') or 0)})
  else:f.update({'pFill1s':float(q.pFill1s) if pd.notna(q.pFill1s) else None,'pFill3s':float(q.pFill3s) if pd.notna(q.pFill3s) else None,'pFill5s':float(q.pFill5s) if pd.notna(q.pFill5s) else None,'fillExpertAvailable':float(pd.notna(q.pFill5s)),'fillExpertHasChild':float(bool(q.hasChild))})
  r['features']=f
 return rows
def A(rs,fs):return np.asarray([[(r.get('features') or {}).get(k) for k in fs] for r in rs],float)
def bm(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=.5).astype(int);cm=confusion_matrix(y,z,labels=[0,1]);return {'n':len(y),'positiveRate':float(y.mean()),'predictedPositiveRate':float(z.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if len(set(y.tolist()))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if len(set(y.tolist()))>1 else None,'precision':float(precision_score(y,z,zero_division=0)),'recall':float(recall_score(y,z,zero_division=0)),'logLoss':float(log_loss(y,p,labels=[0,1])),'confusionTnFpFnTp':[int(cm[0,0]),int(cm[0,1]),int(cm[1,0]),int(cm[1,1])]}
def train_variant(name,tr,va,fw,fs):
 yd=np.asarray([int(r['paretoLabel'] in {'REPLACE_DOMINATES','KEEP_DOMINATES'}) for r in tr],int);dm=pipe();dm.fit(A(tr,fs),yd)
 dom=[r for r in tr if r['paretoLabel'] in {'REPLACE_DOMINATES','KEEP_DOMINATES'}];ya=np.asarray([int(r['paretoLabel']=='REPLACE_DOMINATES') for r in dom],int);am=pipe();am.fit(A(dom,fs),ya)
 def evalset(rs):
  pd=dm.predict_proba(A(rs,fs))[:,1];dec=(pd>=.5); truthd=np.asarray([int(r['paretoLabel'] in {'REPLACE_DOMINATES','KEEP_DOMINATES'}) for r in rs],int)
  pa=am.predict_proba(A(rs,fs))[:,1]; act=(pa>=.5).astype(int)
  auto_replace=(dec & (act==1)); auto_keep=(dec & (act==0)); truth_replace=np.asarray([int(r['paretoLabel']=='REPLACE_DOMINATES') for r in rs],int); truth_keep=np.asarray([int(r['paretoLabel']=='KEEP_DOMINATES') for r in rs],int)
  selected=[]
  for r,d,a,pd0,pa0 in zip(rs,dec,act,pd,pa):
   if d:selected.append({'marketId':int(r['marketId']),'truth':r['paretoLabel'],'autoAction':'REPLACE' if a else 'KEEP','pDecidable':float(pd0),'pReplaceGivenDecidable':float(pa0),'pFill5s':(r.get('features') or {}).get('pFill5s')})
  return {'decidable':bm(truthd,pd),'autoCoverage':float(dec.mean()),'autoCount':int(dec.sum()),'autoCorrectDominantAction':int(sum((r['paretoLabel']=='REPLACE_DOMINATES' and a==1) or (r['paretoLabel']=='KEEP_DOMINATES' and a==0) for r,d,a in zip(rs,dec,act) if d)),'autoWrongDominantAction':int(sum((r['paretoLabel']=='REPLACE_DOMINATES' and a==0) or (r['paretoLabel']=='KEEP_DOMINATES' and a==1) for r,d,a in zip(rs,dec,act) if d)),'autoOnTradeoffOrNeutral':int(sum(r['paretoLabel'] in {'TRADEOFF','NEUTRAL'} for r,d in zip(rs,dec) if d)),'autoReplacePrecision':float(precision_score(truth_replace,auto_replace.astype(int),zero_division=0)),'autoReplaceRecall':float(recall_score(truth_replace,auto_replace.astype(int),zero_division=0)),'autoKeepPrecision':float(precision_score(truth_keep,auto_keep.astype(int),zero_division=0)),'autoKeepRecall':float(recall_score(truth_keep,auto_keep.astype(int),zero_division=0)),'selected':selected}
 return {'name':name,'features':fs,'decidableModel':dm,'actionModel':am,'report':{'train':evalset(tr),'validation':evalset(va),'forwardOos':evalset(fw),'dominantTrainCounts':{'replace':sum(r['paretoLabel']=='REPLACE_DOMINATES' for r in dom),'keep':sum(r['paretoLabel']=='KEEP_DOMINATES' for r in dom)}}}
def main():
 rows=[json.loads(x) for x in PAIR.read_text(encoding='utf-8').splitlines() if x.strip()];rows.sort(key=lambda r:int(r['checkpointMs']));rows=enrich(rows);f=[r for r in rows if int(r['marketId'])<=FROZEN_LAST];tr=f[:100];va=f[100:126];fw=[r for r in rows if int(r['marketId'])>=FORWARD_FIRST]
 base=train_variant('BASE',tr,va,fw,BASE);fill=train_variant('FILL_COMPOSED',tr,va,fw,BASE+FILL)
 rep={'version':'PAIR_COMPLETION_HIERARCHICAL_JUDGMENT_V7','researchOnly':True,'liveTradingChanges':False,'dreamFillAllowed':False,'semantics':'Stage1 decides whether execution state is Pareto-decidable versus TRADEOFF/NEUTRAL. Stage2 chooses REPLACE vs KEEP only inside predicted decidable states.','split':{'trainFrozen':100,'validationFrozen':26,'forwardOos':23},'base':base['report'],'fillComposed':fill['report'],'guardrails':['No threshold sweep; both stages use natural 0.5.','Pareto labels unchanged.','Pair pFill is chronological OOF in training and pre-cutoff in validation/OOS.','TRADEOFF/NEUTRAL are explicit abstention targets, not forced action labels.','No winner, PnL, Target runtime data or future fill labels in runtime features.']}
 joblib.dump({'version':rep['version'],'threshold':.5,'base':{k:v for k,v in base.items() if k!='report'},'fillComposed':{k:v for k,v in fill.items() if k!='report'}},ART);REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'base':rep['base'],'fillComposed':rep['fillComposed']},ensure_ascii=False))
if __name__=='__main__':main()
