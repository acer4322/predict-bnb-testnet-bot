from __future__ import annotations

"""Diagnostic: does future order-arrival queue state explain Phase-B resolution?

Arrival features are FUTURE ORACLE values at decision_ms + measured entry latency.
They are never runtime eligible.  This test compares them with causal decision-time
queue/depth under identical strict-forward folds and model family.
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
QUEUE_SUFFIX=("native_side_buy","native_price","queue_ahead_qty","same_best_price","same_best_depth","opp_best_price","opp_best_depth","same_top3_depth","same_top5_depth","opp_top3_depth","opp_top5_depth","better_same_depth","better_same_levels","worse_within1tick_depth","worse_within2tick_depth","worse_within4tick_depth","native_spread_ticks","candidate_from_same_best_ticks","book_levels_bid","book_levels_ask","current_order_count","current_update_age_ms")
DECISION_QUEUE=tuple('micro_'+x for x in QUEUE_SUFFIX)
ARRIVAL_QUEUE=tuple('oracle_arrival_'+x for x in QUEUE_SUFFIX)

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

def load(phaseb,decision,arrival):
 c=duckdb.connect(database=':memory:')
 try:
  dcols=[x[0] for x in c.execute(f"describe select * from read_parquet('{qp(decision)}')").fetchall()];acols=[x[0] for x in c.execute(f"describe select * from read_parquet('{qp(arrival)}')").fetchall()]
  md=sorted(set(DECISION_QUEUE)-set(dcols));ma=sorted(set(ARRIVAL_QUEUE)-set(acols))
  if md or ma:raise RuntimeError(f'missing decision={md} arrival={ma}')
  sel='p.*,'+','.join('d."'+x+'"' for x in DECISION_QUEUE)+','+','.join('a."'+x+'"' for x in ARRIVAL_QUEUE)
  keys='market_id,decision_ms,pair_id,action_class,action_side,action_price,action_qty'
  cur=c.execute(f"select {sel} from read_parquet('{qp(phaseb)}') p join read_parquet('{qp(decision)}') d using({keys}) join read_parquet('{qp(arrival)}') a using({keys}) order by p.window_end_ms,p.market_id,p.decision_ms,p.action_class")
  cols=[x[0] for x in cur.description];return [dict(zip(cols,r)) for r in cur.fetchall()]
 finally:c.close()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phaseb',required=True,type=Path);ap.add_argument('--decision-micro',required=True,type=Path);ap.add_argument('--arrival-oracle',required=True,type=Path);ap.add_argument('--output',required=True);a=ap.parse_args();rows=load(a.phaseb,a.decision_micro,a.arrival_oracle);mids=[]
 for r in rows:
  m=int(r['market_id'])
  if m not in mids:mids.append(m)
 if len(rows)!=200 or len(mids)!=100:raise RuntimeError(f'incomplete join rows={len(rows)} markets={len(mids)}')
 base=list(GEOM)+list(FULL_EXTRA);variants={'FULL':base,'DECISION_QUEUE':base+list(DECISION_QUEUE),'ARRIVAL_QUEUE_ORACLE':base+list(ARRIVAL_QUEUE)};reports=[]
 for fi,(tr0,tr1,te0,te1) in enumerate(FOLDS,1):
  trm=set(mids[tr0:tr1]);tem=set(mids[te0:te1]);tr=[r for r in rows if int(r['market_id']) in trm];te=[r for r in rows if int(r['market_id']) in tem];y=np.asarray([int(r['label_structural_fill']) for r in tr]);yy=np.asarray([int(r['label_structural_fill']) for r in te]);lag=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in tr]));lagy=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in te]));rec={'fold':fi,'models':{}}
  for name,cols in variants.items():
   xt=X(tr,cols);xe=X(te,cols);clf=ExtraTreesClassifier(n_estimators=500,min_samples_leaf=3,max_features=.75,class_weight='balanced',random_state=260907+fi,n_jobs=1).fit(xt,y);pp=clf.predict_proba(xe)[:,1];reg=ExtraTreesRegressor(n_estimators=500,min_samples_leaf=3,max_features=.75,random_state=261907+fi,n_jobs=1).fit(xt,lag);lp=reg.predict(xe);rec['models'][name]={'auc':auc(yy,pp),'lagMae':float(mean_absolute_error(lagy,lp))}
  f=rec['models']['FULL'];d=rec['models']['DECISION_QUEUE'];o=rec['models']['ARRIVAL_QUEUE_ORACLE'];rec['delta']={'decisionAucVsFull':float(d['auc']-f['auc']),'arrivalAucVsFull':float(o['auc']-f['auc']),'arrivalAucVsDecision':float(o['auc']-d['auc']),'decisionLagVsFull':float(f['lagMae']-d['lagMae']),'arrivalLagVsFull':float(f['lagMae']-o['lagMae']),'arrivalLagVsDecision':float(d['lagMae']-o['lagMae'])};reports.append(rec);print(json.dumps(rec,ensure_ascii=False),flush=True)
 aa=[r['delta']['arrivalAucVsDecision'] for r in reports];al=[r['delta']['arrivalLagVsDecision'] for r in reports];af=[r['delta']['arrivalAucVsFull'] for r in reports];alf=[r['delta']['arrivalLagVsFull'] for r in reports]
 summary={'arrivalAucBeatsDecisionFolds':sum(x>0 for x in aa),'meanArrivalAucVsDecision':float(np.mean(aa)),'arrivalLagBeatsDecisionFolds':sum(x>0 for x in al),'meanArrivalLagVsDecision':float(np.mean(al)),'arrivalAucBeatsFullFolds':sum(x>0 for x in af),'meanArrivalAucVsFull':float(np.mean(af)),'arrivalLagBeatsFullFolds':sum(x>0 for x in alf),'meanArrivalLagVsFull':float(np.mean(alf))}
 fill=bool(summary['arrivalAucBeatsDecisionFolds']>=2 and summary['meanArrivalAucVsDecision']>0 and summary['arrivalAucBeatsFullFolds']>=2);lagok=bool(summary['arrivalLagBeatsDecisionFolds']>=2 and summary['meanArrivalLagVsDecision']>0 and summary['arrivalLagBeatsFullFolds']>=2)
 verdict='ARRIVAL_FRONTIER_MATERIAL' if fill or lagok else 'ARRIVAL_FRONTIER_NOT_ESTABLISHED'
 out={'version':'MANAGEMENT_PHASEB_ARRIVAL_FRONTIER_ORACLE_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'futureOracle':True,'entryLatencyMs':1092,'rows':len(rows),'markets':len(mids),'folds':reports,'summary':summary,'diagnosticGate':{'fillMaterial':fill,'lagMaterial':lagok},'verdict':verdict,'boundary':['arrival queue is FUTURE ORACLE at decision+1092ms and is NEVER runtime eligible','1092ms is measured execution latency, not strategy timing rule','strict forward market folds 40->20,60->20,80->20','same FULL baseline/fixed ExtraTrees for all variants','decision queue is causal; arrival queue diagnostic only','no threshold/hyperparameter sweep','no winner/Target future action/no NEW24-B/no dream fill/no 8781']}
 op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary,'gate':out['diagnosticGate'],'verdict':verdict},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
