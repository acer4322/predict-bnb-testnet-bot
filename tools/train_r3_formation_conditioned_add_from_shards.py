from pathlib import Path
import pickle,json,joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/r3_v0'; rows=[]; features=None; nsh=4
for s in range(nsh):
 p=D/f'r3_formation_conditioned_add_rows_shard_{s}_of_{nsh}.pkl'; d=pickle.load(p.open('rb')); rows.extend(d['rows']); features=d['features']
X=np.asarray([r[1] for r in rows],float);y=np.asarray([r[2] for r in rows],int);ms=[r[0] for r in rows];uniq=sorted(set(ms));a=int(.6*len(uniq));b=int(.8*len(uniq));SS={'train':set(uniq[:a]),'validation':set(uniq[a:b]),'test':set(uniq[b:])};tr=np.asarray([i for i,m in enumerate(ms) if m in SS['train']],int)
m=HistGradientBoostingClassifier(max_iter=280,learning_rate=.045,max_leaf_nodes=27,min_samples_leaf=100,l2_regularization=3.,class_weight='balanced',random_state=20260825).fit(X[tr],y[tr]);rep={'version':'R3_FORMATION_CONDITIONED_ADD_HAZARD_V2_SHARDED','markets':len(uniq),'rows':len(rows),'features':features,'splits':{}}
for nm,S in SS.items():
 ix=np.asarray([i for i,mm in enumerate(ms) if mm in S],int);yy=y[ix];p=m.predict_proba(X[ix])[:,1];pred=(p>=.5).astype(int);rep['splits'][nm]={'n':int(len(ix)),'positiveRate':float(yy.mean()),'predictedRate':float(pred.mean()),'auc':float(roc_auc_score(yy,p)),'ap':float(average_precision_score(yy,p)),'balancedAccuracy':float(balanced_accuracy_score(yy,pred))}
joblib.dump({'features':features,'model':m,'version':rep['version']},D/'r3_formation_conditioned_add_hazard_v2_sharded.joblib');(D/'r3_formation_conditioned_add_hazard_v2_sharded_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
