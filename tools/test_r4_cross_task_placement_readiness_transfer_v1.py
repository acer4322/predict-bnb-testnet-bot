from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
PLACEMENT=ROOT/'data/research/target_maker_direct_hazard_v1.csv'
FORMATION=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_cross_task_placement_readiness_transfer_v1.json'
VERSION='R4_CROSS_TASK_PLACEMENT_READINESS_TRANSFER_V1'

NATIVE=['seconds_left','predict_up_mid','predict_down_mid','predict_edge']
EXTERNAL=[
'spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps',
'futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps','perp_spot_basis_bps','spot_minus_strike_bps','chainlink_minus_strike_bps','spot_minus_chainlink_bps']
FULL=NATIVE+EXTERNAL
LOGIC=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
BASE=LOGIC+['predict_up_mid','predict_edge','predict_supports_dominant','strike_toward_dominant_bps','spot_supports_dominant']

def model(seed):
 return HistGradientBoostingClassifier(learning_rate=.055,max_leaf_nodes=15,max_depth=4,min_samples_leaf=30,l2_regularization=1,max_iter=240,random_state=seed)
def metric(y,p):
 return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def chrono_markets(df,time_col):
 mt=df.groupby('market_id',as_index=False)[time_col].min().sort_values(time_col);return mt.market_id.astype(int).tolist()

def main():
 # Earlier cohort: train a semantic 5-second Maker placement-readiness expert.
 p=pd.read_csv(PLACEMENT).replace([np.inf,-np.inf],np.nan);p.market_id=p.market_id.astype(int);p['predict_edge']=(p.predict_up_mid-.5).abs();label='label_next_inferred_placement_any_5s'
 p=p.dropna(subset=['market_id','decision_sampled_at_ms',label,'seconds_left','predict_up_mid','predict_down_mid']).copy();p[label]=p[label].astype(int)
 ms=chrono_markets(p,'decision_sampled_at_ms');n=len(ms);a=int(n*.70);b=int(n*.85);psplit={'train':ms[:a],'validation':ms[a:b],'test':ms[b:]};pparts={k:p[p.market_id.isin(set(v))] for k,v in psplit.items()}
 source_metrics={};source_models={}
 for j,(name,feats) in enumerate([('NATIVE',NATIVE),('FULL_PUBLIC',FULL)]):
  m=model(20262000+j);m.fit(pparts['train'][feats],pparts['train'][label]);source_models[name]=m;source_metrics[name]={}
  for sk in ('validation','test'):
   z=pparts[sk];source_metrics[name][sk]=metric(z[label].to_numpy(),m.predict_proba(z[feats])[:,1])
 # Final frozen source experts use all earlier placement markets. This is still temporally before the Formation cohort.
 native=model(20262101);native.fit(p[NATIVE],p[label]);full=model(20262102);full.fit(p[FULL],p[label])

 # Later cohort: apply frozen placement readiness scores, then let Formation logic learn how/if to use them.
 f=pd.read_csv(FORMATION).replace([np.inf,-np.inf],np.nan);f.market_id=f.market_id.astype(int);f['repair']=1-f.is_add.astype(int)
 # Ensure common features exist; formation enrichment already carries strict-past public data.
 f['placement_readiness_native_5s']=native.predict_proba(f[NATIVE])[:,1]
 f['placement_readiness_full_5s']=full.predict_proba(f[FULL])[:,1]
 f['placement_readiness_external_delta_5s']=f.placement_readiness_full_5s-f.placement_readiness_native_5s
 fms=chrono_markets(f,'first_event_ms');fn=len(fms);fa=int(fn*.70);fb=int(fn*.85);fsplit={'train':fms[:fa],'validation':fms[fa:fb],'test':fms[fb:]};parts={k:f[f.market_id.isin(set(v))] for k,v in fsplit.items()}
 sets={
  'BASE_PREDICT_STRIKE':BASE,
  'BASE_PLUS_NATIVE_READINESS':BASE+['placement_readiness_native_5s'],
  'BASE_PLUS_FULL_READINESS':BASE+['placement_readiness_full_5s'],
  'BASE_PLUS_READINESS_AND_EXTERNAL_DELTA':BASE+['placement_readiness_full_5s','placement_readiness_external_delta_5s'],
 }
 formation_results={}
 for j,(name,feats) in enumerate(sets.items()):
  m=model(20262200+j);m.fit(parts['train'][feats],parts['train'].repair);formation_results[name]={}
  for sk in ('validation','test'):
   z=parts[sk];formation_results[name][sk]=metric(z.repair.to_numpy(),m.predict_proba(z[feats])[:,1])
 # Diagnostics of transferred readiness vs actual later Target Formation mode.
 test=parts['test'];diag=[]
 for qname,col in [('native','placement_readiness_native_5s'),('full','placement_readiness_full_5s'),('externalDelta','placement_readiness_external_delta_5s')]:
  qs=pd.qcut(test[col].rank(method='first'),4,labels=['Q1','Q2','Q3','Q4'])
  for q in ['Q1','Q2','Q3','Q4']:
   z=test[qs==q];diag.append({'score':qname,'quartile':q,'n':int(len(z)),'meanScore':float(z[col].mean()),'repairRate':float(z.repair.mean()),'meanGap':float(z.pre_abs_payoff_gap.mean())})
 artifact={'version':VERSION,'researchOnly':True,'runtimePromotionAllowed':False,'question':'If spot/futures information first learns its semantically supported job (5s Target Maker placement readiness) on an earlier cohort, does the frozen readiness belief transfer usefully into a later Formation-mode logic teacher?','sourcePlacement':{'rows':int(len(p)),'markets':int(p.market_id.nunique()),'timeRangeMs':[int(p.decision_sampled_at_ms.min()),int(p.decision_sampled_at_ms.max())],'label':label,'split':psplit,'metrics':source_metrics},'targetFormation':{'rows':int(len(f)),'markets':int(f.market_id.nunique()),'timeRangeMs':[int(f.first_event_ms.min()),int(f.first_event_ms.max())],'split':fsplit,'featureSets':sets,'results':formation_results,'testScoreQuartiles':diag},'temporalGuard':{'sourceMaxMs':int(p.decision_sampled_at_ms.max()),'formationMinMs':int(f.first_event_ms.min()),'sourceStrictlyBeforeFormation':bool(p.decision_sampled_at_ms.max()<f.first_event_ms.min())},'guards':['Placement readiness horizon fixed at 5s because prior queue-option research uses a 5s true-rest opportunity window; no horizon sweep here.','Source expert is trained only on an earlier Target placement cohort and frozen before application to the later Formation cohort.','Formation logic receives scores, not raw source labels or future placement outcomes.','Research-only; no action authority.']}
 OUT.write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'temporalGuard':artifact['temporalGuard'],'sourceMetrics':source_metrics,'formationResults':formation_results,'diag':diag},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
