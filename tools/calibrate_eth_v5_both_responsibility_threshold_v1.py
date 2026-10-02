from __future__ import annotations
import argparse,json,sys,importlib.util
from pathlib import Path
import numpy as np,torch
from sklearn.metrics import balanced_accuracy_score,roc_auc_score,average_precision_score
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import train_eth_persistent_repair_specialist_v5_factorized_capability_router as v5
except ImportError:v5=sib('v5_cal','train_eth_persistent_repair_specialist_v5_factorized_capability_router.py')
v3=v5.v3
TASK='both_responsibilities_30s'

def predict(model,rows,dev):
 z,X,G,A,AM,R,RM,E,EM,y=v5.arr(rows,TASK);out=[];model.eval()
 with torch.no_grad():
  for i in range(0,len(y),4096):
   q=model(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(G[i:i+4096]).to(dev),torch.from_numpy(A[i:i+4096]).to(dev),torch.from_numpy(R[i:i+4096]).to(dev),torch.from_numpy(E[i:i+4096]).to(dev));out.append(torch.sigmoid(q).cpu().numpy())
 return np.asarray(y,int),np.concatenate(out)

def metric(y,p,th):
 pr=(p>=th).astype(int);return {'n':int(len(y)),'positiveRate':float(y.mean()),'predictedPositiveRate':float(pr.mean()),'balancedAccuracy':float(balanced_accuracy_score(y,pr)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if y.sum()>0 else None}

def best_threshold(y,p):
 vals=np.unique(np.quantile(p,np.linspace(0.02,.98,193)));best=None
 for th in vals:
  b=float(balanced_accuracy_score(y,(p>=th).astype(int)))
  if best is None or b>best[0]+1e-12:best=(b,float(th))
 return best[1],best[0]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output');a=ap.parse_args()
 rows,cut,nwin,births=v3.build(a.db);rows=v5.relabel(rows);pre=[r for r in rows if r['end']<cut];test=[r for r in rows if r['end']>=cut]
 # Keep model normalization exactly as training: standardize on all pre-cut training rows.
 stats=v3.standardize(pre,test)
 ends=sorted(set(int(r['end']) for r in pre));split=ends[int(len(ends)*.80)]
 fit=[r for r in pre if int(r['end'])<split];cal=[r for r in pre if int(r['end'])>=split]
 ck=torch.load(a.model,map_location='cpu',weights_only=False);dev=torch.device('cuda' if torch.cuda.is_available() else 'cpu');m=v5.CapabilityExpert(v5.MODES[TASK]);m.load_state_dict(ck['models'][TASK]);m.to(dev).eval()
 yfit,pfit=predict(m,fit,dev);ycal,pcal=predict(m,cal,dev);ytest,ptest=predict(m,test,dev)
 th,bcal=best_threshold(ycal,pcal)
 # Secondary rate-matched threshold for diagnosis only: choose calibration quantile matching label positive rate.
 rate=float(ycal.mean());rate_th=float(np.quantile(pcal,1-rate))
 out={'version':'ETH_V5_BOTH_RESPONSIBILITY_THRESHOLD_CALIBRATION_V1','researchOnly':True,'device':str(dev),'chronologyCutoff':int(cut),'calibrationSplitEnd':int(split),'windows':nwin,'graphParentBirths':births,'thresholdSelectedOnCalibrationBalancedAccuracy':float(th),'calibrationBestBalancedAccuracy':float(bcal),'rateMatchedThresholdDiagnostic':rate_th,'fitAt05':metric(yfit,pfit,.5),'calAt05':metric(ycal,pcal,.5),'testAt05':metric(ytest,ptest,.5),'calAtSelected':metric(ycal,pcal,th),'testAtSelected':metric(ytest,ptest,th),'calAt04':metric(ycal,pcal,.4),'testAt04':metric(ytest,ptest,.4),'scoreDistribution':{'cal':{'min':float(pcal.min()),'q10':float(np.quantile(pcal,.1)),'q25':float(np.quantile(pcal,.25)),'median':float(np.median(pcal)),'q75':float(np.quantile(pcal,.75)),'q90':float(np.quantile(pcal,.9)),'max':float(pcal.max())},'test':{'min':float(ptest.min()),'q10':float(np.quantile(ptest,.1)),'q25':float(np.quantile(ptest,.25)),'median':float(np.median(ptest)),'q75':float(np.quantile(ptest,.75)),'q90':float(np.quantile(ptest,.9)),'max':float(ptest.max())}},'boundary':['frozen V5 Both expert; no retraining','normalization reconstructed from original pre-cut training chronology only','threshold selected only on last 20% of pre-cut chronology','later chronology used for development validation only; no new-market claim','winner/PnL absent']}
 op=Path(a.output) if a.output else Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'threshold':th,'cal':out['calAtSelected'],'test':out['testAtSelected'],'at05':out['testAt05'],'at04':out['testAt04']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
