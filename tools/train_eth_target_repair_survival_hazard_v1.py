from __future__ import annotations
import argparse,json,math,os,sys,importlib.util,joblib
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
try:
 from tools import train_eth_persistent_repair_specialist_v3_parent_child_graph as v3
except ImportError:
 p=Path(__file__).resolve().with_name('train_eth_persistent_repair_specialist_v3_parent_child_graph.py')
 s=importlib.util.spec_from_file_location('v3haz',p);v3=importlib.util.module_from_spec(s);s.loader.exec_module(v3)

SEED=20260901
GRID=np.asarray([0.,2.,5.,10.,20.,30.,45.,60.,90.,120.],np.float32)
THRESH=0.5
FEATURES=list(v3.CUR_FEATURES)+list(v3.GRAPH_FEATURES)+['elapsed_log120']

def delay_sec(r):
 z=r.get('time_to_next_repair')
 if z is None:return None
 return float(np.expm1(float(z)*math.log1p(120000.0))/1000.0)

def eligible(rows):
 # Only states with a material inventory gap. Repair timing is undefined for flat/no-responsibility geometry.
 ai=v3.CUR_FEATURES.index('absnet_ratio')
 return [r for r in rows if float(r['cur'][ai])>0.01]

def subsample(rows,n):
 if not n or len(rows)<=n:return rows
 idx=np.linspace(0,len(rows)-1,int(n),dtype=int)
 return [rows[int(i)] for i in idx]

def base_x(r):return np.concatenate([np.asarray(r['cur'],np.float32),np.asarray(r['graph'],np.float32)]).astype(np.float32)

def risk_rows(anchors):
 X=[];y=[];aid=[]
 den=math.log1p(120.)
 for i,r in enumerate(anchors):
  b=base_x(r);d=delay_sec(r)
  for e in GRID:
   X.append(np.concatenate([b,np.asarray([math.log1p(float(e))/den],np.float32)]))
   y.append(int(d is not None and d<=float(e)+1e-9));aid.append(i)
 return np.asarray(X,np.float32),np.asarray(y,np.int8),np.asarray(aid,np.int32)

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=THRESH).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracyAt05':float(balanced_accuracy_score(y,pred)) if len(np.unique(y))>1 else None}

def crossing_metrics(model,anchors):
 if not anchors:return {}
 den=math.log1p(120.);cross=[];truth=[];pcurves=[];viol=0
 for r in anchors:
  b=base_x(r);xx=np.stack([np.concatenate([b,np.asarray([math.log1p(float(e))/den],np.float32)]) for e in GRID])
  p=model.predict_proba(xx)[:,1];pcurves.append(p)
  viol+=int(np.sum(np.diff(p)<-1e-8))
  ix=np.where(p>=THRESH)[0];cross.append(float(GRID[int(ix[0])]) if len(ix) else 120.0);truth.append(delay_sec(r))
 cross=np.asarray(cross,float);known=np.asarray([d is not None for d in truth]);td=np.asarray([120. if d is None else min(120.,float(d)) for d in truth],float)
 buckets={'LE5':int(np.sum(cross<=5)),'GT5_LE10':int(np.sum((cross>5)&(cross<=10))),'GT10_LE30':int(np.sum((cross>10)&(cross<=30))),'GT30':int(np.sum(cross>30))}
 out={'anchors':len(anchors),'monotonicViolationCount':int(viol),'crossingBuckets':buckets,'crossingMeanSec':float(cross.mean()),'crossingMedianSec':float(np.median(cross)),'knownRepairWithin120':int(known.sum()),'censoredNoRepairWithin120':int((~known).sum())}
 if known.any():
  out['knownDelayMaeSec']=float(np.mean(np.abs(cross[known]-td[known])));out['knownDelayMedianTruthSec']=float(np.median(td[known]));out['knownDelayMedianPredSec']=float(np.median(cross[known]))
 for h in (10.,30.):
  yy=np.asarray([int(d is None or float(d)>h) for d in truth],int)
  out[f'delay_gt{int(h)}_rate']=float(yy.mean())
  out[f'delay_gt{int(h)}_aucFromCrossing']=float(roc_auc_score(yy,cross)) if len(np.unique(yy))>1 else None
 # Also report cumulative probability at fixed elapsed horizons.
 pc=np.asarray(pcurves,float)
 for e in (5.,10.,30.,60.):
  j=int(np.where(GRID==e)[0][0]);yy=np.asarray([int(d is not None and float(d)<=e) for d in truth],int)
  out[f'repairBy{int(e)}_auc']=float(roc_auc_score(yy,pc[:,j])) if len(np.unique(yy))>1 else None
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');ap.add_argument('--model-out');ap.add_argument('--max-train-anchors',type=int,default=0);ap.add_argument('--max-test-anchors',type=int,default=0);ap.add_argument('--max-iter',type=int,default=140);a=ap.parse_args()
 rows,cut,nwin,births=v3.build(a.db);tr=eligible([r for r in rows if int(r['end'])<cut]);te=eligible([r for r in rows if int(r['end'])>=cut]);tr=subsample(tr,a.max_train_anchors);te=subsample(te,a.max_test_anchors)
 Xtr,ytr,_=risk_rows(tr);Xte,yte,_=risk_rows(te)
 mono=[0]*(len(FEATURES)-1)+[1]
 model=HistGradientBoostingClassifier(max_iter=a.max_iter,learning_rate=.055,max_leaf_nodes=21,l2_regularization=.7,min_samples_leaf=40,random_state=SEED,monotonic_cst=mono)
 model.fit(Xtr,ytr);pte=model.predict_proba(Xte)[:,1]
 risk=met(yte,pte);cross=crossing_metrics(model,te)
 nondeg=sum(v>0 for v in cross.get('crossingBuckets',{}).values())>=2
 smoke=bool((risk.get('auc') or 0)>=.60 and cross.get('monotonicViolationCount',1)==0 and nondeg and (cross.get('delay_gt10_aucFromCrossing') or 0)>=.58)
 out={'version':'ETH_REPAIR_SURVIVAL_HAZARD_V1','researchOnly':True,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'parentBirths':births,'features':FEATURES,'elapsedGridSec':GRID.tolist(),'threshold':THRESH,'monotonicElapsed':True,'trainAnchors':len(tr),'testAnchors':len(te),'trainRiskRows':len(ytr),'testRiskRows':len(yte),'riskRowsChronologyLater':risk,'crossingChronologyLater':cross,'smokeVerified':smoke,'smokeRule':'risk AUC>=.60; monotonic violations=0; >=2 crossing buckets populated; delay_gt10 AUC from predicted crossing>=.58','boundary':['Target next-Repair timing is supervision/evaluation only','anchor current geometry + parent graph are strict-past','elapsed is the only feature allowed to evolve between material events','no winner/future PnL runtime feature','no HFT-PnL threshold tuning','threshold fixed at 0.5','Repair ownership remains deterministic']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'model':model,'features':FEATURES,'elapsedGridSec':GRID.tolist(),'threshold':THRESH,'chronologyCutoff':cut},mp)
 print(json.dumps({'ok':True,'smokeVerified':smoke,'trainAnchors':len(tr),'testAnchors':len(te),'risk':risk,'crossing':cross,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
