from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score

def met(y,p,th=.5):
 y=np.asarray(y,int);p=np.asarray(p,float);z=(p>=th).astype(int);both=len(np.unique(y))==2
 return {'n':int(len(y)),'positiveRate':float(np.mean(y)) if len(y) else None,'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'threshold':float(th),'predPositiveRate':float(np.mean(z)) if len(z) else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if both else None}
def best_th(y,p):
 cand=np.unique(np.quantile(p,np.linspace(.05,.95,37)));best=(.5,-1.)
 for th in cand:
  b=balanced_accuracy_score(y,(p>=th).astype(int))
  if b>best[1]:best=(float(th),float(b))
 return best[0]
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta',required=True);ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 z=np.load(a.dataset);X=z['X'];y=z['y_value'];vm=z['value_mask'].astype(bool);mid=z['market_id'];end=z['end_ms'];meta=json.loads(Path(a.meta).read_text(encoding='utf-8'));F=meta['features'];ix={k:i for i,k in enumerate(F)}
 markets=sorted(set(map(int,mid.tolist())),key=lambda m:int(np.min(end[mid==m])));n=len(markets);a1=int(.70*n);a2=int(.85*n);sets={'train':set(markets[:a1]),'calibration':set(markets[a1:a2]),'test':set(markets[a2:])};ms={k:np.isin(mid,list(v)) for k,v in sets.items()}
 bal_names=[x for x in ['seconds_left','pair_coverage','absnet_ratio','gross_log','candidate_relation','candidate_qty_log'] if x in ix];bal=[ix[x] for x in bal_names];econ=list(range(len(F)));res={};mods={}
 it=np.where(ms['train']&vm)[0];ic=np.where(ms['calibration']&vm)[0];ie=np.where(ms['test']&vm)[0]
 for name,cols in [('balanceOnly',bal),('economicFull',econ)]:
  m=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=23,min_samples_leaf=50,l2_regularization=3.,class_weight='balanced',random_state=20260901).fit(X[it][:,cols],y[it].astype(int));pc=m.predict_proba(X[ic][:,cols])[:,1];th=best_th(y[ic].astype(int),pc);pt=m.predict_proba(X[ie][:,cols])[:,1];res[name]={'calibration':met(y[ic].astype(int),pc,th),'test':met(y[ie].astype(int),pt,th)};mods[name]={'model':m,'cols':cols,'threshold':th}
 lift=float(res['economicFull']['test']['auc']-res['balanceOnly']['test']['auc']);checks={'testAucGe080':res['economicFull']['test']['auc']>=.80,'economicLiftGe003':lift>=.03,'thresholdNondegenerate':.02<res['economicFull']['test']['threshold']<.98,'frontierRespected':int(meta.get('cutoff',0))<=1823545};out={'version':'ETH_TARGET_POST_SAFE_SURPLUS_VALUE_V2','researchOnly':True,'rows':int(len(X)),'markets':n,'marketSplit':{k:len(v) for k,v in sets.items()},'task':{'balanceOnly':res['balanceOnly'],'economicFull':res['economicFull'],'aucLiftEconomic':lift},'passChecks':checks,'representationPass':all(checks.values()),'featureSets':{'balanceOnly':bal_names,'economicFull':F},'runtimeIntent':'After Repair parent is complete and floor>=0, score each UP/DOWN Maker candidate independently; direction emerges from candidate economics + placement, not a side classifier.','boundary':['marketId<=1823545','Target actual Maker fills only','winner/PnL absent','no runtime authority yet']}
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');joblib.dump({'version':out['version'],'features':F,'models':mods,'representationPass':out['representationPass'],'runtimeIntent':out['runtimeIntent']},mp);print(json.dumps({'ok':True,'representationPass':out['representationPass'],'task':out['task'],'checks':checks},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
