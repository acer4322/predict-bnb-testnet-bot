from __future__ import annotations
import json,math
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
DATA=OUT/'target_maker_quote_offset_v1_states.csv'
AGG_ART=OUT/'target_maker_quote_aggressive_v1.joblib'; DEEP_ART=OUT/'target_maker_quote_deep_conditional_v1.joblib'; REPORT=OUT/'target_maker_quote_zone_v1_report.json'
SEED=20260820
CORE=['seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign']
BOOK=['up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge']
LIFE=['last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s']
ECON=['maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge','taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge']
PLACE=['last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak']
SIDE=['side_is_up','side_maker_net','side_combined_net','chosen_bid','chosen_ask','chosen_spread_ticks','opposite_bid_side','last_same_maker_age_ms','last_opp_maker_age_ms','same_placements_5s','opp_placements_5s','same_placements_10s','opp_placements_10s']
FEATURES=CORE+BOOK+LIFE+ECON+PLACE+SIDE

def ebm(seed):return ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=24,interactions=4,outer_bags=3,learning_rate=.04,max_rounds=1000,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def metric(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()),'predMean':float(p.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}
def top(m,n=15):
 im=list(m.term_importances());nm=list(m.term_names_);ix=sorted(range(len(im)),key=lambda i:float(im[i]),reverse=True)[:n];return [{'term':str(nm[i]),'importance':float(im[i])} for i in ix]
def main():
 d=pd.read_csv(DATA); d['label_aggressive']=(pd.to_numeric(d.offset_ticks,errors='coerce')<=0).astype(int); d['label_deep']=(pd.to_numeric(d.offset_ticks,errors='coerce')>=2).astype(int)
 mm=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=mm.market_id.astype(int).tolist();n=len(ids);a=int(n*.70);b=int(n*.85);sp={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}; result={}
 for task,label,conditional,art,seed in [('AGGRESSIVE_AT_OR_AHEAD','label_aggressive',False,AGG_ART,SEED+1),('DEEP_GTE2_GIVEN_NOT_AGGRESSIVE','label_deep',True,DEEP_ART,SEED+2)]:
  base=d[d.offset_ticks>0].copy() if conditional else d.copy();parts={k:base[base.market_id.astype(int).isin(v)].copy() for k,v in sp.items()};m=ebm(seed);m.fit(parts['train'][FEATURES].apply(pd.to_numeric,errors='coerce'),parts['train'][label].astype(int));joblib.dump({'version':'TARGET_MAKER_QUOTE_ZONE_V1','task':task,'features':FEATURES,'model':m,'trainingMarkets':sorted(sp['train']),'trainingMaxEndMs':int(parts['train'].market_end_ms.max()),'conditional':conditional},art);rr={'artifact':str(art),'splitMetrics':{},'topTerms':top(m)}
  for k,x in parts.items():rr['splitMetrics'][k]=metric(x[label],m.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1])
  # OPEN calibration on each chronological split
  rr['openCalibration']={}
  for k,x in parts.items():
   q=x[x.seconds_left>240];rr['openCalibration'][k]=metric(q[label],m.predict_proba(q[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]) if len(q) else None
  result[task]=rr;print(json.dumps({task:{'metrics':rr['splitMetrics'],'open':rr['openCalibration'],'top':rr['topTerms'][:8]}},indent=2),flush=True)
 rep={'reportVersion':'TARGET_MAKER_QUOTE_ZONE_V1','researchOnly':True,'runtimeTargetDataAllowed':False,'goal':'Ordinal quote controller distilled from strict-past Target placement states: first choose aggressive at/inside-vs-behind; conditional on behind choose 1tick-vs-deep>=2ticks.','dataset':{'rows':len(d),'markets':int(d.market_id.nunique()),'marketEndMaxMs':int(d.market_end_ms.max()),'aggressiveRate':float(d.label_aggressive.mean()),'deepConditionalRate':float(d[d.offset_ticks>0].label_deep.mean())},'splitMarkets':{k:len(v) for k,v in sp.items()},'results':result,'runtimeMapping':{'aggressive':'quote at current best bid; never cross/inside in V1','nonAggressiveNotDeep':'1 tick behind','deep':'2 ticks behind in V1; 4+ exact depth intentionally not modeled yet'},'references':{'oldPublicOnlyAtOrAheadMeanAucApprox':0.6294,'oldPublicOnlyNear2MeanAucApprox':0.6551},'guards':['Dataset was built by strictly matching checkpoint before placement_first_ms.','Target placement/price is label only; no future/winner/PnL input.','All source markets end before final start75 closed-loop holdout begins.','No hyperparameter sweep; binary model capacity reduced only to make ordinal controller tractable.','Promotion requires chronological validation/test signal and reasonable probability calibration, not PnL.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'report':str(REPORT)},indent=2))
if __name__=='__main__':main()
