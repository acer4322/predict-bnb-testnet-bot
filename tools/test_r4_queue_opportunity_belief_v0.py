from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/r4_v0/hourly'
FILES=[BASE/f'r4_target_depth_archive_hazard_v1_o{o}_m60.csv' for o in (0,60,120,180)]
OUT=BASE/'r4_queue_opportunity_belief_v0.json'

def metric(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def model(seed=27):
 return HistGradientBoostingClassifier(max_depth=3,max_leaf_nodes=12,min_samples_leaf=30,learning_rate=.05,max_iter=220,l2_regularization=2,random_state=seed)
def main():
 dfs=[]
 for p in FILES:
  d=pd.read_csv(p);dfs.append(d)
 d=pd.concat(dfs,ignore_index=True).sort_values(['sample_ms','market_id']).drop_duplicates(['market_id','sample_ms']).copy()
 d['top5_total']=d.bid_top5+d.ask_top5
 d['churn1_total']=d.bid_add_1s+d.ask_add_1s+d.bid_remove_1s+d.ask_remove_1s
 d['depth_imb_top5_abs']=d.book_imb_top5.abs()
 feats={
   'TIME_ONLY':['seconds_left'],
   'QUEUE_PORTABLE':['spread_ticks','top5_total','churn1_total'],
   'TIME_PLUS_QUEUE':['seconds_left','spread_ticks','top5_total','churn1_total'],
 }
 # Strict market chronology: first 3 archive batches/train-ish; final quarter of markets as untouched test by first sample time.
 ms=d.groupby('market_id').sample_ms.min().sort_values().index.astype(int).tolist();n=len(ms);a=int(n*.75);tr=set(ms[:a]);te=set(ms[a:]);train=d[d.market_id.isin(tr)];test=d[d.market_id.isin(te)]
 res={}
 for name,fs in feats.items():
  m=model(2700+len(res));m.fit(train[fs],train.y5.astype(int));res[name]={'train':metric(train.y5,m.predict_proba(train[fs])[:,1]),'test':metric(test.y5,m.predict_proba(test[fs])[:,1]),'features':fs}
 b=res['TIME_ONLY']['test'];q=res['TIME_PLUS_QUEUE']['test'];res['TIME_PLUS_QUEUE']['test']['deltaAucVsTime']=q['auc']-b['auc'];res['TIME_PLUS_QUEUE']['test']['deltaApVsTime']=q['ap']-b['ap'];res['TIME_PLUS_QUEUE']['test']['logLossImprovementVsTime']=b['logLoss']-q['logLoss']
 # Four chronological market blocks with expanding history; no threshold sweep.
 blocks=[];block=max(20,n//5)
 starts=[max(60,int(n*.45)),max(80,int(n*.60)),max(100,int(n*.70)),max(120,int(n*.80))]
 seen=set()
 for i,s in enumerate(starts):
  if s in seen or s>=n-5:continue
  seen.add(s);tm=ms[:s];em=ms[s:min(n,s+block)];trz=d[d.market_id.isin(tm)];tez=d[d.market_id.isin(em)]
  row={'block':i+1,'trainMarkets':len(tm),'testMarkets':len(em),'metrics':{}}
  for j,(name,fs) in enumerate(feats.items()):
   m=model(2800+i*10+j);m.fit(trz[fs],trz.y5.astype(int));row['metrics'][name]=metric(tez.y5,m.predict_proba(tez[fs])[:,1])
  row['deltaQueueVsTime']={'auc':row['metrics']['TIME_PLUS_QUEUE']['auc']-row['metrics']['TIME_ONLY']['auc'],'ap':row['metrics']['TIME_PLUS_QUEUE']['ap']-row['metrics']['TIME_ONLY']['ap'],'logLossImprovement':row['metrics']['TIME_ONLY']['logLoss']-row['metrics']['TIME_PLUS_QUEUE']['logLoss']};blocks.append(row)
 # Freeze portable expert on all Target rows for later shadow use only if supported.
 frozen=model(2999);frozen.fit(d[feats['TIME_PLUS_QUEUE']],d.y5.astype(int))
 import joblib
 mod=BASE/'r4_queue_opportunity_belief_v0.joblib';joblib.dump({'model':frozen,'features':feats['TIME_PLUS_QUEUE']},mod)
 art={'version':'R4_QUEUE_OPPORTUNITY_BELIEF_V0','researchOnly':True,'runtimePromotionAllowed':False,'actionChanges':False,'coverage':{'rows':int(len(d)),'markets':int(d.market_id.nunique()),'positiveRate5s':float(d.y5.mean())},'semanticTarget':'Target inferred Maker placement within 5s. Queue information is learned as a belief/context task, not as an order rule.','clockCaveat':'Target archive forensic features are exchange/source-clock aligned to inferred Target placement. Any HFT transfer must be receipt-clock recomputed and treated as domain-shift shadow evidence only.','featureSets':feats,'holdout':{'trainMarkets':len(tr),'testMarkets':len(te),'results':res},'blocks':blocks,'frozenModel':str(mod.relative_to(ROOT)).replace('\\','/'),'guards':['No winner/settlement.','No side/price/future fill used.','No threshold sweep.','Portable queue features selected before result: seconds_left, spread_ticks, total top5 depth, total 1s add/remove churn.','No action authority.']}
 OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'holdout':res,'blocks':[x['deltaQueueVsTime'] for x in blocks]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
