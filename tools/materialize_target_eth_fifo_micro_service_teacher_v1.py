from __future__ import annotations
import argparse,gzip,json,math,os,platform,time
from pathlib import Path
import joblib,numpy as np,sklearn
from sklearn.ensemble import HistGradientBoostingClassifier

TOPO=['logQty','logAge','lotCount','oldestShare','priorNoRepairClocks','logSinceRepair','sinceRepairMissing','prior_NONE','prior_REPAIR_PLUS_EXPAND','prior_REPAIR_PRESENT_NO_NEW_EXPAND','prior_EXPAND_ONLY_WITH_DEBT']
BOOK=['bookAgeMs','repairBid','repairAsk','repairSpreadTicks','repairBidDepth','repairAskDepth','repairTop3Bid','repairTop3Ask','expandBid','expandAsk','expandSpreadTicks','expandBidDepth','expandAskDepth','expandTop3Bid','expandTop3Ask','repairMinusExpandAsk','repairMinusExpandBid','pairAskSum','pairBidSum','repairBidMove1s','repairAskMove1s','repairBidDepthChange1s','repairAskDepthChange1s','repairBidMove3s','repairAskMove3s','repairBidDepthChange3s','repairAskDepthChange3s']
EXEC=['oldestPrice','weightedDebtPrice','oldestPairAtRepairBid','oldestPairAtRepairAsk','weightedPairAtRepairBid','weightedPairAtRepairAsk','insidePassiveAvailable','oldestPairAtInside','weightedPairAtInside','repairSideFillClocks1s','expandSideFillClocks1s','repairSideFillQty1s','expandSideFillQty1s','repairSideFillClocks3s','expandSideFillClocks3s','repairSideFillQty3s','expandSideFillQty3s']
FEATURES=TOPO+BOOK+EXEC

def load(paths):
 rows=[];mf={};viol={}
 for p in paths:
  with gzip.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  for k,v in (d.get('invariantViolations') or {}).items():viol[k]=viol.get(k,0)+int(v)
  for k,v in d['marketFirst'].items():
   m=int(k)
   if m in mf:raise RuntimeError(f'duplicate market:{m}')
   mf[m]=int(v)
  rows.extend(d['rows'])
 return rows,mf,viol

def split(mf):
 mids=[m for m,_ in sorted(mf.items(),key=lambda kv:(kv[1],kv[0]))];n=len(mids);a=max(1,int(.70*n));b=max(a+1,int(.85*n));return mids[:a],mids[a:b],mids[b:]
def mat(rows):return np.asarray([[float(r.get(k,math.nan)) if r.get(k) is not None else math.nan for k in FEATURES] for r in rows],np.float32)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('inputs',nargs='+');a=ap.parse_args();ts=time.time();rows,mf,viol=load([Path(p) for p in a.inputs])
 if viol:raise RuntimeError(f'invariants:{viol}')
 trm,vam,tem=split(mf);trs=set(trm);train=[r for r in rows if int(r['marketId']) in trs];y=np.asarray([r['service'] for r in train],int)
 model=HistGradientBoostingClassifier(max_iter=260,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=40,l2_regularization=2.0,class_weight='balanced',random_state=20260906).fit(mat(train),y)
 root=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'));root.mkdir(parents=True,exist_ok=True);mp=root/'TARGET_ETH_FIFO_MICROSTRUCTURE_SERVICE_TEACHER_V1_FULL_20260906.joblib';meta=root/'TARGET_ETH_FIFO_MICROSTRUCTURE_SERVICE_TEACHER_V1_MODEL_META_20260906.json'
 art={'version':'TARGET_ETH_FIFO_MICROSTRUCTURE_SERVICE_TEACHER_V1_FULL','researchOnly':True,'actionAuthority':False,'features':FEATURES,'topologyFeatures':TOPO,'bookFeatures':BOOK,'executionGeometryFeatures':EXEC,'model':model,'trainMarkets':trm,'validationMarkets':vam,'testMarkets':tem,'sklearnVersion':sklearn.__version__,'pythonVersion':platform.python_version(),'modelParams':{'max_iter':260,'learning_rate':.05,'max_leaf_nodes':31,'min_samples_leaf':40,'l2_regularization':2.0,'class_weight':'balanced','random_state':20260906},'boundary':['same frozen full representation as prereg/full evaluation','Target future action is label only','no winner/PnL/runtime authority']}
 joblib.dump(art,mp,compress=3)
 meta.write_text(json.dumps({'version':art['version'],'features':FEATURES,'trainMarkets':len(trm),'validationMarkets':len(vam),'testMarkets':len(tem),'rows':len(rows),'trainRows':len(train),'sklearnVersion':sklearn.__version__,'pythonVersion':platform.python_version(),'joblib':str(mp),'bytes':mp.stat().st_size,'runtimeSeconds':time.time()-ts,'invariantViolations':viol},indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'joblib':str(mp),'meta':str(meta),'bytes':mp.stat().st_size,'runtimeSeconds':time.time()-ts,'trainRows':len(train)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
