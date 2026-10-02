from __future__ import annotations
import json, math, warnings
from pathlib import Path
from collections import defaultdict
import numpy as np, pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier

ROOT=Path(__file__).resolve().parents[1];D=ROOT/'data/research/execution_aware_fill_lifecycle_v0';SRC=D/'r2_fill_quality_explicit_v1_dataset.csv';OUT=D/'r2_selective_fill_risk_oos_v0_report.json';SEED=20260822
F=['side_is_up','order_age_ms','quote_price','status_none','status_new','status_partial','cum_exec_qty','remaining_qty','remaining_ratio','partial_fill_ratio','active_same_count','active_opp_count','quote_offset_ticks','current_bid','current_ask','current_spread_ticks','initial_depth','public_cum_depletion','public_depletion_ratio','public_any_depletion','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s']

def finite(v):
 try:x=float(v);return x if math.isfinite(x) else math.nan
 except:return math.nan

def feat(s):
 p=s.get('portfolio') if isinstance(s.get('portfolio'),dict) else {};cum=finite(s.get('cumExecQty'));rem=finite(s.get('remainingQty'));tot=(0 if math.isnan(cum) else cum)+(0 if math.isnan(rem) else rem);init=finite(s.get('initialDepth'));dep=finite(s.get('publicCumDepletion'));st=str(s.get('hftStatus') or 'NONE')
 v={'side_is_up':float(str(s.get('side')).upper()=='UP'),'order_age_ms':finite(s.get('orderAgeMs')),'quote_price':finite(s.get('price')),'status_none':float(st=='NONE'),'status_new':float(st=='NEW'),'status_partial':float(st=='PARTIALLY_FILLED'),'cum_exec_qty':cum,'remaining_qty':rem,'remaining_ratio':rem/tot if tot>1e-9 and not math.isnan(rem) else math.nan,'partial_fill_ratio':finite(s.get('partialFillRatio')),'active_same_count':finite(s.get('activeSameCount')),'active_opp_count':finite(s.get('activeOppCount')),'quote_offset_ticks':finite(s.get('quoteOffsetTicks')),'current_bid':finite(s.get('currentBid')),'current_ask':finite(s.get('currentAsk')),'current_spread_ticks':finite(s.get('currentSpreadTicks')),'initial_depth':init,'public_cum_depletion':dep,'public_depletion_ratio':dep/init if init>1e-9 and not math.isnan(dep) else math.nan,'public_any_depletion':float(bool(s.get('publicAnyDepletion')))}
 for k in F:
  if k not in v:v[k]=finite(p.get(k))
 return v

def reports():
 for p in sorted(D.glob('r2_execution_school_market*_mid_risk_v0.json')):
  try:
   r=json.loads(p.read_text(encoding='utf-8'))
   if r.get('version')=='HFTBACKTEST_R2_EXECUTION_SCHOOL_V0':yield r
  except:pass

def main():
 warnings.filterwarnings('ignore');d=pd.read_csv(SRC);mids=d.groupby('market_id')['fill_ms'].max().sort_values().index.astype(int).tolist();a=int(len(mids)*.70);b=int(len(mids)*.85);train=set(mids[:a]);evalm=set(mids[a:]);mods={}
 for h in [1,3]:
  lab=f'label_markout{h}s_ticks';tr=d[d.market_id.astype(int).isin(train)&d[lab].notna()].copy();tr['y']=(tr[lab]<0).astype(int);m=ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=800,early_stopping_rounds=60,min_samples_leaf=12,n_jobs=-2,random_state=SEED+h);m.fit(tr[F],tr.y);mods[h]=m
 allrows=[]
 for r in reports():
  mid=int(r['marketId']);
  if mid not in evalm:continue
  filled={str(x['orderId']) for x in r.get('makerFillEvents',[]) if float(x.get('deltaShares') or 0)>0};states=defaultdict(list)
  for s in r.get('orderStateRows',[]):states[str(s.get('orderId'))].append(s)
  for oid,z in states.items():
   z=sorted(z,key=lambda x:int(x['checkpointMs']));s=z[0];x=feat(s);row={'market_id':mid,'order_id':oid,'actual_filled':int(oid in filled),**x};X=pd.DataFrame([x],columns=F)
   for h,m in mods.items():row[f'p_adverse_{h}s']=float(m.predict_proba(X)[0,1])
   allrows.append(row)
 q=pd.DataFrame(allrows);rng=np.random.default_rng(SEED);seeds=[]
 actual={h:float(q[q.actual_filled==1][f'p_adverse_{h}s'].mean()) for h in [1,3]};non={h:float(q[q.actual_filled==0][f'p_adverse_{h}s'].mean()) for h in [1,3]}
 for seed in range(1000):
  rr=np.random.default_rng(SEED+seed);sel=[]
  for mid,g in q.groupby('market_id'):
   n=int(g.actual_filled.sum());
   if n<=0:continue
   idx=rr.choice(g.index.to_numpy(),size=min(n,len(g)),replace=False);sel.extend(idx.tolist())
  z=q.loc[sel];seeds.append({h:float(z[f'p_adverse_{h}s'].mean()) for h in [1,3]})
 rep={'version':'R2_SELECTIVE_FILL_RISK_OOS_V0','researchOnly':True,'evalMarkets':len(evalm),'orders':len(q),'actualFilledOrders':int(q.actual_filled.sum()),'actualFilledMeanRisk':actual,'unfilledMeanRisk':non,'randomMatchedMeanRisk':{h:float(np.mean([x[h] for x in seeds])) for h in [1,3]},'shareRandomSeedsLowerRiskThanActual':{h:float(np.mean([x[h]<actual[h] for x in seeds])) for h in [1,3]},'guardrails':['Risk models trained only on strict-past 70% markets; evaluated on later 30% markets.','Random comparison matches actual filled-order count within each market.','No winner/PnL/Target runtime input.','No dream fill.','No threshold sweep.']}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False))
if __name__=='__main__':main()
