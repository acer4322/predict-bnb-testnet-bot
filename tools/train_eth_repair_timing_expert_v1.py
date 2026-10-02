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
 p=Path(__file__).resolve().with_name('train_eth_persistent_repair_specialist_v3_parent_child_graph.py');s=importlib.util.spec_from_file_location('v3timingfit',p);v3=importlib.util.module_from_spec(s);s.loader.exec_module(v3)
SEED=20260901

def delay_sec(r):
 z=r.get('time_to_next_repair');return None if z is None else float(np.expm1(float(z)*math.log1p(120000.0))/1000.)

def metric(y,p,th=.5):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=th).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'predictedPositiveRate':float(pred.mean()),'auc':float(roc_auc_score(y,p)),'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'threshold':float(th)}

def best_threshold(y,p):
 vals=np.unique(np.quantile(np.asarray(p,float),np.linspace(.02,.98,97)))
 best=(None,-1.)
 for th in vals:
  s=balanced_accuracy_score(y,(np.asarray(p)>=th).astype(int))
  if s>best[1]:best=(float(th),float(s))
 return best

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 rows,cut,nwin,births=v3.build(a.db)
 z=[]
 for r in rows:
  d=delay_sec(r)
  if d is not None:r['delay_gt10']=int(d>10.);z.append(r)
 pre=sorted(set(int(r['end']) for r in z if int(r['end'])<cut));calcut=pre[int(len(pre)*.80)]
 fit=[r for r in z if int(r['end'])<calcut];cal=[r for r in z if calcut<=int(r['end'])<cut];te=[r for r in z if int(r['end'])>=cut]
 X=lambda rr:np.stack([r['cur'] for r in rr]);Y=lambda rr:np.asarray([r['delay_gt10'] for r in rr],int)
 m=HistGradientBoostingClassifier(max_iter=160,learning_rate=.055,max_leaf_nodes=15,l2_regularization=.5,random_state=SEED,class_weight='balanced')
 m.fit(X(fit),Y(fit));pc=m.predict_proba(X(cal))[:,1];th,calba=best_threshold(Y(cal),pc);pt=m.predict_proba(X(te))[:,1]
 out={'version':'ETH_REPAIR_TIMING_EXPERT_V1','researchOnly':True,'sourceDb':os.path.abspath(a.db),'windows':nwin,'parentBirths':births,'chronologyCutoff':cut,'calibrationCutoff':calcut,'fitRows':len(fit),'calRows':len(cal),'testRows':len(te),'features':v3.CUR_FEATURES,'label':'next Repair fill delay >10s','thresholdSelectedOnCalibration':th,'fit':metric(Y(fit),m.predict_proba(X(fit))[:,1],th),'calibration':metric(Y(cal),pc,th),'laterChronology':metric(Y(te),pt,th),'promotionPass':bool(roc_auc_score(Y(te),pt)>=.62 and balanced_accuracy_score(Y(te),(pt>=th).astype(int))>=.58),'boundary':['future repair timing label supervision only','strict-past current geometry only','no winner/future PnL features','no HFT/PnL threshold tuning','V9 remains frozen baseline']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'model':m,'threshold':th,'features':v3.CUR_FEATURES},mp);print(json.dumps({'ok':True,'promotionPass':out['promotionPass'],'threshold':th,'calibration':out['calibration'],'later':out['laterChronology'],'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
