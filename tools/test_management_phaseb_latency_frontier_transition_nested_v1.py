from __future__ import annotations

"""Nested strict-forward test of a Latency-Frontier Transition Head.

Offline supervision uses FUTURE arrival-frontier queue/depth at decision+1092ms,
but every prediction for a market is produced by a transition model trained only
on earlier markets and consuming only decision-time causal state/action/microstructure.
The predicted arrival state is then tested as input to branch-resolution models.
"""
import argparse,json,math,os
from pathlib import Path
from typing import Any
import duckdb
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier,ExtraTreesRegressor
from sklearn.metrics import roc_auc_score,mean_absolute_error

ROLES=("PROBE_CORE","ECONOMIC_CORE","SATELLITE_REPAIR","SATELLITE_EXPAND")
TRANS_BLOCKS=((0,20,20,40),(0,40,40,60),(0,60,60,80),(0,80,80,100))
VALUE_FOLDS=((20,40,40,60),(20,60,60,80),(20,80,80,100))
GEOM=("state_floor","state_best","state_abs_net","state_total_debt","state_initial_debt_qty","state_paid_debt_qty","state_remaining_debt_qty","state_repair_progress_frac","state_free_slots","action_price","action_qty","action_price_to_bid","action_ask_to_price","action_pair_legal","context_immediate_delta_floor","context_immediate_delta_best","context_immediate_delta_gap")
FULL_EXTRA=("state_seconds_left","state_coverage","state_gross","state_debt_up","state_debt_down","state_oldest_repair_progress","state_oldest_repair_age_ms","state_responsibility_count","state_live_slots","state_repair_family_live_slots","state_satellite_expand_live_slots","state_expand_family_live_slots","state_pending_cancel_count","state_book_imbalance","state_spread","state_dominant_mid","state_q_ladder_live","state_q_pending_active","context_side_bid","context_side_ask","context_side_mid","context_side_is_dominant","context_side_is_weak","context_target_debt_for_action_side","action_q_arm_used")
QUEUE_SUFFIX=("native_side_buy","native_price","queue_ahead_qty","same_best_price","same_best_depth","opp_best_price","opp_best_depth","same_top3_depth","same_top5_depth","opp_top3_depth","opp_top5_depth","better_same_depth","better_same_levels","worse_within1tick_depth","worse_within2tick_depth","worse_within4tick_depth","native_spread_ticks","candidate_from_same_best_ticks","book_levels_bid","book_levels_ask","current_order_count","current_update_age_ms")
DECISION_QUEUE=tuple('micro_'+x for x in QUEUE_SUFFIX)
# native side/price are deterministic from current action; predict only changing arrival frontier state
PRED_SUFFIX=QUEUE_SUFFIX[2:]
ARRIVAL_TARGETS=tuple('oracle_arrival_'+x for x in PRED_SUFFIX)
PRED_COLS=tuple('pred_arrival_'+x for x in PRED_SUFFIX)

def safe(x:Any,d=0.0):
 try:
  z=float(x);return z if math.isfinite(z) else d
 except:return d
def qp(p:Path):return p.resolve().as_posix().replace("'","''")
def cats(r):
 role=str(r.get('action_role') or '');side=str(r.get('action_side') or '');cls=str(r.get('action_class') or '')
 return [1.0 if role==x else 0.0 for x in ROLES]+[1.0 if side=='UP' else 0.0,1.0 if cls=='REPAIR' else 0.0,1.0 if cls=='EXPAND' else 0.0]
