from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1];SRC=ROOT/'data/research/target_maker_direct_hazard_v1.csv';OUT=ROOT/'data/research/r4_v0/hourly/target_maker_external_lead_lag_v1.json'
N=['seconds_left','predict_up_bid','predict_up_ask','predict_up_mid','predict_down_bid','predict_down_ask','predict_down_mid','predict_up_spread','predict_down_spread','predict_mid_sum','predict_up_mid_edge']
E=['spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps','futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','perp_spot_basis_bps','spot_minus_strike_bps']
def mdl():return Pipeline([('i',SimpleImputer(strategy='median')),('m',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=160,l2_regularization=3.0,min_samples_leaf=40,random_state=20260826))])
def met(y,p):return {'n':len(y),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def main():
 d=pd.read_csv(SRC,low_memory=False).sort_values(['market_id','decision_sampled_at_ms']).copy()
 for c in E:d[c+'_lag1']=d.groupby('market_id')[c].shift(1)
 EL=[c+'_lag1' for c in E]
 d=d.sort_values(['decision_sampled_at_ms','market_id']);mids=d.groupby('market_id').decision_sampled_at_ms.min().sort_values().index.astype(int).tolist();a=int(len(mids)*.70);b=int(len(mids)*.85);parts={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])}
 cfg={'NATIVE':N,'NATIVE_CURRENT_EXT':N+E,'NATIVE_LAG1_EXT':N+EL,'NATIVE_CURRENT_AND_LAG1':N+E+EL};res={}
 for h in (1,2,5):
  lab=f'label_next_inferred_placement_any_{h}s';tr=d[d.market_id.isin(parts['train'])];res[str(h)]={}
  for name,feats in cfg.items():
   m=mdl().fit(tr[feats],tr[lab].astype(int));res[str(h)][name]={}
   for sk,ids in parts.items():
    x=d[d.market_id.isin(ids)];res[str(h)][name][sk]=met(x[lab].astype(int),m.predict_proba(x[feats])[:,1])
  for sk in ('validation','test'):
   b0=res[str(h)]['NATIVE'][sk];res[str(h)].setdefault('delta',{})[sk]={name:{'dAuc':res[str(h)][name][sk]['auc']-b0['auc'],'dAP':res[str(h)][name][sk]['ap']-b0['ap'],'dLogLossImprovement':b0['logLoss']-res[str(h)][name][sk]['logLoss']} for name in cfg if name!='NATIVE'}
 rep={'version':'TARGET_MAKER_EXTERNAL_LEAD_LAG_V1','researchOnly':True,'medianCheckpointSpacingMs':1022,'semantics':'Compare current strict-past spot/futures fields with the previous ~1.02s checkpoint values. If current external materially beats lag1 after controlling native Predict/time, external information is more consistent with a short-lived leading cue than a slow regime proxy.','results':res,'guards':['Chronological market split','No threshold sweep','Current external remains strict-past at decision checkpoint','Lagged external is previous stored checkpoint within same market','Predict/time state held in every model','Attribution does not prove Target causally reads any specific feed']};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'delta':{h:res[h]['delta'] for h in res}},ensure_ascii=False))
if __name__=='__main__':main()
