from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np,pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier,HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score
ROOT=Path(__file__).resolve().parents[1]; TZ=ZoneInfo('Asia/Taipei')
CACHE=ROOT/'data/research/r4_v0/r4_weakliq_cache_v1.csv'
CON=ROOT/'data/research/execution_aware_fill_lifecycle_v0/hft_native_unused_chronology_v2_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly'
GEOM=['action_price','current_spread_ticks','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_shares_5s','seconds_left']
LIQ=['queue_ahead_shares','queue_ahead_chunks','distance_from_same_best_ticks','same_top_shares','opposite_top_shares','same_depth_3ticks_shares','same_book_levels','top_imbalance_toward_action','contra_trade_qty_1s','contra_trade_qty_3s','contra_trade_through_qty_1s','contra_trade_through_qty_3s','depth_events_1s','depth_events_3s','action_add_qty_1s','action_remove_qty_1s','action_add_qty_3s','action_remove_qty_3s','same_near_add_qty_3s','same_near_remove_qty_3s','queue_depletion_pressure_3s','queue_clearance_proxy_s']
def weak(df):
 z=df.copy(); m=((z.combined_net>1e-9)&(z.side=='DOWN'))|((z.combined_net<-1e-9)&(z.side=='UP')); z=z[m & (z.combined_abs_net>=5)].copy(); z['recovery_usdt']=np.maximum(z.delta_floor_realized.astype(float),0); z['recovered']=(z.recovery_usdt>1e-9).astype(int); return z
def ev(tr,te,cols):
 X=tr[cols].replace([np.inf,-np.inf],np.nan); Xt=te[cols].replace([np.inf,-np.inf],np.nan)
 c=HistGradientBoostingClassifier(max_iter=120,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=2.,random_state=20260827).fit(X,tr.recovered)
 r=HistGradientBoostingRegressor(max_iter=120,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=2.,random_state=20260827).fit(X,tr.recovery_usdt)
 p=c.predict_proba(Xt)[:,1]; q=np.maximum(r.predict(Xt),0)
 return {'rows':len(te),'markets':te.market_id.nunique(),'positiveRate':float(te.recovered.mean()),'auc':float(roc_auc_score(te.recovered,p)) if te.recovered.nunique()>1 else None,'recoverySpearman':float(spearmanr(te.recovery_usdt,q).statistic) if len(te)>=3 else None,'meanActualRecovery':float(te.recovery_usdt.mean()),'medianActualRecovery':float(te.recovery_usdt.median())}
def main():
 d=weak(pd.read_csv(CACHE)); con=json.loads(CON.read_text(encoding='utf-8')); ids={k:{int(x['marketId']) for x in con[k]} for k in ['train','validation','holdout']}; tr=d[d.market_id.isin(ids['train'])]; va=d[d.market_id.isin(ids['validation'])]; ho=d[d.market_id.isin(ids['holdout'])]
 gv=ev(tr,va,GEOM); fv=ev(tr,va,GEOM+LIQ); gh=ev(pd.concat([tr,va]),ho,GEOM); fh=ev(pd.concat([tr,va]),ho,GEOM+LIQ)
 al=fh['auc']-gh['auc'] if fh['auc'] is not None and gh['auc'] is not None else None; rl=fh['recoverySpearman']-gh['recoverySpearman'] if fh['recoverySpearman'] is not None and gh['recoverySpearman'] is not None else None
 gate={'minHoldoutRows':80,'aucLiftRequired':.03,'spearmanLiftRequired':.03}; ok=bool(fh['rows']>=80 and al is not None and rl is not None and al>=.03 and rl>=.03)
 # Chronology consistency safeguard: validation should not have both incremental metrics negative if holdout passes.
 stable=not ((fv['auc'] is not None and gv['auc'] is not None and fv['auc']<gv['auc']) and (fv['recoverySpearman'] is not None and gv['recoverySpearman'] is not None and fv['recoverySpearman']<gv['recoverySpearman']))
 status='KEEP_SIGNAL' if ok and stable else 'REJECTED' if fh['rows']>=80 else 'INCONCLUSIVE'
 rep={'version':'R4_WEAK_SIDE_EXECUTABLE_LIQUIDITY_CAPACITY_FULL120_CACHED_REPLICATION_V2','createdAt':datetime.now(TZ).isoformat(),'status':status,'candidate':'Exact cached full-120 replication of instantaneous weak-side executable-liquidity incremental predictor; same features/model/gates, predictor only.','data':{'cachedActionRows':int(len(pd.read_csv(CACHE))),'weakRows':int(len(d)),'trainMarkets':len(ids['train']),'validationMarkets':len(ids['validation']),'holdoutMarkets':len(ids['holdout']),'sealed20260816':True,'echtgeldTraining':False},'validation':{'geometryOnly':gv,'geometryPlusLiquidity':fv,'aucLift':None if gv['auc'] is None or fv['auc'] is None else fv['auc']-gv['auc'],'recoverySpearmanLift':None if gv['recoverySpearman'] is None or fv['recoverySpearman'] is None else fv['recoverySpearman']-gv['recoverySpearman']},'holdout':{'geometryOnly':gh,'geometryPlusLiquidity':fh,'aucLift':al,'recoverySpearmanLift':rl},'gate':{**gate,'numericPass':ok,'chronologyConsistencyPass':stable,'pass':ok and stable},'guards':{'strictPastRuntimeFeatures':True,'futureFillOfflineLabelOnly':True,'winnerExcluded':True,'noThresholdSweep':True,'noEchtgeldTrainingOrTuning':True,'r4ResearchOnly':True},'conclusion':('KEEP_SIGNAL: instantaneous executable-liquidity adds stable incremental value over geometry on full preregistered chronology.' if status=='KEEP_SIGNAL' else 'REJECTED: with sufficient cached full-120 support, instantaneous executable-liquidity does not add chronology-stable incremental value over geometry; do not tune this family.' if status=='REJECTED' else 'INCONCLUSIVE: holdout support still below fixed minimum.'),'nextDistinct':('Test a bounded R4 control overlay only after separate execution-stress confirmation.' if status=='KEEP_SIGNAL' else 'Move to pre-positioned resting-order occupancy / queue-position lifecycle: test whether already-held queue age/position before weak-side need predicts durable base recovery better than instantaneous depth.' if status=='REJECTED' else 'Expand support without semantic changes.')}
 out=OUT/f"r4_weakliq_full120_cached_replication_v2_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json"; out.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'status':status,'validation':rep['validation'],'holdout':rep['holdout'],'gate':rep['gate']},ensure_ascii=False))
if __name__=='__main__': main()