def X(rows,cols):return np.asarray([[safe(r.get(k)) for k in cols]+cats(r) for r in rows],float)
def Y(rows,cols):return np.asarray([[safe(r.get(k)) for k in cols] for r in rows],float)
def auc(y,p):return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def load(phaseb,decision,arrival):
 c=duckdb.connect(database=':memory:')
 try:
  dcols=[x[0] for x in c.execute(f"describe select * from read_parquet('{qp(decision)}')").fetchall()];acols=[x[0] for x in c.execute(f"describe select * from read_parquet('{qp(arrival)}')").fetchall()]
  md=sorted(set(DECISION_QUEUE)-set(dcols));ma=sorted(set(ARRIVAL_TARGETS)-set(acols))
  if md or ma:raise RuntimeError(f'missing decision={md} arrival={ma}')
  sel='p.*,'+','.join('d."'+x+'"' for x in DECISION_QUEUE)+','+','.join('a."'+x+'"' for x in ARRIVAL_TARGETS)
  keys='market_id,decision_ms,pair_id,action_class,action_side,action_price,action_qty'
  cur=c.execute(f"select {sel} from read_parquet('{qp(phaseb)}') p join read_parquet('{qp(decision)}') d using({keys}) join read_parquet('{qp(arrival)}') a using({keys}) order by p.window_end_ms,p.market_id,p.decision_ms,p.action_class")
  cols=[x[0] for x in cur.description];return [dict(zip(cols,r)) for r in cur.fetchall()]
 finally:c.close()

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phaseb',required=True,type=Path);ap.add_argument('--decision-micro',required=True,type=Path);ap.add_argument('--arrival-oracle',required=True,type=Path);ap.add_argument('--output',required=True);a=ap.parse_args();rows=load(a.phaseb,a.decision_micro,a.arrival_oracle);mids=[]
 for r in rows:
  m=int(r['market_id'])
  if m not in mids:mids.append(m)
 if len(rows)!=200 or len(mids)!=100:raise RuntimeError(f'incomplete join {len(rows)}/{len(mids)}')
 base=list(GEOM)+list(FULL_EXTRA);trans_in=base+list(DECISION_QUEUE);pred_by_key={};trans_reports=[]
 for bi,(tr0,tr1,te0,te1) in enumerate(TRANS_BLOCKS,1):
  trm=set(mids[tr0:tr1]);tem=set(mids[te0:te1]);tr=[r for r in rows if int(r['market_id']) in trm];te=[r for r in rows if int(r['market_id']) in tem];xt=X(tr,trans_in);xe=X(te,trans_in);yt=Y(tr,ARRIVAL_TARGETS);ye=Y(te,ARRIVAL_TARGETS);mu=yt.mean(axis=0);sd=yt.std(axis=0);sd=np.where(sd<1e-9,1.0,sd);z=(yt-mu)/sd
  reg=ExtraTreesRegressor(n_estimators=500,min_samples_leaf=3,max_features=.75,random_state=270907+bi,n_jobs=1).fit(xt,z);pred=reg.predict(xe)*sd+mu
  per=[]
  for j,k in enumerate(ARRIVAL_TARGETS):
   mae=float(mean_absolute_error(ye[:,j],pred[:,j]));naive=float(mean_absolute_error(ye[:,j],np.full(len(ye),float(np.median(yt[:,j])))));per.append({'target':k,'mae':mae,'naiveMae':naive,'improvementVsNaive':naive-mae})
  for i,r in enumerate(te):
   key=(int(r['market_id']),int(r['decision_ms']),str(r['action_class']))
   pred_by_key[key]={PRED_COLS[j]:float(pred[i,j]) for j in range(len(PRED_COLS))}
  trans_reports.append({'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':len(tr),'testRows':len(te),'meanNormalizedMae':float(np.mean(np.abs((ye-pred)/sd))),'targetsBeatingNaive':sum(x['improvementVsNaive']>0 for x in per),'targetCount':len(per),'perTarget':per})
  print(json.dumps({'transitionBlock':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'meanNormalizedMae':trans_reports[-1]['meanNormalizedMae'],'targetsBeatingNaive':trans_reports[-1]['targetsBeatingNaive']},ensure_ascii=False),flush=True)
 for r in rows:
  key=(int(r['market_id']),int(r['decision_ms']),str(r['action_class']))
  if key in pred_by_key:r.update(pred_by_key[key])
 predicted_markets=set(mids[20:]);missing=[(r['market_id'],r['decision_ms'],r['action_class']) for r in rows if int(r['market_id']) in predicted_markets and not all(k in r for k in PRED_COLS)]
 if missing:raise RuntimeError(f'missing predicted arrival rows {missing[:5]}')
 variants={'FULL':base,'DECISION_QUEUE':base+list(DECISION_QUEUE),'PREDICTED_ARRIVAL':base+list(PRED_COLS),'ARRIVAL_ORACLE':base+list(ARRIVAL_TARGETS)};folds=[]
 for fi,(tr0,tr1,te0,te1) in enumerate(VALUE_FOLDS,1):
  trm=set(mids[tr0:tr1]);tem=set(mids[te0:te1]);tr=[r for r in rows if int(r['market_id']) in trm];te=[r for r in rows if int(r['market_id']) in tem];y=np.asarray([int(r['label_structural_fill']) for r in tr]);yy=np.asarray([int(r['label_structural_fill']) for r in te]);lag=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in tr]));lagy=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in te]));rec={'fold':fi,'trainMarkets':len(trm),'testMarkets':len(tem),'models':{}}
  for name,cols in variants.items():
   xt=X(tr,cols);xe=X(te,cols);clf=ExtraTreesClassifier(n_estimators=500,min_samples_leaf=3,max_features=.75,class_weight='balanced',random_state=280907+fi,n_jobs=1).fit(xt,y);pp=clf.predict_proba(xe)[:,1];reg=ExtraTreesRegressor(n_estimators=500,min_samples_leaf=3,max_features=.75,random_state=281907+fi,n_jobs=1).fit(xt,lag);lp=reg.predict(xe);rec['models'][name]={'auc':auc(yy,pp),'lagMae':float(mean_absolute_error(lagy,lp))}
  b=rec['models']['FULL'];d=rec['models']['DECISION_QUEUE'];p=rec['models']['PREDICTED_ARRIVAL'];o=rec['models']['ARRIVAL_ORACLE'];rec['delta']={'predAucVsFull':float(p['auc']-b['auc']),'predAucVsDecision':float(p['auc']-d['auc']),'oracleAucVsPred':float(o['auc']-p['auc']),'predLagVsFull':float(b['lagMae']-p['lagMae']),'predLagVsDecision':float(d['lagMae']-p['lagMae']),'oracleLagVsPred':float(p['lagMae']-o['lagMae'])};folds.append(rec);print(json.dumps(rec,ensure_ascii=False),flush=True)
 pa=[r['delta']['predAucVsFull'] for r in folds];pad=[r['delta']['predAucVsDecision'] for r in folds];pl=[r['delta']['predLagVsFull'] for r in folds];pld=[r['delta']['predLagVsDecision'] for r in folds]
 summary={'transitionBlocks':len(trans_reports),'transitionTargetsBeatingNaiveMean':float(np.mean([r['targetsBeatingNaive']/r['targetCount'] for r in trans_reports])),'predAucBeatsFullFolds':sum(x>0 for x in pa),'meanPredAucVsFull':float(np.mean(pa)),'predAucBeatsDecisionFolds':sum(x>0 for x in pad),'meanPredAucVsDecision':float(np.mean(pad)),'predLagBeatsFullFolds':sum(x>0 for x in pl),'meanPredLagVsFull':float(np.mean(pl)),'predLagBeatsDecisionFolds':sum(x>0 for x in pld),'meanPredLagVsDecision':float(np.mean(pld))}
 fill=bool(summary['predAucBeatsFullFolds']>=2 and summary['meanPredAucVsFull']>0 and summary['predAucBeatsDecisionFolds']>=2);lagok=bool(summary['predLagBeatsFullFolds']>=2 and summary['meanPredLagVsFull']>0 and summary['predLagBeatsDecisionFolds']>=2)
 verdict='LATENCY_FRONTIER_TRANSITION_USEFUL' if fill or lagok else 'LATENCY_FRONTIER_TRANSITION_NOT_YET_USEFUL'
 out={'version':'MANAGEMENT_PHASEB_LATENCY_FRONTIER_TRANSITION_NESTED_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'entryLatencyMs':1092,'rows':len(rows),'markets':len(mids),'transitionInputCount':len(trans_in)+len(cats(rows[0])),'arrivalTargetCount':len(ARRIVAL_TARGETS),'transitionBlocks':trans_reports,'valueFolds':folds,'summary':summary,'diagnosticGate':{'fillUseful':fill,'lagUseful':lagok},'verdict':verdict,'boundary':['arrival oracle used only as offline transition supervision and oracle diagnostic','every predicted arrival state is OOS from a transition model trained only on earlier markets','resolution model train/test also strict-forward on OOS-predicted arrival states','no future arrival feature directly enters PREDICTED_ARRIVAL model','1092ms measured execution latency, not strategy timing rule','fixed ExtraTrees; no threshold/hyperparameter sweep','no winner/Target future/no NEW24-B/no dream fill/no 8781']}
 op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary,'gate':out['diagnosticGate'],'verdict':verdict},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
