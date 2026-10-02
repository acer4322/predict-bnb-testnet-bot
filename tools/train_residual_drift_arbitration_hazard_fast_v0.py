from __future__ import annotations
import json
from pathlib import Path
import joblib, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
ROWS=OUT/'residual_drift_repair_arbitration_3s_states_v0.csv'
FROZEN=OUT/'frozen_hazard_3s_full.joblib'
ART=OUT/'residual_drift_repair_arbitration_3s_hgb_fast_v0.joblib'
REPORT=OUT/'residual_drift_repair_arbitration_3s_hgb_fast_v0_report.json'
BASE_FEATURES=[
 'seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
 'taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage',
 'combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
 'worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign',
 'last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms',
 'maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s',
 'maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak',
 'combined_absnet_change_10s','maker_absnet_change_10s','maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge',
 'taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge',
 'up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth',
 'pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge',
 'last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak',
 'maker_coverage_gap','combined_coverage_gap','floor_per_maker_gross','floor_per_combined_gross','absnet_per_maker_gross','maker_absnet_velocity_10s_per_gross','recent_maker_share_rate_10s']

def metric(y,p):
 y=np.asarray(y,dtype=int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()),'predMean':float(p.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}

def base_prob(art,x):
 return art['model'].predict_proba(x[list(art['features'])].apply(pd.to_numeric,errors='coerce'))[:,1]

def main():
 d=pd.read_csv(ROWS).sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True)
 ms=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']); ids=ms.market_id.astype(int).tolist(); a=int(len(ids)*.70); b=int(len(ids)*.85)
 splits={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}; parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in splits.items()}
 model=HistGradientBoostingClassifier(learning_rate=.05,max_iter=240,max_leaf_nodes=15,min_samples_leaf=60,l2_regularization=1.0,early_stopping=True,validation_fraction=.1,n_iter_no_change=25,random_state=20260820)
 model.fit(parts['train'][BASE_FEATURES].apply(pd.to_numeric,errors='coerce'),parts['train'].label_repair_next3s.astype(int))
 frozen=joblib.load(FROZEN); results={}
 for n,x in parts.items():
  y=x.label_repair_next3s.astype(int).to_numpy(); p=model.predict_proba(x[BASE_FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]; q=base_prob(frozen,x)
  m=metric(y,p); bm=metric(y,q); results[n]={'repairSpecificFast':m,'frozenGeneralTaker3s':bm,'lift':{'auc':m['auc']-bm['auc'],'ap':m['ap']-bm['ap'],'logLoss':m['logLoss']-bm['logLoss'],'brier':m['brier']-bm['brier']}}
 payload={'version':'RESIDUAL_DRIFT_REPAIR_ARBITRATION_3S_HGB_FAST_V0','model':model,'features':BASE_FEATURES,'trainingMarkets':sorted(splits['train']),'trainingMaxEndMs':int(parts['train'].market_end_ms.max()),'semantics':'repair-specific learnability probe only; episode eligibility, never direct Taker action'}; joblib.dump(payload,ART)
 rep={'reportVersion':'RESIDUAL_DRIFT_REPAIR_ARBITRATION_3S_HGB_FAST_V0','researchOnly':True,'promotion':'LEARNABILITY_PROBE_ONLY','dataset':{'rows':len(d),'markets':int(d.market_id.nunique()),'rate':float(d.label_repair_next3s.mean()),'source':str(ROWS)},'splitMarkets':{k:len(v) for k,v in splits.items()},'chronology':{'trainMaxEndMs':int(parts['train'].market_end_ms.max()),'validationMaxEndMs':int(parts['validation'].market_end_ms.max()),'testMaxEndMs':int(parts['test'].market_end_ms.max()),'reservedFinalClosedLoopStartMs':1787137800000},'results':results,'artifact':str(ART),'decisionRule':'Proceed only if repair-specific model shows consistent validation AND test lift over frozen general 3s hazard; no PnL/forward-loss threshold selection.','guards':['No winner/PnL feature or label.','Overlap-excursion +15s states excluded upstream.','Final start75-99 untouched.','8784 R1 frozen.']}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
