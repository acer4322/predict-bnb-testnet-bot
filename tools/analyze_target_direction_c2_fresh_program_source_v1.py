from __future__ import annotations
import json, importlib.util
from pathlib import Path
import numpy as np, pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
EP=Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_EPISODES_V2_20260907.csv')
TRJ=LANE/'PRIOR_CLEAN_TRAJECTORY_FULL120_V1.csv'
CYC=LANE/'C2_CYCLE_CREDIT_FEATURES_V1.csv'
CONT=LANE/'C2_QUOTE_PROGRAM_CONTINUITY_V1.csv'
OUT=LANE/'C2_FRESH_PROGRAM_SOURCE_FALSIFICATION_V1.json'
CSV=LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv'
SEED=20260907
spec=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(spec);spec.loader.exec_module(v1)
spec2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(spec2);spec2.loader.exec_module(v2)

def slog(x): return np.sign(x)*np.log1p(np.abs(x))
def fit_eval(d,groups,label='fresh_program_admission'):
 y=d[label].to_numpy(int);tr=d.split.eq('TRAIN').to_numpy();res={};pred={}
 for n,cols in groups.items():
  res[n]={};pred[n]={}
  for sp in ['VALIDATION','TEST']:
   te=d.split.eq(sp).to_numpy();x,z=v1.prepare(d.loc[tr,cols],d.loc[te,cols]);b=v1.fit_logit(x,y[tr]);p=1/(1+np.exp(-np.clip(z@b,-35,35)));pred[n][sp]=p;res[n][sp]=v1.metrics(y[te],p)
 inc={}
 for n in groups:
  if n=='F0_CURRENT': continue
  inc[n]={}
  for sp in ['VALIDATION','TEST']:
   z=d[d.split.eq(sp)].copy();inc[n][sp]=v2.market_cluster_delta(z,pred['F0_CURRENT'][sp],pred[n][sp],label=label,resamples=3000)
 return {'models':res,'incrementsVsCurrent':inc}

def contrast(d,cols):
 out={}
 for sp in ['TRAIN','VALIDATION','TEST']:
  z=d[d.split.eq(sp)];a=z[z.fresh_program_admission.eq(1)];b=z[z.fresh_program_admission.eq(0)]
  out[sp]={c:{'posMedian':None if a[c].dropna().empty else float(a[c].median()),'negMedian':None if b[c].dropna().empty else float(b[c].median())} for c in cols}
 return out

