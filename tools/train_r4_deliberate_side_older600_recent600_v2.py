from __future__ import annotations
import json,importlib.util,sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,average_precision_score

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'train_r4_deliberate_expansion_side_preaction_lastfill_v1.py'
spec=importlib.util.spec_from_file_location('r4_side_base',P);b=importlib.util.module_from_spec(spec);assert spec and spec.loader
sys.modules[spec.name]=b;spec.loader.exec_module(b)
TZ=ZoneInfo('Asia/Taipei');VERSION='R4_DELIBERATE_SIDE_OLDER600_RECENT600_V2'

def score(model,X,y):
 p=model.predict_proba(X)[:,1]
 return {'n':len(y),'positiveRateUP':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'balancedAccuracyAt05':float(balanced_accuracy_score(y,p>=.5))}

def main():
 meta=b.load_meta()[-1200:]
 train_meta=meta[:600];test_meta=meta[600:]
 ids=[m for m,_ in meta];pub=b.load_public(ids)
 import sqlite3
 c=sqlite3.connect(b.TDB);data=[]
 for mid,_ in meta:data.extend(b.build_market(c,pub,mid))
 c.close()
 tr_ids=set(m for m,_ in train_meta);te_ids=set(m for m,_ in test_meta)
 tr=[x for x in data if x[0] in tr_ids];te=[x for x in data if x[0] in te_ids]
 Xtr=np.asarray([x[1] for x in tr],float);ytr=np.asarray([x[2] for x in tr],int);Xte=np.asarray([x[1] for x in te],float);yte=np.asarray([x[2] for x in te],int)
 model=HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=30,l2_regularization=2.,random_state=20260826).fit(Xtr,ytr)
 rep={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'teacher':b.VERSION,'split':{'trainMarkets':len(train_meta),'testMarkets':len(test_meta),'trainStart':train_meta[0][1],'trainEnd':train_meta[-1][1],'testStart':test_meta[0][1],'testEnd':test_meta[-1][1]},'features':b.FEATURES,'train':score(model,Xtr,ytr),'test':score(model,Xte,yte),'gate':{'testAucRequired':.80},'guards':{'winnerExcluded':True,'strictPastPublicMaxAgeMs':3000,'sealed20260816':True,'noEchtgeldTraining':True,'noThresholdSweep':True}}
 rep['gate']['pass']=bool(rep['test']['auc']>=.80)
 joblib.dump({'version':VERSION,'model':model,'features':b.FEATURES,'trainMarketIds':sorted(tr_ids),'testMarketIds':sorted(te_ids),'researchOnly':True},ROOT/'data'/'research'/'r4_v0'/'r4_deliberate_side_older600_recent600_v2.joblib')
 out=ROOT/'data'/'research'/'r4_v0'/'hourly'/f"r4_deliberate_side_older600_recent600_v2_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(rep,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'split':rep['split'],'train':rep['train'],'test':rep['test'],'gate':rep['gate']},ensure_ascii=False))
if __name__=='__main__':main()
