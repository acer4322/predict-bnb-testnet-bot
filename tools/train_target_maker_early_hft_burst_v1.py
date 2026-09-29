from __future__ import annotations
import bisect,json,math,sqlite3
from pathlib import Path
import joblib,numpy as np,pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,brier_score_loss
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
DB=ROOT/'data'/'wallet_maker_book_inference.db'
TRAIN=[OUT/f'target_blind_promoted_controller_closed_loop_v2_hft_early_side_redistribute_v1_growv21_train{x}_states.csv' for x in ['00_09','10_19','20_29']]
VAL=[OUT/'target_blind_promoted_controller_closed_loop_v2_hft_early_side_redistribute_v1_growv21_val30_39_states.csv']
UP_ART=OUT/'target_maker_early_hft_burst_up_v1.joblib';DN_ART=OUT/'target_maker_early_hft_burst_down_v1.joblib';REPORT=OUT/'target_maker_early_hft_burst_v1_report.json';DATA=OUT/'target_maker_early_hft_burst_v1_states.csv'
SEED=20260820
CORE=['seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign']
BOOK=['up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge']
LIFE=['last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s']
ECON=['maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge','taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge']
PLACE=['last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak']
BASE=CORE+BOOK+LIFE+ECON+PLACE+['pMakerUpBase','pMakerDownBase','pMakerUp','pMakerDown','pTaker1s','pTaker3s']
def metrics(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);both=len(set(y.tolist()))>1
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}
def ebm(fs,seed):return ExplainableBoostingClassifier(feature_names=fs,max_bins=96,max_interaction_bins=32,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=1600,early_stopping_rounds=80,min_samples_leaf=12,n_jobs=-2,random_state=seed)
def top(m,n=15):
 imp=list(m.term_importances());names=list(m.term_names_);ix=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:n];return [{'term':str(names[i]),'importance':float(imp[i])} for i in ix]
def teachers():
 c=sqlite3.connect(DB);c.row_factory=sqlite3.Row;emap={int(r['window_end_ms']):int(r['market_id']) for r in c.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')};out={}
 for mid in set(emap.values()):
  u=[];d=[]
  for r in c.execute('''select target_side,placement_first_ms from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 and placement_first_ms is not null order by placement_first_ms''',(mid,)):
   (u if str(r['target_side'])=='UP' else d).append(int(r['placement_first_ms']))
  if u or d:out[mid]=(u,d)
 c.close();return emap,out
def label(files,split,emap,times):
 rows=[]
 for p in files:
  df=pd.read_csv(p)
  for _,r in df.iterrows():
   mid=emap.get(int(r.windowEndMs));z=times.get(mid)
   if z is None:continue
   cp=int(r.atMs);row=r.to_dict();row['split']=split;row['targetMarketId']=mid
   for side,a in [('UP',z[0]),('DOWN',z[1])]:
    n=bisect.bisect_right(a,cp+1000)-bisect.bisect_right(a,cp);row[f'count_{side.lower()}']=int(n);row[f'event_{side.lower()}']=int(n>0);row[f'multi_{side.lower()}']=int(n>=2)
   rows.append(row)
 return rows
def main():
 emap,times=teachers();d=pd.DataFrame(label(TRAIN,'train',emap,times)+label(VAL,'validation',emap,times)).sort_values(['windowEndMs','atMs','marketId']).reset_index(drop=True);d.to_csv(DATA,index=False);res={}
 for i,side in enumerate(('UP','DOWN')):
  lo=side.lower();q=d[d[f'event_{lo}']==1].copy();tr=q[q.split=='train'];va=q[q.split=='validation'];haz='pMakerUp' if side=='UP' else 'pMakerDown';fs=['hazard_p']+BASE
  tr=tr.copy();va=va.copy();tr['hazard_p']=pd.to_numeric(tr[haz],errors='coerce');va['hazard_p']=pd.to_numeric(va[haz],errors='coerce');m=ebm(fs,SEED+i);m.fit(tr[fs].apply(pd.to_numeric,errors='coerce'),tr[f'multi_{lo}'].astype(int));art=UP_ART if side=='UP' else DN_ART;joblib.dump({'version':'TARGET_MAKER_EARLY_HFT_BURST_V1','side':side,'features':fs,'model':m,'trainingMaxEndMs':int(tr.windowEndMs.max())},art)
  bp=va.hazard_p.to_numpy(float);pp=m.predict_proba(va[fs].apply(pd.to_numeric,errors='coerce'))[:,1];rr={'trainMarkets':int(tr.marketId.nunique()),'validationMarkets':int(va.marketId.nunique()),'trainRows':len(tr),'validationRows':len(va),'multiRateTrain':float(tr[f'multi_{lo}'].mean()),'multiRateValidation':float(va[f'multi_{lo}'].mean()),'validation':{'hazardOnlyRanking':metrics(va[f'multi_{lo}'],bp),'burstEbm':metrics(va[f'multi_{lo}'],pp)},'topTerms':top(m),'artifact':str(art)};a=rr['validation']['hazardOnlyRanking']['auc'];b=rr['validation']['burstEbm']['auc'];rr['validation']['deltaAuc']=None if a is None or b is None else b-a;res[side]=rr;print(json.dumps({side:rr},ensure_ascii=False,indent=2),flush=True)
 rep={'reportVersion':'TARGET_MAKER_EARLY_HFT_BURST_V1','researchOnly':True,'trajectoryPolicy':'early-HFT V2.1 side redistribution + HftBacktest actual fills','training':'growth manifest 0-29','validation':'growth manifest 30-39','promotionHoldout':'growth manifest 40-50 untouched','question':'Conditional on at least one Target same-side Maker placement in next 1s, identify multi-placement burst seconds from strict-past V2.1 HFT own/public state.','results':res,'guards':['No winner/PnL input.','Future Target placements are labels only.','No threshold sweep.','Do not promote on teacher metrics alone; require HFT closed-loop holdout lift.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
