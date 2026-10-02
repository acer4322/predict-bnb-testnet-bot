from __future__ import annotations
import json,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');SRC=LANE/'C2_FRESH_FIRST_PARENT_CHOICE_CANONICAL_TO_OLDER173_V1.json';OUT=LANE/'C2_FRESH_FIRST_PARENT_MICRO_ABLATION_V1.json'
s=importlib.util.spec_from_file_location('base','tools/validate_target_direction_c2_fresh_first_parent_choice_v1.py');base=importlib.util.module_from_spec(s);s.loader.exec_module(base)
s1=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(s1);s1.loader.exec_module(v1)
s2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(s2);s2.loader.exec_module(v2)
PKG=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907');EXT=LANE/'unseen_older173_v1'
def run():
 can=base.feat(base.build(LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv',PKG/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'));ext=base.feat(base.build(LANE/'UNSEEN_OLDER173_SERVICE_REPLICATION_V1.csv',EXT/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'))
 market=['phase','opp_predict','opp_strike'];groups={'M0_MARKET':market,'Q1_SPOT_QUEUE':market+['o_spot_q'],'Q2_FUT_QUEUE':market+['o_fut_q'],'Q3_BOTH_QUEUE':market+['o_spot_q','o_fut_q'],'T_TAKER':market+['o_spot_t','o_fut_t'],'R_RETURNS':market+['o_spot_r','o_fut_r'],'QT_QUEUE_TAKER':market+['o_spot_q','o_fut_q','o_spot_t','o_fut_t']}
 y=ext.first_parent_same_side.to_numpy(int);pred={};met={}
 for n,c in groups.items():
  x,z=base.fit_transform(can,ext,c);b=v1.fit_logit(x,can.first_parent_same_side.to_numpy(int));p=1/(1+np.exp(-np.clip(z@b,-35,35)));pred[n]=p;met[n]=v1.metrics(y,p)
 inc={n:v2.market_cluster_delta(ext,pred['M0_MARKET'],pred[n],label='first_parent_same_side',resamples=5000) for n in groups if n!='M0_MARKET'}
 # sign-only descriptive rates on external, no threshold fitting
 signs={}
 for c in ['o_spot_q','o_fut_q','o_spot_t','o_fut_t','o_spot_r','o_fut_r']:
  z=ext[ext[c].notna()]; signs[c]={'negativeN':int((z[c]<0).sum()),'negativeSameRate':float(z.loc[z[c]<0,'first_parent_same_side'].mean()) if (z[c]<0).any() else None,'positiveN':int((z[c]>0).sum()),'positiveSameRate':float(z.loc[z[c]>0,'first_parent_same_side'].mean()) if (z[c]>0).any() else None}
 out={'version':'OUR_C2_FRESH_FIRST_PARENT_MICRO_ABLATION_V1','status':'MECHANISM_FALSIFICATION_POST_EXTERNAL','canonicalRows':len(can),'externalRows':len(ext),'groups':groups,'externalMetrics':met,'incrementsVsMarket':inc,'externalSignRates':signs,'guards':['Micro-family decomposition performed after broad microstructure improvement was seen on older173; not an independent promotion test.','Outcome remains first fresh parent prior-anchor side vs opposite side.','Negative oriented queue means ask-side pressure against prior anchor because queue imbalance=(bid-ask)/(bid+ask).','No runtime gate or threshold.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':run()
