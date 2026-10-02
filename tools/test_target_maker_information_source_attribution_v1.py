from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
OUT=ROOT/'data/research/r4_v0/hourly/target_maker_information_source_attribution_v1.json'
NATIVE=['seconds_left','predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid','predict_up_spread','predict_down_spread','predict_mid_sum','predict_up_mid_edge']
EXTERNAL=['spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps','futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','perp_spot_basis_bps','spot_minus_strike_bps','chainlink_minus_strike_bps','spot_minus_chainlink_bps']
COMPOSITE=['direction_score','abs_direction_score']
SETS={'TIME_ONLY':['seconds_left'],'NATIVE':NATIVE,'EXTERNAL_TIME':['seconds_left']+EXTERNAL,'NATIVE_EXTERNAL':NATIVE+EXTERNAL,'NATIVE_EXTERNAL_COMPOSITE':NATIVE+EXTERNAL+COMPOSITE}
LABELS={h:f'label_next_inferred_placement_any_{h}s' for h in (1,2,5)}
def metrics(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def model():
 return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=160,l2_regularization=3.0,min_samples_leaf=40,random_state=20260826))])
def main():
 d=pd.read_csv(SRC,low_memory=False).sort_values(['decision_sampled_at_ms','market_id']).reset_index(drop=True)
 mids=d.groupby('market_id').decision_sampled_at_ms.min().sort_values().index.astype(int).tolist(); a=int(len(mids)*.70);b=int(len(mids)*.85)
 split={'train':mids[:a],'validation':mids[a:b],'test':mids[b:]}; out={}
 for h,label in LABELS.items():
  out[str(h)]={}
  trained={}
  for name,feats in SETS.items():
   m=model(); tr=d[d.market_id.isin(split['train'])];m.fit(tr[feats],tr[label].astype(int));trained[name]=m
   out[str(h)][name]={}
   for sk,ids in split.items():
    x=d[d.market_id.isin(ids)];out[str(h)][name][sk]=metrics(x[label].astype(int),m.predict_proba(x[feats])[:,1])
  for sk in ('validation','test'):
   base=out[str(h)]['NATIVE'][sk];full=out[str(h)]['NATIVE_EXTERNAL'][sk];comp=out[str(h)]['NATIVE_EXTERNAL_COMPOSITE'][sk]
   out[str(h)].setdefault('incremental',{})[sk]={'externalOverNative_auc':full['auc']-base['auc'],'externalOverNative_ap':full['ap']-base['ap'],'externalOverNative_logLossImprovement':base['logLoss']-full['logLoss'],'compositeOverRawFull_auc':comp['auc']-full['auc'],'compositeOverRawFull_ap':comp['ap']-full['ap'],'compositeOverRawFull_logLossImprovement':full['logLoss']-comp['logLoss']}
 # regime stability: train on first 60% markets and score four subsequent chronological blocks using NATIVE vs NATIVE_EXTERNAL only
 n=len(mids);cut=int(n*.60);train_ids=mids[:cut];remaining=mids[cut:];blocks=np.array_split(remaining,4); stability={}
 for h,label in LABELS.items():
  tr=d[d.market_id.isin(train_ids)];mn=model().fit(tr[NATIVE],tr[label].astype(int));mf=model().fit(tr[NATIVE+EXTERNAL],tr[label].astype(int));rows=[]
  for bi,ids0 in enumerate(blocks,1):
   ids=[int(x) for x in ids0];x=d[d.market_id.isin(ids)];yn=x[label].astype(int);bn=metrics(yn,mn.predict_proba(x[NATIVE])[:,1]);bf=metrics(yn,mf.predict_proba(x[NATIVE+EXTERNAL])[:,1]);rows.append({'block':bi,'markets':len(ids),'firstMarketTime':int(x.decision_sampled_at_ms.min()),'lastMarketTime':int(x.decision_sampled_at_ms.max()),'native':bn,'nativeExternal':bf,'deltaAuc':bf['auc']-bn['auc'],'deltaAP':bf['ap']-bn['ap'],'deltaLogLossImprovement':bn['logLoss']-bf['logLoss']})
  stability[str(h)]=rows
 rep={'version':'TARGET_MAKER_INFORMATION_SOURCE_ATTRIBUTION_V1','researchOnly':True,'question':'Does measured external public information add chronology-stable predictive value for Target early Maker placement timing beyond native Predict price/time state?','dataset':{'rows':len(d),'markets':len(mids),'source':str(SRC.relative_to(ROOT)),'splitMarkets':{k:len(v) for k,v in split.items()}},'featureGroups':{'native':NATIVE,'externalPublic':EXTERNAL,'engineeredComposite':COMPOSITE},'results':out,'chronologicalStability':stability,'guards':['All features are strict-past fields already stored in the direct-hazard dataset.','External means measured public spot/futures/Chainlink information, not private/secret information.','Target placement/future is label only.','No threshold sweep; compare nested feature groups on fixed chronological splits.','Failure of measured external features to explain residuals cannot prove absence of private information.']}
 OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'dataset':rep['dataset'],'incremental':{h:out[h]['incremental'] for h in out},'stability':stability},ensure_ascii=False))
if __name__=='__main__':main()
