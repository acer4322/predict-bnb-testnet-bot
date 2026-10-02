from __future__ import annotations
import importlib.util,json
from pathlib import Path
import numpy as np,pandas as pd
D=Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_EPISODES_V2_20260907.csv')
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
A=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',low_memory=False);Q=pd.read_csv(BASE/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv',low_memory=False)
spec=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(spec);spec.loader.exec_module(v2)
spec1=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(spec1);spec1.loader.exec_module(v1)
d=pd.read_csv(D,low_memory=False); smap=v2.frozen_split_map(A,Q);d['split']=d.market_id.map(smap);s=d.anchor.map({'UP':1.,'DOWN':-1.});y=d.renewed_clean_lb_positive.to_numpy(int)
f=pd.DataFrame(index=d.index);f['phase']=d.seconds_left/300;phase=['phase']
f['opp_predict']=-d.predict_support;f['opp_strike']=-d.strike_support;mag=['opp_predict','opp_strike']
f['spot_q']=s*d.spot_queue_imbalance;f['fut_q']=s*d.futures_queue_imbalance;queue=['spot_q','fut_q']
f['spot_taker']=s*d.spot_taker_imbalance_1s;f['fut_taker']=s*d.futures_taker_imbalance_1s;flow=['spot_taker','fut_taker']
f['spot_ret']=s*d.spot_return_1s_bps;f['fut_ret']=s*d.futures_return_1s_bps;ret=['spot_ret','fut_ret']
groups={'P_PHASE':phase,'M_CONFLICT_MAG':phase+mag,'Q_QUEUE':phase+queue,'F_TAKER_FLOW':phase+flow,'R_RETURNS':phase+ret,'MQ_MAG_QUEUE':phase+mag+queue,'MF_MAG_FLOW':phase+mag+flow,'MR_MAG_RET':phase+mag+ret,'FULL_MARKET':phase+mag+queue+flow+ret}
tr=d.split.eq('TRAIN').to_numpy();res={};pred={}
for name,cols in groups.items():
  res[name]={};pred[name]={}
  for sp in ['VALIDATION','TEST']:
    te=d.split.eq(sp).to_numpy();x,w=v1.prepare(f.loc[tr,cols],f.loc[te,cols]);b=v1.fit_logit(x,y[tr]);p=1/(1+np.exp(-np.clip(w@b,-35,35)));pred[name][sp]=p;res[name][sp]=v1.metrics(y[te],p)
inc={}
for name in groups:
  if name=='P_PHASE':continue
  inc[name+'_minus_PHASE']={}
  for sp in ['VALIDATION','TEST']:
    z=d[d.split.eq(sp)].copy();inc[name+'_minus_PHASE'][sp]=v2.market_cluster_delta(z,pred['P_PHASE'][sp],pred[name][sp])
# For each oriented micro source, compare sign-supporting prior vs opposing prior.
assoc={}
for col in ['spot_q','fut_q','spot_taker','fut_taker','spot_ret','fut_ret']:
  assoc[col]={}
  for sp in ['VALIDATION','TEST']:
    z=d[d.split.eq(sp)].copy();vals=f.loc[z.index,col];good=vals.notna();zz=z[good].copy();vv=vals[good];pos=zz[vv>0];neg=zz[vv<0]
    assoc[col][sp]={'positiveSupportN':len(pos),'positiveSupportRate':float(pos.renewed_clean_lb_positive.mean()) if len(pos) else None,'negativeSupportN':len(neg),'negativeSupportRate':float(neg.renewed_clean_lb_positive.mean()) if len(neg) else None}
out={'version':'OUR_C2_ALIGNED_MARKET_SOURCE_ABLATION_V1','status':'RESEARCH_ONLY','rows':len(d),'markets':int(d.market_id.nunique()),'models':res,'incrementsVsPhase':inc,'orientedSignAssociation':assoc,'guards':['Same frozen C2+aligned+full5s conservative renewed-risk cohort.','C2 already conditions Predict and strike against prior side; magnitude models measure degree of opposition, not unconstrained direction choice.','Microstructure signs are oriented to prior clean side.','Predictive association only.']}
Path('data/research/r4_v0/OUR_C2_ALIGNED_MARKET_SOURCE_ABLATION_V1_20260907.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
