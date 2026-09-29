from __future__ import annotations
import json, importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
SRC=LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv';OUT=LANE/'C2_FRESH_PROGRAM_LOWDIM_V1.json'
spec=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(spec);spec.loader.exec_module(v1)
spec2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(v2)

def fit_eval(d,groups):
 y=d.fresh_program_admission.to_numpy(int);tr=d.split.eq('TRAIN').to_numpy();res={};pred={}
 for n,c in groups.items():
  res[n]={};pred[n]={}
  for sp in ['VALIDATION','TEST']:
   te=d.split.eq(sp).to_numpy();x,z=v1.prepare(d.loc[tr,c],d.loc[te,c]);b=v1.fit_logit(x,y[tr]);p=1/(1+np.exp(-np.clip(z@b,-35,35)));res[n][sp]=v1.metrics(y[te],p);pred[n][sp]=p
 inc={}
 for n in groups:
  if n=='L0_CURRENT':continue
  inc[n]={}
  for sp in ['VALIDATION','TEST']:
   z=d[d.split.eq(sp)].copy();inc[n][sp]=v2.market_cluster_delta(z,pred['L0_CURRENT'][sp],pred[n][sp],label='fresh_program_admission',resamples=5000)
 return {'models':res,'incrementsVsCurrent':inc}

def main():
 d=pd.read_csv(SRC,low_memory=False)
 d['conflict_age_log']=np.log1p(d.predict_conflict_age_ms.clip(lower=0));d['last_age_log2']=np.log1p(d.last_action_age_ms.clip(lower=0));d['debt_log2']=np.log1p(d.pre_outstanding_qty.clip(lower=0));d['floor_slog2']=np.sign(d.pre_floor)*np.log1p(abs(d.pre_floor))
 cur=['phase','cur_predict_support','cur_strike_support']
 groups={
 'L0_CURRENT':cur,
 'L1_CONFLICT_AGE':cur+['conflict_age_log'],
 'L2_PRIOR_STRIKE':cur+['prior_clean_strike_support'],
 'L3_AGE_PRIOR_STRIKE':cur+['conflict_age_log','prior_clean_strike_support'],
 'L4_SERVICE_AGE':cur+['last_age_log2'],
 'L5_STATIC_PORT':cur+['debt_log2','floor_slog2'],
 'L6_PORT_MINIMAL':cur+['debt_log2','floor_slog2','last_age_log2'],
 }
 out={'version':'OUR_C2_FRESH_PROGRAM_LOWDIM_V1','status':'POST_HOC_FALSIFICATION_ONLY','rows':len(d),'markets':int(d.market_id.nunique()),'groups':groups,'results':fit_eval(d,groups),'guards':['Features selected after inspecting canonical120 contrasts; results are post-hoc and cannot promote authority.','Purpose is to decide what single seam deserves independent-cohort validation, not to optimize TEST.','No thresholds; same frozen split and market-cluster bootstrap.']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
