from __future__ import annotations

import importlib.util,json,math,sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
OUR=OUT/'our_synthetic_repair_add_reentry_hazard_bridge_v0_states.csv';HIST=OUT/'post_taker_maker_reentry_hazard_v0.csv';HAND=OUT/'post_taker_handoff_states_v1.csv';TAKER=OUT/'taker_event_states_v1.csv';FRESH=OUT/'post_taker_maker_reentry_hazard_forward_states_v0.csv';FHAND=OUT/'forward_handoff_states_v1.csv';FTAKER=OUT/'forward_taker_states_v1.csv';OPP_ART=OUT/'post_taker_reentry_opp_plus_memory_v0.joblib';REPORT=OUT/'target_vs_our_reentry_state_ood_v0_report.json'
P=ROOT/'tools'/'analyze_post_taker_reentry_by_effect_v0.py';spec=importlib.util.spec_from_file_location('ood_eff',P);eff=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=eff;spec.loader.exec_module(eff)
CUTOFF=1787041800000;EFFECTS=['REPAIR_EFFECT','ADD_EFFECT']

def q(x,p):
 a=np.asarray(pd.to_numeric(x,errors='coerce').dropna(),float);return float(np.quantile(a,p)) if len(a) else None

def stats(x):
 a=np.asarray(pd.to_numeric(x,errors='coerce').dropna(),float)
 if not len(a):return {'n':0}
 return {'n':len(a),'mean':float(np.mean(a)),'median':float(np.median(a)),'p05':float(np.quantile(a,.05)),'p25':float(np.quantile(a,.25)),'p75':float(np.quantile(a,.75)),'p95':float(np.quantile(a,.95))}

def dist(t,o):
 ts=stats(t);os=stats(o)
 if ts.get('n',0)<30 or os.get('n',0)<30:return None
 iqr=max(abs(ts['p75']-ts['p25']),1e-9);shift=abs(os['median']-ts['median'])/iqr
 oa=np.asarray(pd.to_numeric(o,errors='coerce').dropna(),float);ood=float(np.mean((oa<ts['p05'])|(oa>ts['p95']))) if len(oa) else None
 return {'target':ts,'our':os,'medianShiftInTargetIQR':float(shift),'ourOutsideTargetP05P95Rate':ood}

def score(df,art):
 fs=art['features'];X=df.reindex(columns=fs).apply(pd.to_numeric,errors='coerce');return art['model'].predict_proba(X)[:,1]

def main():
 art=joblib.load(OPP_ART);features=list(art['features'])
 h=pd.read_csv(HIST);hh=pd.read_csv(HAND);tt=pd.read_csv(TAKER);target=eff.attach_effect(h,hh,tt);target=target[(target.market_end_ms>CUTOFF)&target.label_effect.isin(EFFECTS)].copy()
 if FRESH.exists() and FHAND.exists() and FTAKER.exists():
  z=eff.attach_effect(pd.read_csv(FRESH),pd.read_csv(FHAND),pd.read_csv(FTAKER));z=z[z.label_effect.isin(EFFECTS)].copy();target=pd.concat([target,z],ignore_index=True,sort=False)
 target['p_opp']=score(target,art)
 our=pd.read_csv(OUR)
 rep={'reportVersion':'TARGET_VS_OUR_REENTRY_STATE_OOD_V0','researchOnly':True,'question':'Why do Target-trained REPAIR/ADD OPP re-entry hazards retain ~0.79 AUC on Target but weakly rank OUR synthetic REPAIR/ADD counterfactual value?','targetReference':'Target rows after hazard training cutoff plus fresh forward rows','effects':{}}
 for e in EFFECTS:
  t=target[target.label_effect==e].copy();o=our[our.syntheticEffect==e].copy();dd=[]
  for f in features:
   of='state_'+f
   if f not in t.columns or of not in o.columns:continue
   d=dist(t[f],o[of])
   if d:dd.append({'feature':f,**d})
  dd.sort(key=lambda x:(x['ourOutsideTargetP05P95Rate'],x['medianShiftInTargetIQR']),reverse=True)
  scale_names=['maker_gross','maker_net','maker_abs_net','taker_gross','taker_net','taker_abs_net','combined_gross','combined_net','combined_abs_net','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','intervention_shares']
  invariant_names=['maker_imbalance_ratio','maker_paired_coverage','taker_imbalance_ratio','taker_paired_coverage','combined_imbalance_ratio','combined_paired_coverage','maker_side_streak','taker_side_streak','last_maker_age_ms','last_taker_age_ms','up_spread_ticks','down_spread_ticks','pair_bid_edge','pair_ask_edge','combined_avg_pair_edge']
  by={x['feature']:x for x in dd}
  rep['effects'][e]={'coverage':{'targetRows':len(t),'targetParents':int(t.parent_id.nunique()),'ourRows':len(o),'ourMarkets':int(o.marketId.nunique())},'scoreDistribution':{'target':stats(t.p_opp),'our':stats(o.pOpp)},'topOodFeatures':dd[:25],'scaleSensitive':{k:by[k] for k in scale_names if k in by},'scaleInvariant':{k:by[k] for k in invariant_names if k in by}}
 rep['interpretationBoundary']='Large OOD in absolute inventory/flow scale with smaller OOD in ratios/ages/book state supports training a scale-normalized transferable student. This audit is descriptive; it does not alter frozen Target forward models.'
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
