from __future__ import annotations

"""Strict-forward family ablation for Phase-B execution microstructure.

No threshold/model tuning.  The goal is only to localize which causal
microstructure family contributes to branch-resolution lag and/or structural fill.
"""
import argparse,json,math,os
from pathlib import Path
from typing import Any
import duckdb
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier,ExtraTreesRegressor
from sklearn.metrics import roc_auc_score,mean_absolute_error

FOLDS=((0,40,40,60),(0,60,60,80),(0,80,80,100))
ROLES=("PROBE_CORE","ECONOMIC_CORE","SATELLITE_REPAIR","SATELLITE_EXPAND")
GEOM=("state_floor","state_best","state_abs_net","state_total_debt","state_initial_debt_qty","state_paid_debt_qty","state_remaining_debt_qty","state_repair_progress_frac","state_free_slots","action_price","action_qty","action_price_to_bid","action_ask_to_price","action_pair_legal","context_immediate_delta_floor","context_immediate_delta_best","context_immediate_delta_gap")
FULL_EXTRA=("state_seconds_left","state_coverage","state_gross","state_debt_up","state_debt_down","state_oldest_repair_progress","state_oldest_repair_age_ms","state_responsibility_count","state_live_slots","state_repair_family_live_slots","state_satellite_expand_live_slots","state_expand_family_live_slots","state_pending_cancel_count","state_book_imbalance","state_spread","state_dominant_mid","state_q_ladder_live","state_q_pending_active","context_side_bid","context_side_ask","context_side_mid","context_side_is_dominant","context_side_is_weak","context_target_debt_for_action_side","action_q_arm_used")
QUEUE_DEPTH=("micro_native_side_buy","micro_native_price","micro_queue_ahead_qty","micro_same_best_price","micro_same_best_depth","micro_opp_best_price","micro_opp_best_depth","micro_same_top3_depth","micro_same_top5_depth","micro_opp_top3_depth","micro_opp_top5_depth","micro_better_same_depth","micro_better_same_levels","micro_worse_within1tick_depth","micro_worse_within2tick_depth","micro_worse_within4tick_depth","micro_native_spread_ticks","micro_candidate_from_same_best_ticks","micro_book_levels_bid","micro_book_levels_ask","micro_current_order_count","micro_current_update_age_ms")
BOOK_CHURN=tuple([f"micro_u{n}_{x}" for n in (8,32) for x in ("same_abs_delta","same_negative_delta","same_positive_delta","opp_abs_delta","candidate_abs_delta","candidate_negative_delta","candidate_positive_delta","candidate_change_events","order_count_delta","span_ms")])
TRUE_TRADE=tuple([f"micro_t{n}_{x}" for n in (8,32) for x in ("trade_count","trade_qty","fill_aggressor_qty","fill_aggressor_share","at_or_through_candidate_qty","last_trade_age_ms","last_fill_aggressor_age_ms")])
FAMILIES={"QUEUE_DEPTH":QUEUE_DEPTH,"BOOK_CHURN":BOOK_CHURN,"TRUE_TRADE_FLOW":TRUE_TRADE,"ALL_MICRO":QUEUE_DEPTH+BOOK_CHURN+TRUE_TRADE}

def safe(x:Any,d=0.0):
 try:
  z=float(x);return z if math.isfinite(z) else d
 except:return d
def qp(p:Path):return p.resolve().as_posix().replace("'","''")
def cats(r):
 role=str(r.get('action_role') or '');side=str(r.get('action_side') or '');cls=str(r.get('action_class') or '')
 return [1.0 if role==x else 0.0 for x in ROLES]+[1.0 if side=='UP' else 0.0,1.0 if cls=='REPAIR' else 0.0,1.0 if cls=='EXPAND' else 0.0]
