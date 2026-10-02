from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1];SRC=ROOT/'data/research/target_maker_direct_hazard_v1.csv';OUT=ROOT/'data/research/r4_v0/hourly/target_maker_external_source_ablation_v1.json'
N=['seconds_left','predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid','predict_up_spread','predict_down_spread','predict_mid_sum','predict_up_mid_edge']
SPOT=['spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps','spot_minus_strike_bps']
FUT=['futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','perp_spot_basis_bps']
REF=['chainlink_minus_strike_bps','spot_minus_chainlink_bps']
SETS={'NATIVE':N,'NATIVE_SPOT':N+SPOT,'NATIVE_FUTURES':N+FUT,'NATIVE_REFERENCE':N+REF,'NATIVE_SPOT_FUTURES':N+SPOT+FUT,'FULL_RAW':N+SPOT+FUT+REF}
def mdl():return Pipeline([('imp',SimpleImputer(strategy='median')),('clf',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=160,l2_regularization=3.0,min_samples_leaf=40,random_state=20260826))])
def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1])),'n':len(y),'rate':float(y.mean())}
def main():
 d=pd.read_csv(SRC,low_memory=False).sort_values(['decision_sampled_at_ms','market_id']);mids=d.groupby('market_id').decision_sampled_at_ms.min().sort_values().index.astype(int).tolist();a=int(len(mids)*.70);b=int(len(mids)*.85);train=set(mids[:a]);val=set(mids[a:b]);test=set(mids[b:]);res={}
 for h in (1,2,5):
  lab=f'label_next_inferred_placement_any_{h}s';tr=d[d.market_id.isin(train)];res[str(h)]={}
  for name,feats in SETS.items():
   m=mdl().fit(tr[feats],tr[lab].astype(int));res[str(h)][name]={}
   for sk,ids in [('validation',val),('test',test)]:
    x=d[d.market_id.isin(ids)];res[str(h)][name][sk]=met(x[lab].astype(int),m.predict_proba(x[feats])[:,1])
  for sk in ('validation','test'):
   base=res[str(h)]['NATIVE'][sk];res[str(h)].setdefault('deltas',{})[sk]={name:{'dAuc':res[str(h)][name][sk]['auc']-base['auc'],'dAP':res[str(h)][name][sk]['ap']-base['ap'],'dLogLossImprovement':base['logLoss']-res[str(h)][name][sk]['logLoss']} for name in SETS if name!='NATIVE'}
 rep={'version':'TARGET_MAKER_EXTERNAL_SOURCE_ABLATION_V1','dataset':{'rows':len(d),'markets':len(mids),'trainMarkets':len(train),'validationMarkets':len(val),'testMarkets':len(test)},'featureGroups':{'native':N,'spot':SPOT,'futures':FUT,'reference':REF},'results':res,'guards':['Fixed chronological split identical across ablations','Strict-past stored public state only','Target future placement is label only','No threshold sweep','Source ablation is attribution, not proof of causal use by Target']};OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'deltas':{h:res[h]['deltas'] for h in res}},ensure_ascii=False))
if __name__=='__main__':main()
