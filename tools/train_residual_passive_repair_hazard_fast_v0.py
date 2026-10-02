from __future__ import annotations
import bisect, json, sqlite3
from pathlib import Path
import joblib, numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss, brier_score_loss

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
ROWS_IN=OUT/'residual_drift_repair_arbitration_3s_states_v0.csv'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
UP_ART=OUT/'target_general_maker_up_core_book_v1.joblib'; DOWN_ART=OUT/'target_general_maker_down_core_book_v1.joblib'
ROWS=OUT/'residual_passive_repair_1s_states_v0.csv'; ART=OUT/'residual_passive_repair_1s_hgb_fast_v0.joblib'; REPORT=OUT/'residual_passive_repair_1s_hgb_fast_v0_report.json'
WALLET='0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03'; H=1000
FEATURES=[
 'seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign','last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s','maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge','taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge','up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth','pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge','last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak','maker_coverage_gap','combined_coverage_gap','floor_per_maker_gross','floor_per_combined_gross','absnet_per_maker_gross','maker_absnet_velocity_10s_per_gross','recent_maker_share_rate_10s']

def metric(y,p):
 y=np.asarray(y,int); p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':len(y),'positives':int(y.sum()),'rate':float(y.mean()),'predMean':float(p.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p))}

def mprob(art,x): return art['model'].predict_proba(x[list(art['features'])].apply(pd.to_numeric,errors='coerce'))[:,1]

def build_labels(d):
 mids=sorted(set(d.market_id.astype(int))); q=','.join('?'*len(mids)); c=sqlite3.connect(f'file:{TARGET_DB.resolve().as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row
 ev=list(c.execute(f"select market_id,event_ms,side,shares from wallet_shadow_target_events where lower(wallet)=lower(?) and asset='BTC' and role='MAKER' and quote_type='BID' and market_id in ({q}) order by market_id,event_ms",[WALLET,*mids])); c.close()
 idx={}
 for mid in mids:
  rr=[r for r in ev if int(r['market_id'])==mid]; ts=[]; up=[]; dn=[]; u=dnv=0.0
  for r in rr:
   t=int(r['event_ms']); side=str(r['side']); sh=float(r['shares']); u+=sh if side=='UP' else 0; dnv+=sh if side=='DOWN' else 0; ts.append(t); up.append(u); dn.append(dnv)
  idx[mid]=(ts,up,dn)
 labels=[]; oppshares=[]; deltaabs=[]
 for r in d[['market_id','checkpoint_ms','maker_net']].itertuples(index=False):
  mid=int(r.market_id); t=int(r.checkpoint_ms); net=float(r.maker_net); ts,up,dn=idx.get(mid,([],[],[])); i=bisect.bisect_right(ts,t)-1; j=bisect.bisect_right(ts,t+H)-1
  u0=up[i] if i>=0 else 0.; d0=dn[i] if i>=0 else 0.; u1=up[j] if j>=0 else 0.; d1=dn[j] if j>=0 else 0.; fu=u1-u0; fd=d1-d0
  future=net+fu-fd; opp=fd if net>0 else fu; da=abs(net)-abs(future)
  labels.append(int(opp>1e-9 and da>1.0)); oppshares.append(opp); deltaabs.append(da)
 d=d.copy(); d['label_effective_passive_repair_next1s']=labels; d['future_minority_maker_shares_1s']=oppshares; d['future_absnet_reduction_1s']=deltaabs; return d

def main():
 d=pd.read_csv(ROWS_IN)
 if 'label_effective_passive_repair_next1s' not in d: d=build_labels(d); d.to_csv(ROWS,index=False)
 else: d.to_csv(ROWS,index=False)
 d=d.sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True); ms=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']); ids=ms.market_id.astype(int).tolist(); a=int(len(ids)*.70); b=int(len(ids)*.85); sp={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}; parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}
 model=HistGradientBoostingClassifier(learning_rate=.05,max_iter=220,max_leaf_nodes=15,min_samples_leaf=60,l2_regularization=1.,early_stopping=True,validation_fraction=.1,n_iter_no_change=25,random_state=20260820); model.fit(parts['train'][FEATURES].apply(pd.to_numeric,errors='coerce'),parts['train'].label_effective_passive_repair_next1s.astype(int))
 up=joblib.load(UP_ART); down=joblib.load(DOWN_ART); results={}
 for n,x in parts.items():
  y=x.label_effective_passive_repair_next1s.astype(int).to_numpy(); p=model.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1]; q=np.empty(len(x),float); pos=pd.to_numeric(x.maker_net,errors='coerce').to_numpy()>0
  if pos.any(): q[pos]=mprob(down,x.iloc[np.where(pos)[0]])
  if (~pos).any(): q[~pos]=mprob(up,x.iloc[np.where(~pos)[0]])
  a1=metric(y,p); b1=metric(y,q); results[n]={'residualPassiveRepairFast':a1,'oppositeGeneralMakerPressure':b1,'lift':{'auc':a1['auc']-b1['auc'],'ap':a1['ap']-b1['ap'],'logLoss':a1['logLoss']-b1['logLoss'],'brier':a1['brier']-b1['brier']}}
 joblib.dump({'version':'RESIDUAL_PASSIVE_REPAIR_1S_HGB_FAST_V0','model':model,'features':FEATURES,'trainingMarkets':sorted(sp['train']),'trainingMaxEndMs':int(parts['train'].market_end_ms.max()),'semantics':'next1s effective minority-side Target Maker fill reducing Maker abs-net by >1 share, outside realized-overlap +15s windows; research probe'},ART)
 rep={'reportVersion':'RESIDUAL_PASSIVE_REPAIR_1S_HGB_FAST_V0','researchOnly':True,'promotion':'LEARNABILITY_PROBE_ONLY','dataset':{'rows':len(d),'markets':int(d.market_id.nunique()),'positiveRate':float(d.label_effective_passive_repair_next1s.mean()),'label':'within next1s, Target Maker minority-side fills and resulting Maker abs-net decreases by >1 share; overlap-excursion +15s states already excluded','rowsFile':str(ROWS)},'splitMarkets':{k:len(v) for k,v in sp.items()},'results':results,'artifact':str(ART),'decisionRule':'Only proceed if validation/test show stable lift over opposite-side general Maker CORE+BOOK pressure. No forward PnL threshold selection.','guards':['No winner/PnL features or labels.','Future Target Maker fills are labels only.','Final start75-99 untouched.','8784 R1 frozen.']}; REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
