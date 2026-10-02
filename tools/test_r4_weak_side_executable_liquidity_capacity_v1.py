from __future__ import annotations
import json, math, sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hft_native_queue_reactive_chronology_v2 as qv2

BASE=ROOT/'data'/'research'/'execution_aware_fill_lifecycle_v0'
OUT=ROOT/'data'/'research'/'r4_v0'/'hourly'
TZ=ZoneInfo('Asia/Taipei')
VERSION='R4_WEAK_SIDE_EXECUTABLE_LIQUIDITY_CAPACITY_V1'

GEOM=[
 'action_price','current_spread_ticks','combined_gross','combined_net','combined_abs_net',
 'combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl',
 'abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms',
 'maker_fills_1s','maker_fills_5s','maker_shares_5s','seconds_left'
]
LIQ=[
 'queue_ahead_shares','queue_ahead_chunks','distance_from_same_best_ticks','same_top_shares',
 'opposite_top_shares','same_depth_3ticks_shares','same_book_levels','top_imbalance_toward_action',
 'contra_trade_qty_1s','contra_trade_qty_3s','contra_trade_through_qty_1s','contra_trade_through_qty_3s',
 'depth_events_1s','depth_events_3s','action_add_qty_1s','action_remove_qty_1s',
 'action_add_qty_3s','action_remove_qty_3s','same_near_add_qty_3s','same_near_remove_qty_3s',
 'queue_depletion_pressure_3s','queue_clearance_proxy_s'
]

def mat(df, cols):
 return df[cols].replace([np.inf,-np.inf],np.nan)

def weak_only(df):
 z=df.copy()
 # combined_net >0 means UP surplus, hence DOWN is weak side; vice versa.
 weak=((z.combined_net>1e-9)&(z.side=='DOWN'))|((z.combined_net<-1e-9)&(z.side=='UP'))
 z=z[weak & (z.combined_abs_net>=qv2.QTY)].copy()
 z['recovery_usdt']=np.maximum(z['delta_floor_realized'].astype(float),0.0)
 z['recovered']= (z['recovery_usdt']>1e-9).astype(int)
 return z

def fit_eval(train, test, cols):
 X=mat(train,cols); Xt=mat(test,cols)
 clf=HistGradientBoostingClassifier(max_iter=120,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=2.,random_state=20260827)
 reg=HistGradientBoostingRegressor(max_iter=120,learning_rate=.05,max_leaf_nodes=15,min_samples_leaf=20,l2_regularization=2.,random_state=20260827)
 clf.fit(X,train.recovered)
 reg.fit(X,train.recovery_usdt)
 p=clf.predict_proba(Xt)[:,1]
 pred=np.maximum(reg.predict(Xt),0.0)
 auc=float(roc_auc_score(test.recovered,p)) if test.recovered.nunique()>1 else None
 rho=float(spearmanr(test.recovery_usdt,pred).statistic) if len(test)>=3 else None
 return {'rows':int(len(test)),'markets':int(test.market_id.nunique()),'positiveRate':float(test.recovered.mean()),'auc':auc,'recoverySpearman':rho,'meanActualRecovery':float(test.recovery_usdt.mean()),'medianActualRecovery':float(test.recovery_usdt.median())}

