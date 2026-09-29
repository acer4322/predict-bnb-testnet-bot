from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1';S=ROOT/'data/research/supervisor_options_v0'
FRESH=P/'r4_management_student_v0_fresh13_pilot_rows.csv'
OLD=S/'supervisor_target_act_states_v2.csv'
GROUPS={
 'ECON':['seconds_left','maker_gross','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_abs_net','combined_gross','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap'],
 'BOOK':['up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','pair_bid_edge','pair_ask_edge','dominant_opp_bid_pair_edge'],
 'LIFECYCLE':['last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_10s','taker_shares_10s','last_place_age_ms','placements_1s','placements_5s','placements_10s'],
 'MEMORY':['maker_net_delta3s','maker_net_delta10s','maker_net_delta30s','maker_abs_net_delta3s','maker_abs_net_delta10s','maker_abs_net_delta30s','combined_net_delta3s','combined_net_delta10s','combined_net_delta30s','combined_abs_net_delta3s','combined_abs_net_delta10s','combined_abs_net_delta30s','worst_case_floor_delta3s','worst_case_floor_delta10s','worst_case_floor_delta30s']
}
def qstats(s):
 x=pd.to_numeric(s,errors='coerce');return {'n':int(x.notna().sum()),'nanRate':float(x.isna().mean()),'median':float(x.median()) if x.notna().any() else None,'q10':float(x.quantile(.1)) if x.notna().any() else None,'q90':float(x.quantile(.9)) if x.notna().any() else None}
def shift(a,b):
 xa=pd.to_numeric(a,errors='coerce');xb=pd.to_numeric(b,errors='coerce');ma,mb=xa.median(),xb.median();iqr=max(1e-9,float(xa.quantile(.75)-xa.quantile(.25)));return float(abs(mb-ma)/iqr) if np.isfinite(ma) and np.isfinite(mb) else None
def main():
 f=pd.read_csv(FRESH);o=pd.read_csv(OLD);order=o.groupby('market_id').market_end_ms.min().sort_values().index.astype(int).tolist();old=o[o.market_id.isin(set(order[-80:]))].copy();out={'version':'R4_MANAGEMENT_STUDENT_V0_FRESH_DRIFT_AUDIT','researchOnly':True,'freshMarkets':int(f.market_id.nunique()),'freshRows':len(f),'oldMarkets':int(old.market_id.nunique()),'oldRows':len(old),'labelPrior':{'fresh':f.option_mode_v2.value_counts(normalize=True).to_dict(),'old':old.option_mode_v2.value_counts(normalize=True).to_dict()},'groups':{}}
 for g,cols in GROUPS.items():
  rows=[]
  for c in cols:
   if c not in f.columns or c not in old.columns: continue
   rows.append({'feature':c,'shiftIQR':shift(old[c],f[c]),'old':qstats(old[c]),'fresh':qstats(f[c])})
  rows.sort(key=lambda z:-1 if z['shiftIQR'] is None else -z['shiftIQR']);out['groups'][g]={'features':rows,'meanShiftIQR':float(np.nanmean([r['shiftIQR'] for r in rows if r['shiftIQR'] is not None])) if rows else None,'maxShiftIQR':max([r['shiftIQR'] for r in rows if r['shiftIQR'] is not None],default=None)}
 (P/'r4_management_student_v0_fresh_drift_audit.json').write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