def main():
 ep=pd.read_csv(EP,low_memory=False);trj=pd.read_csv(TRJ,low_memory=False);cyc=pd.read_csv(CYC,low_memory=False);ct=pd.read_csv(CONT,low_memory=False)
 base=ep.copy()
 fresh=set(zip(ct.loc[ct.fresh_program_admission,'market_id'].astype(int),ct.loc[ct.fresh_program_admission,'entry_ms'].astype(int)))
 traced=set(zip(ct.loc[ct.traces_to_pre_conflict_program,'market_id'].astype(int),ct.loc[ct.traces_to_pre_conflict_program,'entry_ms'].astype(int)))
 base['fresh_program_admission']=[int((int(m),int(t)) in fresh) for m,t in zip(base.market_id,base.entry_ms)]
 base['traced_program_continuation']=[int((int(m),int(t)) in traced) for m,t in zip(base.market_id,base.entry_ms)]
 tjcols=['market_id','entry_ms','prior_clean_predict_support','prior_clean_strike_support','delta_predict_support','delta_strike_support','prior_side_ask_change','predict_conflict_age_ms','pred_mean_5000','pred_negative_frac_5000','strike_negative_frac_5000','pred_slope_5000','pred_mean_10000','pred_slope_10000']
 cycols=['market_id','entry_ms','w20_repair_qty','w20_debt_reduction_pos','w20_repair_floor_gain_pos','w60_debt_reduction_pos','w60_repair_floor_gain_pos','time_since_repair_ms','reset_repair_qty','reset_repair_to_same_birth','clean_repair_qty','clean_debt_reduction_pos','clean_repair_floor_gain_pos']
 d=base.merge(trj[tjcols],on=['market_id','entry_ms'],how='left',validate='one_to_one').merge(cyc[cycols],on=['market_id','entry_ms'],how='left',validate='one_to_one')
 s=d.anchor.map({'UP':1.,'DOWN':-1.})
 d['phase']=d.seconds_left/300;d['cur_predict_support']=d.predict_support;d['cur_strike_support']=d.strike_support
 d['o_spot_queue']=s*d.spot_queue_imbalance;d['o_fut_queue']=s*d.futures_queue_imbalance;d['o_spot_taker']=s*d.spot_taker_imbalance_1s;d['o_fut_taker']=s*d.futures_taker_imbalance_1s;d['o_spot_ret']=s*d.spot_return_1s_bps;d['o_fut_ret']=s*d.futures_return_1s_bps
 d['log_debt']=np.log1p(d.pre_outstanding_qty.clip(lower=0));d['floor_slog']=slog(d.pre_floor);d['best_slog']=slog(d.pre_best);d['last_age_log']=np.log1p(d.last_action_age_ms.clip(lower=0))
 d['prior_side_ask']=np.where(s==1,d.predict_up_ask,d.predict_down_ask);d['repair_ask']=np.where(s==1,d.predict_down_ask,d.predict_up_ask);d['pair_surplus']=1-d.predict_up_ask-d.predict_down_ask
 for c in ['predict_conflict_age_ms','time_since_repair_ms','w20_repair_qty','w20_debt_reduction_pos','w20_repair_floor_gain_pos','w60_debt_reduction_pos','w60_repair_floor_gain_pos','reset_repair_qty','reset_repair_to_same_birth','clean_repair_qty','clean_debt_reduction_pos','clean_repair_floor_gain_pos']:
  if c.endswith('_ms'): d[c+'_x']=np.log1p(d[c].clip(lower=0))
  else: d[c+'_x']=slog(d[c])
 current=['phase','cur_predict_support','cur_strike_support']
 queue=['o_spot_queue','o_fut_queue']
 flow=['o_spot_taker','o_fut_taker','o_spot_ret','o_fut_ret']
 port=['log_debt','floor_slog','best_slog','last_age_log']
 price=['prior_side_ask','repair_ask','pair_surplus']
 traj=['prior_clean_predict_support','prior_clean_strike_support','delta_predict_support','delta_strike_support','prior_side_ask_change','predict_conflict_age_ms_x','pred_mean_5000','pred_negative_frac_5000','strike_negative_frac_5000','pred_slope_5000']
 cycle=['w20_repair_qty_x','w20_debt_reduction_pos_x','w20_repair_floor_gain_pos_x','time_since_repair_ms_x']
 groups={
  'F0_CURRENT':current,
  'F1_PLUS_QUEUE':current+queue,
  'F2_PLUS_FLOW':current+queue+flow,
  'F3_PLUS_PORT_SERVICE':current+port,
  'F4_PLUS_PRICE':current+price,
  'F5_PLUS_PRIOR_TRAJECTORY':current+traj,
  'F6_PLUS_CYCLE_PROGRESS':current+cycle,
  'F7_COMPACT_MULTI_SOURCE':current+queue+port+price+['predict_conflict_age_ms_x','pred_mean_5000','pred_slope_5000']+cycle,
 }
 model=fit_eval(d,groups)
 summary={sp:{'rows':int(len(z)),'markets':int(z.market_id.nunique()),'fresh':int(z.fresh_program_admission.sum()),'freshRate':float(z.fresh_program_admission.mean()),'traced':int(z.traced_program_continuation.sum())} for sp,z in [('ALL',d)]+[(s,d[d.split.eq(s)]) for s in ['TRAIN','VALIDATION','TEST']]}
 diagnostic_cols=['cur_predict_support','cur_strike_support','o_spot_queue','o_fut_queue','pre_outstanding_qty','pre_floor','last_action_age_ms','prior_side_ask','pair_surplus','prior_clean_predict_support','prior_clean_strike_support','predict_conflict_age_ms','w20_repair_qty','w20_debt_reduction_pos','w20_repair_floor_gain_pos','time_since_repair_ms']
 out={'version':'OUR_C2_FRESH_PROGRAM_SOURCE_FALSIFICATION_V1','status':'RESEARCH_ONLY','summary':summary,'groups':groups,'model':model,'contrasts':contrast(d,diagnostic_cols),'guards':['Fresh-program admission = HQ same-side Maker parent within 5s that cannot be traced by confirmed reprice/refill lifecycle chain back to a pre-conflict same-side quote program.','Untraced is conservative evidence, not proof of private fresh intent; incomplete lifecycle linkage can remain.','Original frozen 72/24/24 split; fixed feature groups; no TEST tuning.','All source features are strict-past at C2 entry; retrospective parent/lifecycle labels are outcome diagnostics only.']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');d.to_csv(CSV,index=False);print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
