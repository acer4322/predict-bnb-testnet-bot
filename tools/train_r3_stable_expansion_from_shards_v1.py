from __future__ import annotations
import pickle,json,math
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,balanced_accuracy_score,mean_absolute_error
ROOT=Path(__file__).resolve().parents[1]; D=ROOT/'data/research/r3_v0'
import sys
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.train_r3_stable_expansion_teacher_v1 as base
parts=[]
for s in range(8): parts += pickle.loads((D/f'r3_stable_expansion_rows_shard{s:02d}_of12.pkl').read_bytes())
for par in range(8,12):
 for pt in (0,1): parts += pickle.loads((D/f'r3_stable_expansion_rows_parent{par:02d}_part{pt}.pkl').read_bytes())
uniq=sorted(set(d['marketId'] for d in parts)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); groups=[set(uniq[:a]),set(uniq[a:b]),set(uniq[b:])]
mats=[]
for g in groups:
 dd=[d for d in parts if d['marketId'] in g]; X=np.asarray([d['x'] for d in dd],float); y=np.asarray([d['stableExpand'] for d in dd],int); u=np.asarray([d['utility5s'] for d in dd],float); mats.append((X,y,u))
Xtr,ytr,utr=mats[0]
clf=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=70,l2_regularization=3.,class_weight='balanced',random_state=20260825).fit(Xtr,ytr)
util=HistGradientBoostingRegressor(loss='absolute_error',max_iter=220,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=70,l2_regularization=3.,random_state=20260825).fit(Xtr,utr)
rep={'version':'R3_STABLE_EXPANSION_TEACHER_V1_FULL','researchOnly':True,'objective':'stableExpand iff future5s upside_gain > floor_spend; utility=upside_gain-floor_spend, same USDT units','markets':len(uniq),'rows':len(parts),'features':base.FEATURES,'winnerFeature':False,'fixed18Forbidden':True,'splits':{}}
for name,(X,y,u) in zip(['train','validation','test'],mats):
 p=clf.predict_proba(X)[:,1]; pu=util.predict(X); rep['splits'][name]={'n':len(y),'positiveRate':float(y.mean()),'predictedRate':float(np.mean(p>=.5)),'auc':float(roc_auc_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p>=.5)),'utility':{'actualMedian':float(np.median(u)),'predMedian':float(np.median(pu)),'mae':float(mean_absolute_error(u,pu)),'positiveActualRate':float(np.mean(u>0)),'positivePredRate':float(np.mean(pu>0))}}
joblib.dump({'model':clf,'utilityModel':util,'features':base.FEATURES,'version':rep['version']},D/'r3_stable_expansion_teacher_v1_full.joblib');(D/'r3_stable_expansion_teacher_v1_full_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
