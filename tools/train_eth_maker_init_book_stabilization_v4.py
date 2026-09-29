from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,accuracy_score

def metrics(y,p,th):
 z=(p>=th).astype(int);return {'n':int(len(y)),'positiveRate':float(y.mean()),'predPositiveRate':float(z.mean()),'accuracy':float(accuracy_score(y,z)),'balancedAccuracy':float(balanced_accuracy_score(y,z)),'rocAuc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p))}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();z=np.load(a.dataset);meta=json.load(open(a.meta,encoding='utf-8'));X=z['X'];mid=z['market_id'];ts=z['timestamp_ms'];markets=sorted(set(map(int,mid)),key=lambda m:int(ts[mid==m].min()));n=len(markets);i1=int(.7*n);i2=int(.85*n);sets={'train':set(markets[:i1]),'validation':set(markets[i1:i2]),'test':set(markets[i2:])};out={'version':'ETH_MAKER_INIT_BOOK_STABILIZATION_V4','features':meta['features'],'markets':{k:len(v) for k,v in sets.items()}|{'total':n},'tasks':{}};models={}
 for key in ['500','2000','5000']:
  y=z['y'+key].astype(int);ix={k:np.where(np.isin(mid,list(v)))[0] for k,v in sets.items()};tr=ix['train'];va=ix['validation'];m=HistGradientBoostingClassifier(max_iter=320,learning_rate=.04,max_leaf_nodes=23,min_samples_leaf=30,l2_regularization=3.,class_weight='balanced',random_state=20260831+int(key)).fit(X[tr],y[tr]);pv=m.predict_proba(X[va])[:,1];rate=float(y[va].mean());th=float(np.quantile(pv,1-max(.001,min(.999,rate))));task={'threshold':th,'metrics':{}}
  for name,j in ix.items():task['metrics'][name]=metrics(y[j],m.predict_proba(X[j])[:,1],th)
  out['tasks'][key+'ms']=task;models[key]=m
 p=Path(a.out);p.parent.mkdir(parents=True,exist_ok=True);joblib.dump({'version':out['version'],'features':meta['features'],'models':models,'thresholds':{k:v['threshold'] for k,v in out['tasks'].items()}},p.with_suffix('.joblib'));p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
