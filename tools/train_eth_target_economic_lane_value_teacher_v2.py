from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,precision_score,recall_score

TASKS={
 'durable_safe_surplus_30s':0,
 'expensive_repair_recovers_30s':2,
 'safe_expand_preserves_floor_30s':3,
}
SEED=20260901

def metrics(y,p,th):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=th).astype(int)
 return {
  'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,
  'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'threshold':float(th),
  'predPositiveRate':float(z.mean()),'balancedAccuracy':float(balanced_accuracy_score(y,z)),
  'precision':float(precision_score(y,z,zero_division=0)),'recall':float(recall_score(y,z,zero_division=0))
 }

def choose_threshold(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 qs=np.unique(np.quantile(p,np.linspace(.01,.99,199)))
 cands=np.unique(np.r_[0.02,qs,0.98])
 best=None
 for th in cands:
  z=(p>=th).astype(int);ba=float(balanced_accuracy_score(y,z))
  # deterministic tie-break: prefer threshold closer to 0.5, then larger threshold
  key=(ba,-abs(float(th)-.5),float(th))
  if best is None or key>best[0]:best=(key,float(th))
 return best[1]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--max-markets',type=int,default=0);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 d=np.load(a.dataset);X=d['X'];Y=d['Y'];mid=d['market_id'];ends=d['end_ms'];meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));F=meta['features']
 markets=sorted(set(map(int,mid.tolist())),key=lambda m:int(np.min(ends[mid==m])))
 if a.max_markets>0:
  keep=set(markets[-a.max_markets:]);k=np.isin(mid,list(keep));X=X[k];Y=Y[k];mid=mid[k];ends=ends[k];markets=[m for m in markets if m in keep]
 n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'calibration':set(markets[a1:a2]),'test':set(markets[a2:])}
 mi={k:np.isin(mid,list(v)) for k,v in sets.items()};out_tasks={};mods={};all_ok=True
 for name,j in TASKS.items():
  finite=np.isfinite(Y[:,j]);idx={k:np.where(mask&finite)[0] for k,mask in mi.items()};yt=Y[idx['train'],j].astype(int);yc=Y[idx['calibration'],j].astype(int);ye=Y[idx['test'],j].astype(int)
  if min(len(yt),len(yc),len(ye))<25 or min(len(np.unique(yt)),len(np.unique(yc)),len(np.unique(ye)))<2:
   out_tasks[name]={'status':'INSUFFICIENT_SUPPORT','counts':{k:int(len(v)) for k,v in idx.items()}};all_ok=False;continue
  model=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=35,l2_regularization=3.0,class_weight='balanced',random_state=SEED).fit(X[idx['train']],yt)
  pc=model.predict_proba(X[idx['calibration']])[:,1];th=choose_threshold(yc,pc);pe=model.predict_proba(X[idx['test']])[:,1]
  out_tasks[name]={'calibration':metrics(yc,pc,th),'test':metrics(ye,pe,th)};mods[name]={'model':model,'threshold':th}
 checks={
  'durableSafeSurplusAuc':bool(out_tasks.get('durable_safe_surplus_30s',{}).get('test',{}).get('auc') is not None and out_tasks['durable_safe_surplus_30s']['test']['auc'] >= (.80 if a.max_markets else .85)),
  'expensiveRepairRecoveryAuc':bool(out_tasks.get('expensive_repair_recovers_30s',{}).get('test',{}).get('auc') is not None and out_tasks['expensive_repair_recovers_30s']['test']['auc'] >= (.72 if a.max_markets else .78)),
  'safeExpandAuc':bool(out_tasks.get('safe_expand_preserves_floor_30s',{}).get('test',{}).get('auc') is not None and out_tasks['safe_expand_preserves_floor_30s']['test']['auc'] >= (.75 if a.max_markets else .85)),
  'thresholdsNondegenerate':bool(all(.02<float(v['threshold'])<.98 for v in mods.values())) if mods else False,
  'frontierRespected':bool(int(meta.get('cutoff',0))<=1823545)
 }
 passed=bool(all_ok and all(checks.values()))
 out={'version':'ETH_TARGET_ECONOMIC_LANE_VALUE_TEACHER_V2','researchOnly':True,'maxMarkets':a.max_markets,'rows':int(len(X)),'markets':n,'marketSplit':{k:len(v) for k,v in sets.items()},'features':F,'tasks':out_tasks,'passChecks':checks,'representationPass':passed,'runtimeSemantics':{'cheapRepair':'pair_edge>=0 deterministic ACCEPT; not model-gated','expensiveRepair':'use expensive_repair_recovers_30s teacher only when Repair pair_edge<0','safeExpand':'use safe_expand_preserves_floor_30s only when pre-action floor>=0','baseFormation':'durable_safe_surplus_30s belief/diagnostic'},'boundary':['threshold calibration uses chronology calibration only','no winner/PnL','marketId>1823545 excluded by source dataset','no runtime authority yet']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'features':F,'models':mods,'representationPass':passed,'runtimeSemantics':out['runtimeSemantics']},mp)
 print(json.dumps({'ok':True,'representationPass':passed,'markets':n,'rows':len(X),'tasks':out_tasks,'checks':checks,'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
