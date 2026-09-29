from __future__ import annotations
import argparse,json,joblib
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
import train_target_eth_post_settlement_transition_v2_structured as v2

SEED=20260904
TASKS=('next_role_repair','next_composite')
FOLDS=[(0.50,0.60),(0.60,0.70),(0.70,0.80),(0.80,1.00)]

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}
def sw(y):
 y=np.asarray(y,int);p=max(float(y.mean()),1e-6);return np.where(y==1,.5/p,.5/max(1-p,1e-6))
def fit(X,y):
 m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=40,l2_regularization=1.0,random_state=SEED,early_stopping=False);m.fit(X,y,sample_weight=sw(y));return m

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows,_,_,_,an=v2.build(a.db)
 ends=sorted({int(r['end']) for r in rows if r.get('end') is not None});outrows=[]
 for fi,(train_frac,test_frac) in enumerate(FOLDS,1):
  train_end=ends[min(len(ends)-1,max(0,int(len(ends)*train_frac)-1))];test_end=ends[-1] if test_frac>=1 else ends[min(len(ends)-1,max(0,int(len(ends)*test_frac)-1))]
  tr=[r for r in rows if int(r['end'])<=train_end];te=[r for r in rows if int(r['end'])>train_end and int(r['end'])<=test_end]
  rec={'fold':fi,'trainFrac':train_frac,'testEndFrac':test_frac,'trainEndMs':train_end,'testEndMs':test_end,'trainN':len(tr),'testN':len(te),'metrics':{}}
  for task in TASKS:
   X=np.stack([r['x_base'] for r in tr]);y=np.asarray([r[task] for r in tr],int);Xt=np.stack([r['x_base'] for r in te]);yt=np.asarray([r[task] for r in te],int);m=fit(X,y);rec['metrics'][task]=metric(yt,m.predict_proba(Xt)[:,1])
  outrows.append(rec);print(json.dumps(rec),flush=True)
 summary={}
 for task in TASKS:
  augs=[float(x['metrics'][task]['auc']) for x in outrows];summary[task]={'foldAucs':augs,'minAuc':min(augs),'medianAuc':float(np.median(augs)),'maxAuc':max(augs)}
 gates={'roleAllAbove060':summary['next_role_repair']['minAuc']>=.60,'roleMedianAbove062':summary['next_role_repair']['medianAuc']>=.62,'compositeAllAbove062':summary['next_composite']['minAuc']>=.62,'compositeMedianAbove064':summary['next_composite']['medianAuc']>=.64}
 out={'version':'TARGET_ETH_POST_SETTLEMENT_HGB_ROLLING_V1','date':'2026-09-04','researchOnly':True,'fixedModel':'V2 current-state HistGradientBoosting, unchanged hyperparameters/features','datasetAnatomy':an,'folds':outrows,'summary':summary,'gates':gates,'decision':'KEEP_CURRENT_STATE_HGB_AS_ROBUST_SHADOW' if all(gates.values()) else 'DO_NOT_PROMOTE_HGB_SHADOW_YET','boundary':['rolling chronology only','no feature/threshold/hyperparameter changes after V2','no winner/PnL feature','Target next action only offline label','shadow information only']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'summary':summary,'gates':gates},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
