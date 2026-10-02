from __future__ import annotations
import argparse,gzip,json,math,os,time
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

TOPO=['logQty','logAge','lotCount','oldestShare','priorNoRepairClocks','logSinceRepair','sinceRepairMissing','prior_NONE','prior_REPAIR_PLUS_EXPAND','prior_REPAIR_PRESENT_NO_NEW_EXPAND','prior_EXPAND_ONLY_WITH_DEBT']
BOOK=['bookAgeMs','repairBid','repairAsk','repairSpreadTicks','repairBidDepth','repairAskDepth','repairTop3Bid','repairTop3Ask','expandBid','expandAsk','expandSpreadTicks','expandBidDepth','expandAskDepth','expandTop3Bid','expandTop3Ask','repairMinusExpandAsk','repairMinusExpandBid','pairAskSum','pairBidSum','repairBidMove1s','repairAskMove1s','repairBidDepthChange1s','repairAskDepthChange1s','repairBidMove3s','repairAskMove3s','repairBidDepthChange3s','repairAskDepthChange3s']
EXEC=['oldestPrice','weightedDebtPrice','oldestPairAtRepairBid','oldestPairAtRepairAsk','weightedPairAtRepairBid','weightedPairAtRepairAsk','insidePassiveAvailable','oldestPairAtInside','weightedPairAtInside','repairSideFillClocks1s','expandSideFillClocks1s','repairSideFillQty1s','expandSideFillQty1s','repairSideFillClocks3s','expandSideFillClocks3s','repairSideFillQty3s','expandSideFillQty3s']
SETS={'TOPOLOGY_ONLY':TOPO,'BOOK_LIFECYCLE_ONLY':BOOK,'TOPOLOGY_PLUS_BOOK':TOPO+BOOK,'TOPOLOGY_PLUS_EXECUTION_GEOMETRY':TOPO+BOOK+EXEC}

def load(paths):
 rows=[];mf={};viol={};seen=set();chunks=[]
 for p in paths:
  with gzip.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  chunks.append({'path':str(p),'rows':len(d['rows']),'markets':len(d['marketFirst']),'violations':d.get('invariantViolations') or {}})
  for k,v in (d.get('invariantViolations') or {}).items():viol[k]=viol.get(k,0)+int(v)
  for k,v in d['marketFirst'].items():
   m=int(k)
   if m in mf: raise RuntimeError(f'duplicate market across chunks:{m}')
   mf[m]=int(v)
  rows.extend(d['rows'])
 return rows,mf,viol,chunks

def split(mf):
 mids=[m for m,_ in sorted(mf.items(),key=lambda kv:(kv[1],kv[0]))];n=len(mids);a=max(1,int(.70*n));b=max(a+1,int(.85*n));return set(mids[:a]),set(mids[a:b]),set(mids[b:])
def mat(rows,features):return np.asarray([[float(r.get(k,math.nan)) if r.get(k) is not None else math.nan for k in features] for r in rows],np.float32)
def met(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'rocAuc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)) if int(y.sum()) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,'brier':float(brier_score_loss(y,p)) if len(y) else None}
def fit_one(train,val,test,features,seed=20260906):
 m=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=40,l2_regularization=2.0,class_weight='balanced',random_state=seed)
 Xtr=mat(train,features);ytr=np.asarray([r['service'] for r in train],int);m.fit(Xtr,ytr)
 out={}
 for name,rr in [('train',train),('validation',val),('test',test)]:
  y=np.asarray([r['service'] for r in rr],int);p=m.predict_proba(mat(rr,features))[:,1];out[name]=met(y,p)
 q=[r for r in test if int(r.get('qualityMarket',0))==1]
 if q:
  y=np.asarray([r['service'] for r in q],int);p=m.predict_proba(mat(q,features))[:,1];out['qualityTest']=met(y,p)
 else:out['qualityTest']={'n':0}
 return m,out

def group_ablation(train,test,base_features):
 groups={'NO_TOPOLOGY':[x for x in base_features if x not in TOPO],'NO_BOOK':[x for x in base_features if x not in BOOK],'NO_EXECUTION_GEOMETRY':[x for x in base_features if x not in EXEC],'TOPOLOGY_EXEC_NO_BOOK':[x for x in base_features if x not in BOOK]}
 out={}
 for i,(name,fs) in enumerate(groups.items()):
  if not fs:continue
  m=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=40,l2_regularization=2.0,class_weight='balanced',random_state=20260920+i)
  ytr=np.asarray([r['service'] for r in train],int);m.fit(mat(train,fs),ytr);y=np.asarray([r['service'] for r in test],int);p=m.predict_proba(mat(test,fs))[:,1];out[name]={'features':len(fs),'test':met(y,p)}
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('inputs',nargs='+');ap.add_argument('--output');x=ap.parse_args();ts=time.time();paths=[Path(p) for p in x.inputs];rows,mf,viol,chunks=load(paths)
 if viol:raise RuntimeError(f'FIFO invariant violations:{viol}')
 trm,vam,tem=split(mf);train=[r for r in rows if int(r['marketId']) in trm];val=[r for r in rows if int(r['marketId']) in vam];test=[r for r in rows if int(r['marketId']) in tem]
 models={};trained={}
 for name,fs in SETS.items():
  _,rep=fit_one(train,val,test,fs);models[name]=rep
 base=models['TOPOLOGY_ONLY']['test'];full=models['TOPOLOGY_PLUS_EXECUTION_GEOMETRY']['test'];lift=float(full['rocAuc']-base['rocAuc']);gate=lift>=.03 and full['logLoss']<=base['logLoss']+1e-12
 out={'version':'TARGET_ETH_FIFO_MICROSTRUCTURE_SERVICE_TEACHER_V1_FULL','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'runtimeSeconds':time.time()-ts,'chunks':chunks,'coverage':{'rows':len(rows),'markets':len(mf),'qualityRows':sum(int(r.get('qualityMarket',0)) for r in rows),'qualityMarkets':len({int(r['marketId']) for r in rows if int(r.get('qualityMarket',0))==1})},'split':{'markets':{'train':len(trm),'validation':len(vam),'test':len(tem)},'rows':{'train':len(train),'validation':len(val),'test':len(test)},'qualityTestRows':sum(1 for r in test if int(r.get('qualityMarket',0))==1)},'featureSets':SETS,'models':models,'fullGroupAblations':group_ablation(train,test,SETS['TOPOLOGY_PLUS_EXECUTION_GEOMETRY']),'primaryGate':{'requiredAucLift':.03,'aucLift':lift,'requiredNoLogLossWorsening':True,'passed':bool(gate)},'invariantViolations':viol,'boundary':['strict-past extracted Target ETH FIFO+public-book rows only','chronological market-level split after merging all chunks','fixed HGB per feature set; no threshold/hyperparameter sweep','current Target action is label only','winner/PnL/future action unused','diagnostic only; no runtime authority/no NEW24-B']}
 result_dir=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.') );result_dir.mkdir(parents=True,exist_ok=True);op=Path(x.output) if x.output else result_dir/'TARGET_ETH_FIFO_MICROSTRUCTURE_SERVICE_TEACHER_V1_FULL_20260906.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'output':str(op),'coverage':out['coverage'],'split':out['split'],'test':{k:v['test'] for k,v in models.items()},'qualityTest':{k:v['qualityTest'] for k,v in models.items()},'ablations':out['fullGroupAblations'],'gate':out['primaryGate'],'runtimeSeconds':out['runtimeSeconds']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
