from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
sys.path.insert(0,'tools')
from run_target_direction_confidence_c2_aligned_new_risk_v1 import carrier_for_action
BASE=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907')
SRC=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907/C2_ALIGNED_RENEWED_RISK_FULL120_V4.csv')
OUT=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907/C2_ALIGNED_RENEWAL_PATH_V1.json')
def desc(x):
 x=np.asarray(list(x),float);x=x[np.isfinite(x)];return {'n':len(x),'mean':float(x.mean()) if len(x) else None,'median':float(np.median(x)) if len(x) else None,'p25':float(np.quantile(x,.25)) if len(x) else None,'p75':float(np.quantile(x,.75)) if len(x) else None}
def main():
 e=pd.read_csv(SRC,low_memory=False);e=e[e.net_aligned_prior==True].copy();a=pd.read_csv(BASE/'22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv',low_memory=False);b=pd.read_csv(BASE/'35_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_RETROSPECTIVE_V1.csv',low_memory=False);bridge={k:v for k,v in b.groupby(['market_id','event_ms','side'],sort=False)}
 rows=[]
 for r in e.itertuples():
  fut=a[(a.market_id==r.market_id)&(a.event_ms>r.entry_ms)&(a.event_ms<=r.entry_ms+5000)].sort_values('event_ms')
  renew=None;renewcar=None
  for x in fut[(fut.economic_role=='CLEAN_AGGREGATE_EXPAND')&(fut.birth_side==r.anchor)&(fut.birth_qty>1e-9)].itertuples():
   c=carrier_for_action(x,r.entry_ms,bridge)
   if c and c['newBirthLower']>1e-9: renew=x;renewcar=c;break
  if renew is None: continue
  pre=fut[fut.event_ms<renew.event_ms]
  repair_qty=float(pre.repair_qty.sum()); comps=int((pre.economic_role=='COMPOSITE_CROSSING').sum()); rep_only=int((pre.economic_role=='REPAIR_ONLY').sum()); clean_same=int(((pre.economic_role=='CLEAN_AGGREGATE_EXPAND')&(pre.birth_side==r.anchor)).sum());clean_opp=int(((pre.economic_role=='CLEAN_AGGREGATE_EXPAND')&(pre.birth_side!=r.anchor)).sum())
  if len(pre)==0:path='DIRECT_NO_PRIOR_ACTION'
  elif comps>0:path='AFTER_COMPOSITE'
  elif repair_qty>1e-9:path='AFTER_REPAIR_WITHOUT_COMPOSITE'
  elif clean_same>0:path='AFTER_EARLIER_SAME_CLEAN'
  elif clean_opp>0:path='AFTER_OPPOSITE_CLEAN'
  else:path='AFTER_OTHER_ACTION'
  rows.append({'market_id':int(r.market_id),'entry_ms':int(r.entry_ms),'anchor':r.anchor,'delay_ms':int(renew.event_ms-r.entry_ms),'path':path,'prior_action_count':len(pre),'prior_repair_qty':repair_qty,'prior_repair_only_count':rep_only,'prior_composite_count':comps,'prior_same_clean_count':clean_same,'renew_route':renew.route_mix,'renew_birth_qty':float(renew.birth_qty),'renew_new_birth_lower':float(renewcar['newBirthLower']),'renew_new_birth_upper':float(renewcar['newBirthUpper']),'hq_postonly':renewcar['hqPostOnlyMaker'],'taker_or_mixed':renewcar['takerOrMixed'],'entry_floor':float(r.pre_floor),'entry_best':float(r.pre_best),'entry_debt':float(r.pre_outstanding_qty),'entry_predict_support':float(r.predict_support),'entry_strike_support':float(r.strike_support),'entry_spot_queue':float(r.spot_queue_imbalance) if pd.notna(r.spot_queue_imbalance) else np.nan,'entry_futures_queue':float(r.futures_queue_imbalance) if pd.notna(r.futures_queue_imbalance) else np.nan,'entry_spot_taker1s':float(r.spot_taker_imbalance_1s) if pd.notna(r.spot_taker_imbalance_1s) else np.nan,'entry_futures_taker1s':float(r.futures_taker_imbalance_1s) if pd.notna(r.futures_taker_imbalance_1s) else np.nan,'entry_spot_ret1s':float(r.spot_return_1s_bps) if pd.notna(r.spot_return_1s_bps) else np.nan,'entry_futures_ret1s':float(r.futures_return_1s_bps) if pd.notna(r.futures_return_1s_bps) else np.nan})
 d=pd.DataFrame(rows);paths=d.path.value_counts().to_dict();routes=d.renew_route.value_counts().to_dict();
 path_markets={k:int(g.market_id.nunique()) for k,g in d.groupby('path')}; out={'version':'OUR_C2_ALIGNED_RENEWAL_PATH_V1','eligibleAlignedC2':len(e),'renewedCleanEpisodes':len(d),'renewedMarkets':int(d.market_id.nunique()),'renewalRate':len(d)/len(e),'paths':paths,'pathMarkets':path_markets,'pathRates':{k:v/len(d) for k,v in paths.items()},'routes':routes,'delayMs':desc(d.delay_ms),'priorRepairQty':desc(d.prior_repair_qty),'withAnyPriorRepair':int((d.prior_repair_qty>1e-9).sum()),'withAnyPriorRepairRate':float((d.prior_repair_qty>1e-9).mean()),'withPriorComposite':int((d.prior_composite_count>0).sum()),'withPriorCompositeRate':float((d.prior_composite_count>0).mean()),'directNoPriorAction':int((d.path=='DIRECT_NO_PRIOR_ACTION').sum()),'directNoPriorActionRate':float((d.path=='DIRECT_NO_PRIOR_ACTION').mean()),'hqPostOnlyFirstRenewal':int(d.hq_postonly.sum()),'takerOrMixedFirstRenewal':int(d.taker_or_mixed.sum()),'totalConservativeNewBirthLower':float(d.renew_new_birth_lower.sum()),'guards':['First renewed same-side CLEAN is defined by conservative POST/Taker birth lower bound >0.','Prior Repair/composite is observed confirmed action after conflict entry and before first renewal; association is not causal proof.','Overlapping C2 episodes can share actions; counts are episode-path counts, not portfolio totals.','No runtime authority.']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');d.to_csv(OUT.with_suffix('.csv'),index=False);print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