def X(rows,cols):return np.asarray([[safe(r.get(k)) for k in cols]+cats(r) for r in rows],float)
def auc(y,p):return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def load(phaseb,micro):
 c=duckdb.connect(database=':memory:')
 try:
  mc=[x[0] for x in c.execute(f"describe select * from read_parquet('{qp(micro)}')").fetchall()]
  req=set(QUEUE_DEPTH+BOOK_CHURN+TRUE_TRADE);miss=sorted(req-set(mc))
  if miss:raise RuntimeError(f'missing micro columns {miss}')
  sel='p.*,'+','.join('m."'+x+'"' for x in sorted(req))
  cur=c.execute(f"select {sel} from read_parquet('{qp(phaseb)}') p join read_parquet('{qp(micro)}') m using(market_id,decision_ms,pair_id,action_class,action_side,action_price,action_qty) order by p.window_end_ms,p.market_id,p.decision_ms,p.action_class")
  cols=[x[0] for x in cur.description];return [dict(zip(cols,r)) for r in cur.fetchall()]
 finally:c.close()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phaseb',required=True,type=Path);ap.add_argument('--micro',required=True,type=Path);ap.add_argument('--output',required=True);a=ap.parse_args();rows=load(a.phaseb,a.micro);mids=[]
 for r in rows:
  m=int(r['market_id'])
  if m not in mids:mids.append(m)
 if len(rows)!=200 or len(mids)!=100:raise RuntimeError(f'incomplete join {len(rows)}/{len(mids)}')
 base=list(GEOM)+list(FULL_EXTRA);reports=[]
 variants={"FULL":tuple()};variants.update(FAMILIES)
 for fi,(tr0,tr1,te0,te1) in enumerate(FOLDS,1):
  trm=set(mids[tr0:tr1]);tem=set(mids[te0:te1]);tr=[r for r in rows if int(r['market_id']) in trm];te=[r for r in rows if int(r['market_id']) in tem];y=np.asarray([int(r['label_structural_fill']) for r in tr]);yy=np.asarray([int(r['label_structural_fill']) for r in te]);lag=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in tr]));lagy=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in te]));rec={'fold':fi,'models':{}}
  for vi,(name,extra) in enumerate(variants.items()):
   cols=base+list(extra);xt=X(tr,cols);xe=X(te,cols);clf=ExtraTreesClassifier(n_estimators=500,min_samples_leaf=3,max_features=.75,class_weight='balanced',random_state=260907+fi,n_jobs=1).fit(xt,y);pp=clf.predict_proba(xe)[:,1];reg=ExtraTreesRegressor(n_estimators=500,min_samples_leaf=3,max_features=.75,random_state=261907+fi,n_jobs=1).fit(xt,lag);lp=reg.predict(xe);rec['models'][name]={'auc':auc(yy,pp),'lagMae':float(mean_absolute_error(lagy,lp))}
  b=rec['models']['FULL'];rec['delta']={n:{'aucVsFull':None if rec['models'][n]['auc'] is None or b['auc'] is None else float(rec['models'][n]['auc']-b['auc']),'lagImprovementVsFull':float(b['lagMae']-rec['models'][n]['lagMae'])} for n in FAMILIES};reports.append(rec);print(json.dumps(rec,ensure_ascii=False),flush=True)
 summary={}
 for n in FAMILIES:
  da=[r['delta'][n]['aucVsFull'] for r in reports if r['delta'][n]['aucVsFull'] is not None];dl=[r['delta'][n]['lagImprovementVsFull'] for r in reports];summary[n]={'aucBeatsFullFolds':sum(x>0 for x in da),'meanAucDeltaVsFull':float(np.mean(da)) if da else None,'lagBeatsFullFolds':sum(x>0 for x in dl),'meanLagImprovementVsFull':float(np.mean(dl))}
 out={'version':'MANAGEMENT_PHASEB_MICROSTRUCTURE_FAMILY_ABLATION_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'rows':len(rows),'markets':len(mids),'families':{k:list(v) for k,v in FAMILIES.items()},'folds':reports,'summary':summary,'boundary':['strict forward market folds 40->20,60->20,80->20','same FULL baseline and fixed ExtraTrees for every family','families fixed before scoring: queue/depth, L2 churn, true trade flow','negative depth is churn only','true matches are canonical past-only MID-offset flow','no threshold/hyperparameter sweep','family localization diagnostic only, not model selection or policy authority','no winner/Target future/no NEW24-B/no dream fill/no 8781']}
 op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
