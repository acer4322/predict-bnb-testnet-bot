from __future__ import annotations
import argparse,json,os
from pathlib import Path
import joblib,numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,mean_absolute_error,mean_squared_error

CLASS_TASKS=['durable_safe_surplus_30s','safe_surplus_60s','expensive_repair_recovers_30s','safe_expand_preserves_floor_30s']
REG_TASKS=['future_min_floor_delta_30s','future_max_best_delta_30s']
LABELS=CLASS_TASKS+REG_TASKS

def cm(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}
def rm(y,p):
 return {'n':int(len(y)),'mae':float(mean_absolute_error(y,p)),'rmse':float(mean_squared_error(y,p)**.5)}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--meta');ap.add_argument('--output');ap.add_argument('--model-out');a=ap.parse_args()
 d=np.load(a.dataset);X=d['X'];Y=d['Y'];ends=d['end_ms']; meta=json.loads(Path(a.meta or str(Path(a.dataset).with_suffix('.meta.json'))).read_text(encoding='utf-8'))
 features=meta['features'];bal=[features.index(x) for x in meta['balanceFeatures']];econ=list(range(len(features)))
 uniq=np.unique(ends);cut=uniq[int(len(uniq)*.70)];tr=ends<cut;te=~tr
 result={'version':'ETH_TARGET_FAVORABLE_PAIR_SAFE_SURPLUS_TEACHER_V1','researchOnly':True,'dataset':os.path.abspath(a.dataset),'chronologyCutoffEndMs':int(cut),'trainRows':int(tr.sum()),'testRows':int(te.sum()),'features':features,'balanceFeatures':meta['balanceFeatures'],'tasks':{},'models':{}}
 models={}
 for j,t in enumerate(CLASS_TASKS):
  finite=np.isfinite(Y[:,j]);itr=np.where(tr&finite)[0];ite=np.where(te&finite)[0];ytr=Y[itr,j].astype(int);yte=Y[ite,j].astype(int)
  task={}
  if len(itr)<100 or len(ite)<50 or len(np.unique(ytr))<2 or len(np.unique(yte))<2:
   result['tasks'][t]={'status':'INSUFFICIENT_SUPPORT','trainN':int(len(itr)),'testN':int(len(ite))};continue
  for name,idxs in [('balanceOnly',bal),('economicFull',econ)]:
   m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=35,l2_regularization=3.0,class_weight='balanced',random_state=20260901)
   m.fit(X[itr][:,idxs],ytr);p=m.predict_proba(X[ite][:,idxs])[:,1];task[name]=cm(yte,p);models[f'{t}:{name}']=(m,idxs)
  task['aucLiftEconomic']=None if task['balanceOnly']['auc'] is None or task['economicFull']['auc'] is None else float(task['economicFull']['auc']-task['balanceOnly']['auc']);result['tasks'][t]=task
 for k,t in enumerate(REG_TASKS,start=len(CLASS_TASKS)):
  finite=np.isfinite(Y[:,k]);itr=np.where(tr&finite)[0];ite=np.where(te&finite)[0];ytr=Y[itr,k];yte=Y[ite,k];task={}
  for name,idxs in [('balanceOnly',bal),('economicFull',econ)]:
   m=HistGradientBoostingRegressor(max_iter=180,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=35,l2_regularization=3.0,random_state=20260901)
   m.fit(X[itr][:,idxs],ytr);p=m.predict(X[ite][:,idxs]);task[name]=rm(yte,p);models[f'{t}:{name}']=(m,idxs)
  b=task['balanceOnly']['mae'];f=task['economicFull']['mae'];task['maeImprovementFrac']=float((b-f)/b) if b>0 else None;result['tasks'][t]=task
 dss=result['tasks'].get('durable_safe_surplus_30s',{});price_lifts=[result['tasks'].get(x,{}).get('aucLiftEconomic') for x in CLASS_TASKS];price_lifts=[x for x in price_lifts if x is not None];reg_imps=[result['tasks'][x].get('maeImprovementFrac') for x in REG_TASKS];
 result['passChecks']={
  'durableSafeSurplusAucGe065':bool(dss.get('economicFull',{}).get('auc') is not None and dss['economicFull']['auc']>=.65),
  'economicLiftPrimaryGe003OrConditionalGe004':bool((dss.get('aucLiftEconomic') or -9)>=.03 or any((result['tasks'].get(x,{}).get('aucLiftEconomic') or -9)>=.04 for x in ['expensive_repair_recovers_30s','safe_expand_preserves_floor_30s'])),
  'regressionMaeImproveGe008':bool(any((x or -9)>=.08 for x in reg_imps)),
  'frontierRespected':bool(int(meta.get('cutoff',0))<=1823545)
 }
 result['representationPass']=bool(all(result['passChecks'].values()));result['meanClassificationAucLift']=float(np.mean(price_lifts)) if price_lifts else None
 result['boundary']=['balance-only baseline is share geometry only','economicFull adds prices/cost/floor/pair reserve/debt/proposal economics','chronology-later test only','winner/PnL absent','no runtime authority yet']
 rd=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));op=Path(a.output) if a.output else rd/'result.json';mp=Path(a.model_out) if a.model_out else rd/'model.joblib';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(result,indent=2),encoding='utf-8');joblib.dump({'version':result['version'],'features':features,'balanceFeatures':meta['balanceFeatures'],'models':models,'representationPass':result['representationPass']},mp)
 print(json.dumps({'ok':True,'representationPass':result['representationPass'],'trainRows':result['trainRows'],'testRows':result['testRows'],'tasks':result['tasks'],'checks':result['passChecks'],'output':str(op),'model':str(mp)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
