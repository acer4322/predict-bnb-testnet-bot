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
 p=Path(__file__).resolve().with_name('train_eth_persistent_repair_specialist_v3_parent_child_graph.py');s=importlib.util.spec_from_file_location('v3ord',p);v3=importlib.util.module_from_spec(s);s.loader.exec_module(v3)
SEED=20260901
FEATURES=list(v3.CUR_FEATURES)

def delay_sec(r):
 z=r.get('time_to_next_repair')
 if z is None:return None
 return float(np.expm1(float(z)*math.log1p(120000.0))/1000.0)

def eligible(rows):
 ai=v3.CUR_FEATURES.index('absnet_ratio');return [r for r in rows if float(r['cur'][ai])>0.01 and r.get('time_to_next_repair') is not None]

def sub(rows,n):
 if not n or len(rows)<=n:return rows
 idx=np.linspace(0,len(rows)-1,int(n),dtype=int);return [rows[int(i)] for i in idx]

def X(rows):return np.stack([np.asarray(r['cur'],np.float32) for r in rows])
def y(rows,h):return np.asarray([int(float(delay_sec(r))>h) for r in rows],np.int8)

def choose_threshold(yv,p):
 best=(0.5,-1.0)
 for th in np.linspace(.10,.90,161):
  b=balanced_accuracy_score(yv,(p>=th).astype(int)) if len(np.unique(yv))>1 else .5
  if b>best[1]+1e-12:best=(float(th),float(b))
 return best

def eval_head(yv,p,th):
 return {'n':int(len(yv)),'positiveRate':float(np.mean(yv)),'auc':float(roc_auc_score(yv,p)) if len(np.unique(yv))>1 else None,'averagePrecision':float(average_precision_score(yv,p)) if yv.sum()>0 else None,'balancedAccuracyAtThreshold':float(balanced_accuracy_score(yv,(p>=th).astype(int))) if len(np.unique(yv))>1 else None,'threshold':float(th)}

def timing_buckets(p10,p30,th10,th30):
 gt10=p10>=th10;gt30=(p30>=th30)&gt10
 labels=np.where(gt30,30,np.where(gt10,10,0));u,c=np.unique(labels,return_counts=True)
 return labels,{('NOW' if int(k)==0 else f'WAIT{int(k)}'):int(v) for k,v in zip(u,c)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');ap.add_argument('--model-out');ap.add_argument('--max-train-anchors',type=int,default=0);ap.add_argument('--max-test-anchors',type=int,default=0);ap.add_argument('--max-iter',type=int,default=150);a=ap.parse_args()
 rows,cut,nwin,births=v3.build(a.db);tr=eligible(sorted([r for r in rows if int(r['end'])<cut],key=lambda r:(int(r['end']),int(r['t']),int(r['market']))));te=eligible(sorted([r for r in rows if int(r['end'])>=cut],key=lambda r:(int(r['end']),int(r['t']),int(r['market']))))
 split=max(1,min(len(tr)-1,int(len(tr)*.80)));fit=tr[:split];cal=tr[split:];fit=sub(fit,a.max_train_anchors);cal=sub(cal,max(1000,int((a.max_train_anchors or len(tr))*.25)) if a.max_train_anchors else 0);te=sub(te,a.max_test_anchors)
 Xfit=X(fit);Xcal=X(cal);Xte=X(te);models={};thresholds={};calm={};tem={};ptest={}
 for h in (10,30):
  yy=y(fit,h);m=HistGradientBoostingClassifier(max_iter=a.max_iter,learning_rate=.055,max_leaf_nodes=15,l2_regularization=.7,min_samples_leaf=35,random_state=SEED+h,class_weight='balanced');m.fit(Xfit,yy);pc=m.predict_proba(Xcal)[:,1];th,b=choose_threshold(y(cal,h),pc);pt=m.predict_proba(Xte)[:,1];models[h]=m;thresholds[h]=th;calm[h]=eval_head(y(cal,h),pc,th);tem[h]=eval_head(y(te,h),pt,th);ptest[h]=pt
 labels,buckets=timing_buckets(ptest[10],ptest[30],thresholds[10],thresholds[30]);nondeg=sum(v>0 for v in buckets.values())>=2
 smoke=bool((tem[10]['auc'] or 0)>=.60 and (tem[30]['auc'] or 0)>=.62 and nondeg)
 # Diagnostic true timing buckets; censored is >30.
 truth10=y(te,10);truth30=y(te,30);truth_labels=np.where(truth30==1,30,np.where(truth10==1,10,0));tu,tc=np.unique(truth_labels,return_counts=True);truth_buckets={('NOW_OR_LE10' if int(k)==0 else ('GT10_LE30' if int(k)==10 else 'GT30_OR_CENSORED')):int(v) for k,v in zip(tu,tc)}
 out={'version':'ETH_REPAIR_ORDINAL_TIMING_V2_CONDITIONAL','researchOnly':True,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'parentBirths':births,'features':FEATURES,'fitAnchors':len(fit),'calibrationAnchors':len(cal),'testAnchors':len(te),'calibration':{'delay_gt10':calm[10],'delay_gt30':calm[30]},'chronologyLater':{'delay_gt10':tem[10],'delay_gt30':tem[30]},'predictedTimingBuckets':buckets,'trueTimingBucketsDiagnostic':truth_buckets,'smokeVerified':smoke,'smokeRule':'delay_gt10 AUC>=.60; delay_gt30 AUC>=.62; >=2 timing buckets populated','runtimeSemantics':'Conditional on an already-active Repair obligation: WAIT30 iff gt10 and gt30 heads exceed frozen pre-cut thresholds; WAIT10 iff gt10 only; otherwise NOW. Evaluate only at material lifecycle anchor; deadline expiry forces NOW.','boundary':['Target future timing is supervision only; censored/no-next-Repair anchors are excluded from this timing expert and belong to the separate Repair-obligation layer','thresholds selected only on pre-cut calibration','chronology-later is evaluation only','no HFT-PnL tuning','no winner/future PnL runtime feature','geometry-only strict-past features','Repair ownership remains deterministic']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'models':{'delay_gt10':models[10],'delay_gt30':models[30]},'thresholds':{'delay_gt10':thresholds[10],'delay_gt30':thresholds[30]},'features':FEATURES,'chronologyCutoff':cut},mp)
 print(json.dumps({'ok':True,'smokeVerified':smoke,'fitAnchors':len(fit),'calibrationAnchors':len(cal),'testAnchors':len(te),'calibration':out['calibration'],'later':out['chronologyLater'],'predictedBuckets':buckets,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
