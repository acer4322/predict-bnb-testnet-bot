from __future__ import annotations
import json,math,sqlite3
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1';DB=ROOT/'data'/'wallet_maker_book_inference.db'
TRAIN=[OUT/f'target_blind_promoted_controller_closed_loop_v2_hft_early_side_redistribute_v1_growv21_train{x}_states.csv' for x in ['00_09','10_19','20_29']]
VAL=[OUT/'target_blind_promoted_controller_closed_loop_v2_hft_early_side_redistribute_v1_growv21_val30_39_states.csv']
DATA=OUT/'target_maker_early_hft_quote_zone_v1_states.csv';AGG=OUT/'target_maker_early_hft_quote_aggressive_v1.joblib';DEEP=OUT/'target_maker_early_hft_quote_deep_v1.joblib';REPORT=OUT/'target_maker_early_hft_quote_zone_v1_report.json';SEED=20260820;MAX_AGE=1500
CORE=['seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign']
BOOK=['up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge']
LIFE=['last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s']
ECON=['maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge','taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge']
PLACE=['last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak']
SIDE=['side_is_up','side_maker_net','side_combined_net','chosen_bid','chosen_ask','chosen_spread_ticks','opposite_bid_side','last_same_maker_age_ms','last_opp_maker_age_ms','same_placements_5s','opp_placements_5s','same_placements_10s','opp_placements_10s']
FEATURES=CORE+BOOK+LIFE+ECON+PLACE+SIDE+['pMakerUpBase','pMakerDownBase','pMakerUp','pMakerDown']
def metric(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);both=len(set(y.tolist()))>1
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()) if len(y) else None,'predMean':float(p.mean()) if len(p) else None,'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}
def ebm(fs,seed):return ExplainableBoostingClassifier(feature_names=fs,max_bins=64,max_interaction_bins=24,interactions=4,outer_bags=3,learning_rate=.04,max_rounds=1000,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def sidefeat(r,side):
 up=side=='UP';return {'side_is_up':float(up),'side_maker_net':float(r.maker_net)*(1 if up else -1),'side_combined_net':float(r.combined_net)*(1 if up else -1),'chosen_bid':float(r.up_bid if up else r.down_bid),'chosen_ask':float(r.up_ask if up else r.down_ask),'chosen_spread_ticks':float(r.up_spread_ticks if up else r.down_spread_ticks),'opposite_bid_side':float(r.down_bid if up else r.up_bid),'last_same_maker_age_ms':float(r.last_maker_up_age_ms if up else r.last_maker_down_age_ms),'last_opp_maker_age_ms':float(r.last_maker_down_age_ms if up else r.last_maker_up_age_ms),'same_placements_5s':float(r.up_placements_5s if up else r.down_placements_5s),'opp_placements_5s':float(r.down_placements_5s if up else r.up_placements_5s),'same_placements_10s':float(r.up_placements_10s if up else r.down_placements_10s),'opp_placements_10s':float(r.down_placements_10s if up else r.up_placements_10s)}
def build(files,split):
 s=pd.concat([pd.read_csv(p) for p in files],ignore_index=True).sort_values(['windowEndMs','atMs']);by={int(we):q.reset_index(drop=True) for we,q in s.groupby('windowEndMs')};c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;emap={int(r['window_end_ms']):int(r['market_id']) for r in c.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')};rows=[]
 for we,q in by.items():
  mid=emap.get(we)
  if mid is None:continue
  ts=q.atMs.astype('int64').to_numpy()
  ps=list(c.execute('''select parent_id,target_side,target_price,placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null order by placement_first_ms,parent_id''',(mid,)))
  for p in ps:
   t=int(p['placement_first_ms']);j=int(np.searchsorted(ts,t,side='left')-1)
   if j<0:continue
   age=t-int(ts[j]);
   if age<0 or age>MAX_AGE:continue
   r=q.iloc[j];side=str(p['target_side']);bid=float(r.up_bid if side=='UP' else r.down_bid);price=float(p['target_price'])
   if not(math.isfinite(bid) and math.isfinite(price)):continue
   z={k:r.get(k,math.nan) for k in CORE+BOOK+LIFE+ECON+PLACE+['pMakerUpBase','pMakerDownBase','pMakerUp','pMakerDown']};z.update(sidefeat(r,side));z.update({'split':split,'market_id':int(r.marketId),'target_market_id':mid,'market_end_ms':we,'checkpoint_ms':int(r.atMs),'placement_ms':t,'side':side,'target_price':price,'offset_ticks':int(round((bid-price)/.01)),'checkpoint_age_ms':age});rows.append(z)
 c.close();return rows
def main():
 d=pd.DataFrame(build(TRAIN,'train')+build(VAL,'validation')).sort_values(['market_end_ms','placement_ms']).reset_index(drop=True);d['label_aggressive']=(d.offset_ticks<=0).astype(int);d['label_deep']=(d.offset_ticks>=2).astype(int);d.to_csv(DATA,index=False);res={}
 for task,label,cond,art,seed in [('AGGRESSIVE','label_aggressive',False,AGG,SEED+1),('DEEP_GTE2_GIVEN_BEHIND','label_deep',True,DEEP,SEED+2)]:
  b=d[d.offset_ticks>0].copy() if cond else d.copy();tr=b[b.split=='train'];va=b[b.split=='validation'];m=ebm(FEATURES,seed);m.fit(tr[FEATURES].apply(pd.to_numeric,errors='coerce'),tr[label].astype(int));joblib.dump({'version':'TARGET_MAKER_EARLY_HFT_QUOTE_ZONE_V1','task':task,'features':FEATURES,'model':m,'trainingMaxEndMs':int(tr.market_end_ms.max()),'conditional':cond},art);pp=m.predict_proba(va[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1];res[task]={'trainMarkets':int(tr.market_id.nunique()),'validationMarkets':int(va.market_id.nunique()),'train':metric(tr[label],m.predict_proba(tr[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]),'validation':metric(va[label],pp),'artifact':str(art)};print(json.dumps({task:res[task]},indent=2),flush=True)
 rep={'reportVersion':'TARGET_MAKER_EARLY_HFT_QUOTE_ZONE_V1','trajectoryPolicy':'V2.1 side redistribution + HftBacktest actual fills','training':'growth manifest 0-29','validation':'growth manifest 30-39','promotionHoldout':'40-50 untouched','dataset':{'rows':len(d),'markets':int(d.market_id.nunique()),'aggressiveRate':float(d.label_aggressive.mean()),'deepConditionalRate':float(d[d.offset_ticks>0].label_deep.mean())},'results':res,'guards':['Target price is label only.','Each label uses latest strict-past V2.1 checkpoint <=1500ms.','No winner/PnL input.','No threshold sweep.','Do not promote without HFT closed-loop holdout lift.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