def main():
 contract=json.loads((BASE/'hft_native_unused_chronology_v2_preregistered.json').read_text(encoding='utf-8'))
 # Bounded retry after full-120 extraction exceeded the process window: use chronological 40/10/10 markets.
 chosen={'train':contract['train'][:40],'validation':contract['validation'][:10],'holdout':contract['holdout'][-10:]}
 ids={k:{int(r['marketId']) for r in chosen[k]} for k in chosen}
 all_ids=set().union(*ids.values())
 collector=json.loads((BASE/'hft_native_unused120_collector_v2.json').read_text(encoding='utf-8'))
 sweep=json.loads((BASE/'hft_native_unused120_bothsides_v2.json').read_text(encoding='utf-8'))
 collector['placementRows']=[r for r in (collector.get('placementRows') or []) if int(r['market_id']) in all_ids]
 sweep['rows']=[r for r in (sweep.get('rows') or []) if int(r['marketId']) in all_ids]
 ctmp=BASE/'_r4_weakliq_collector60_tmp.json'; stmp=BASE/'_r4_weakliq_sweep60_tmp.json'
 ctmp.write_text(json.dumps(collector),encoding='utf-8'); stmp.write_text(json.dumps(sweep),encoding='utf-8')
 try:
  rows=qv2.load_rows(ctmp.name,stmp.name)
 finally:
  ctmp.unlink(missing_ok=True); stmp.unlink(missing_ok=True)
 frames={k:weak_only(rows[rows.market_id.isin(v)].copy()) for k,v in ids.items()}
 tr=frames['train']; va=frames['validation']; ho=frames['holdout']
 geom_val=fit_eval(tr,va,GEOM); full_val=fit_eval(tr,va,GEOM+LIQ)
 geom_hold=fit_eval(pd.concat([tr,va],ignore_index=True),ho,GEOM)
 full_hold=fit_eval(pd.concat([tr,va],ignore_index=True),ho,GEOM+LIQ)
 auc_lift=(full_hold['auc']-geom_hold['auc']) if full_hold['auc'] is not None and geom_hold['auc'] is not None else None
 rho_lift=(full_hold['recoverySpearman']-geom_hold['recoverySpearman']) if full_hold['recoverySpearman'] is not None and geom_hold['recoverySpearman'] is not None else None
 gate={'minHoldoutRows':80,'aucLiftRequired':0.03,'spearmanLiftRequired':0.03}
 pass_gate=bool(full_hold['rows']>=80 and auc_lift is not None and rho_lift is not None and auc_lift>=.03 and rho_lift>=.03)
 rep={
  'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'status':'KEEP_SIGNAL' if pass_gate else 'REJECTED',
  'candidate':'Predict 5s realized weak-side floor-recovery capacity directly from strict-past queue/near-touch executable-liquidity state, and require incremental discrimination over economic geometry alone. Predictor only; no control action.',
  'data':{'source':'Predict Execution Tape V1 + HftBacktest preregistered unused120 chronology','dreamFill':False,'trainMarkets':len(ids['train']),'validationMarkets':len(ids['validation']),'holdoutMarkets':len(ids['holdout']),'sealed20260816':True,'echtgeldTraining':False},
  'features':{'geometry':GEOM,'executableLiquidity':LIQ},
  'validation':{'geometryOnly':geom_val,'geometryPlusLiquidity':full_val},
  'holdout':{'geometryOnly':geom_hold,'geometryPlusLiquidity':full_hold,'aucLift':auc_lift,'recoverySpearmanLift':rho_lift},
  'gate':{**gate,'pass':pass_gate},
  'guards':{'strictPastRuntimeFeatures':True,'futureFillUsedAsOfflineLabelOnly':True,'winnerExcluded':True,'noThresholdSweep':True,'noEchtgeldTrainingOrTuning':True,'r4ResearchOnly':True},
  'conclusion':('KEEP_SIGNAL: executable-liquidity state adds preregistered incremental value over geometry for weak-side realized recovery.' if pass_gate else 'REJECTED: strict-past executable-liquidity state does not add enough stable incremental predictive value over geometry under the fixed gate; do not threshold-sweep or couple to control.'),
  'nextDistinct':('Test a bounded control overlay that spends reserve only against predicted weak-side executable recovery, without changing the frozen floor=0 tranche.' if pass_gate else 'Shift from static book-capacity snapshots to queue-position lifecycle / pre-positioned resting-order occupancy: test whether being already in queue before weak-side need arises explains Target durable build and real-venue preservation better than instantaneous executable depth.')
 }
 out=OUT/f"r4_weak_side_executable_liquidity_capacity_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json"
 out.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=False),encoding='utf-8')
 print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'status':rep['status'],'validation':rep['validation'],'holdout':rep['holdout'],'gate':rep['gate']},ensure_ascii=False))

if __name__=='__main__': main()
