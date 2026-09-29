from __future__ import annotations
import importlib.util,json
from pathlib import Path
import numpy as np,pandas as pd
D=Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_EPISODES_V2_20260907.csv');BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
A=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',low_memory=False);Q=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',low_memory=False)
spec=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(spec);spec.loader.exec_module(v2)
spec1=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(spec1);spec1.loader.exec_module(v1)
d=pd.read_csv(D,low_memory=False);d['split']=d.market_id.map(v2.frozen_split_map(A,Q));s=d.anchor.map({'UP':1.,'DOWN':-1.});y=d.renewed_clean_lb_positive.to_numpy(int)
f=pd.DataFrame(index=d.index);f['phase']=d.seconds_left/300;phase=['phase']
f['prior_ask']=np.where(s==1,d.predict_up_ask,d.predict_down_ask);f['repair_ask']=np.where(s==1,d.predict_down_ask,d.predict_up_ask);f['pair_surplus']=1-d.predict_up_ask-d.predict_down_ask
f['spot_q']=s*d.spot_queue_imbalance;f['fut_q']=s*d.futures_queue_imbalance
price=['prior_ask'];pair=['pair_surplus'];repair=['repair_ask'];queue=['spot_q','fut_q']
groups={'P_PHASE':phase,'A_PRIOR_ASK':phase+price,'PAIR_SURPLUS':phase+pair,'REPAIR_ASK':phase+repair,'PRICE_PAIR':phase+price+pair+repair,'QUEUE':phase+queue,'QUEUE_PRICE':phase+queue+price,'QUEUE_PRICE_PAIR':phase+queue+price+pair+repair}
tr=d.split.eq('TRAIN').to_numpy();res={};pred={}
for name,cols in groups.items():
  res[name]={};pred[name]={}
  for sp in ['VALIDATION','TEST']:
    te=d.split.eq(sp).to_numpy();x,w=v1.prepare(f.loc[tr,cols],f.loc[te,cols]);b=v1.fit_logit(x,y[tr]);p=1/(1+np.exp(-np.clip(w@b,-35,35)));pred[name][sp]=p;res[name][sp]=v1.metrics(y[te],p)
inc={}
for name in groups:
  if name=='P_PHASE':continue
  inc[name]={}
  for sp in ['VALIDATION','TEST']:
    z=d[d.split.eq(sp)].copy();inc[name][sp]=v2.market_cluster_delta(z,pred['P_PHASE'][sp],pred[name][sp])
# Train-fixed prior ask quartiles and queue relation.
qs=np.unique(np.nanquantile(f.loc[d.split.eq('TRAIN'),'prior_ask'],[0,.25,.5,.75,1]));bins={}
for sp in ['VALIDATION','TEST']:
  idx=d.split.eq(sp);z=d[idx].copy();z['prior_ask']=f.loc[idx,'prior_ask'];z['spot_q']=f.loc[idx,'spot_q'];z['fut_q']=f.loc[idx,'fut_q'];z['bin']=pd.cut(z.prior_ask,qs,include_lowest=True,duplicates='drop')
  bins[sp]=[{'bin':str(k),'n':len(g),'markets':g.market_id.nunique(),'rate':float(g.renewed_clean_lb_positive.mean()),'meanPriorAsk':float(g.prior_ask.mean()),'meanSpotQ':float(g.spot_q.mean()),'meanFutQ':float(g.fut_q.mean())} for k,g in z.groupby('bin',observed=True)]
# correlation price vs oriented queues by split
corr={}
for sp in ['TRAIN','VALIDATION','TEST']:
  idx=d.split.eq(sp);z=f.loc[idx,['prior_ask','spot_q','fut_q']].dropna();corr[sp]={'n':len(z),'priorAsk_spotQ':float(z.prior_ask.corr(z.spot_q)),'priorAsk_futQ':float(z.prior_ask.corr(z.fut_q))}
out={'version':'OUR_C2_ALIGNED_PRICE_OPPORTUNITY_DIAGNOSTIC_V1','status':'RESEARCH_ONLY','models':res,'incrementsVsPhase':inc,'priorAskQuartiles':{'trainEdges':[float(x) for x in qs],**bins},'queuePriceCorrelation':corr,'guards':['Same C2+aligned+full5s conservative renewed-risk cohort.','prior_ask/repair_ask/pair_surplus are strict-past current quote geometry, not future fill price.','Association may reflect liquidity/execution value rather than alpha.']}
Path('data/research/r4_v0/OUR_C2_ALIGNED_PRICE_OPPORTUNITY_DIAGNOSTIC_V1_20260907.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
