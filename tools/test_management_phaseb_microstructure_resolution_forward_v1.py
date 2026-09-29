from __future__ import annotations

"""Strict-forward falsification of execution microstructure for Phase-B branch resolution.

Compares current clean state/action FULL baseline against FULL+MICRO and a
within-action-class shuffled MICRO control.  Micro features are strict causal
Execution-Tape-derived values only; labels remain exact-fork branch resolution.
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
GEOM=(
 "state_floor","state_best","state_abs_net","state_total_debt","state_initial_debt_qty","state_paid_debt_qty",
 "state_remaining_debt_qty","state_repair_progress_frac","state_free_slots","action_price","action_qty",
 "action_price_to_bid","action_ask_to_price","action_pair_legal","context_immediate_delta_floor",
 "context_immediate_delta_best","context_immediate_delta_gap",
)
FULL_EXTRA=(
 "state_seconds_left","state_coverage","state_gross","state_debt_up","state_debt_down",
 "state_oldest_repair_progress","state_oldest_repair_age_ms","state_responsibility_count","state_live_slots",
 "state_repair_family_live_slots","state_satellite_expand_live_slots","state_expand_family_live_slots",
 "state_pending_cancel_count","state_book_imbalance","state_spread","state_dominant_mid",
 "state_q_ladder_live","state_q_pending_active","context_side_bid","context_side_ask","context_side_mid",
 "context_side_is_dominant","context_side_is_weak","context_target_debt_for_action_side","action_q_arm_used",
)

def safe(x:Any,d=0.0)->float:
 try:
  z=float(x);return z if math.isfinite(z) else d
 except:return d

def qp(p:Path)->str:return p.resolve().as_posix().replace("'","''")
def load_join(phaseb:Path,micro:Path):
 c=duckdb.connect(database=':memory:')
 try:
  # reject any labels in MICRO before join
  mcols=[x[0] for x in c.execute(f"describe select * from read_parquet('{qp(micro)}')").fetchall()]
  bad=[x for x in mcols if x.startswith('label_') or 'winner' in x.lower() or ('target' in x.lower() and 'candidate' not in x.lower())]
  if bad:raise RuntimeError(f'forbidden micro columns: {bad}')
  mfeat=[x for x in mcols if x.startswith('micro_')]
  if not mfeat:raise RuntimeError('no micro features')
  sel='p.*,'+','.join('m."'+x.replace('"','""')+'"' for x in mfeat)
  sql=(f"select {sel} from read_parquet('{qp(phaseb)}') p join read_parquet('{qp(micro)}') m "
       "using(market_id,decision_ms,pair_id,action_class,action_side,action_price,action_qty) "
       "order by p.window_end_ms,p.market_id,p.decision_ms,p.action_class")
  cur=c.execute(sql);cols=[x[0] for x in cur.description];rows=[dict(zip(cols,r)) for r in cur.fetchall()]
  return rows,mfeat
 finally:c.close()
def cats(r):
 role=str(r.get('action_role') or '');side=str(r.get('action_side') or '');cls=str(r.get('action_class') or '')
 return [1.0 if role==x else 0.0 for x in ROLES]+[1.0 if side=='UP' else 0.0,1.0 if cls=='REPAIR' else 0.0,1.0 if cls=='EXPAND' else 0.0]
def X(rows,cols):return np.asarray([[safe(r.get(k)) for k in cols]+cats(r) for r in rows],float)
def auc(y,p):return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
def shuffled_micro(rows,mfeat,seed):
 rng=np.random.default_rng(seed); out=[dict(r) for r in rows]
 for cls in ('REPAIR','EXPAND'):
  idx=[i for i,r in enumerate(rows) if str(r.get('action_class'))==cls]
  perm=np.array(idx,dtype=int);rng.shuffle(perm)
  for dst,src in zip(idx,perm):
   for k in mfeat:out[dst][k]=rows[int(src)].get(k)
 return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phaseb',required=True,type=Path);ap.add_argument('--micro',required=True,type=Path);ap.add_argument('--output',required=True);a=ap.parse_args()
 rows,mfeat=load_join(a.phaseb,a.micro);mids=[]
 for r in rows:
  m=int(r['market_id'])
  if m not in mids:mids.append(m)
 if len(rows)!=200 or len(mids)!=100:raise RuntimeError(f'join incomplete rows={len(rows)} markets={len(mids)}')
 fullcols=list(GEOM)+list(FULL_EXTRA);microcols=fullcols+list(mfeat);reports=[]
 for fi,(tr0,tr1,te0,te1) in enumerate(FOLDS,1):
  trm=set(mids[tr0:tr1]);tem=set(mids[te0:te1]);tr=[r for r in rows if int(r['market_id']) in trm];te=[r for r in rows if int(r['market_id']) in tem]
  strn=shuffled_micro(tr,mfeat,2609070+fi);sten=shuffled_micro(te,mfeat,2609170+fi)
  y=np.asarray([int(r['label_structural_fill']) for r in tr]);yy=np.asarray([int(r['label_structural_fill']) for r in te]);lag=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in tr]));lagy=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in te]))
  variants=[('FULL',tr,te,fullcols),('FULL_MICRO',tr,te,microcols),('FULL_SHUFFLED_MICRO',strn,sten,microcols)]
  rec={'fold':fi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':len(tr),'testRows':len(te),'models':{}}
  for name,atr,ate,cols in variants:
   xt=X(atr,cols);xe=X(ate,cols)
   clf=ExtraTreesClassifier(n_estimators=500,min_samples_leaf=3,max_features=.75,class_weight='balanced',random_state=260907+fi,n_jobs=1).fit(xt,y);pp=clf.predict_proba(xe)[:,1]
   reg=ExtraTreesRegressor(n_estimators=500,min_samples_leaf=3,max_features=.75,random_state=261907+fi,n_jobs=1).fit(xt,lag);lp=reg.predict(xe)
   rec['models'][name]={'structuralFillAuc':auc(yy,pp),'logLagMae':float(mean_absolute_error(lagy,lp)),'naiveLagMae':float(mean_absolute_error(lagy,np.full(len(lagy),float(np.median(lag)))))}
  b=rec['models']['FULL'];m=rec['models']['FULL_MICRO'];s=rec['models']['FULL_SHUFFLED_MICRO']
  rec['delta']={'microAucMinusFull':None if b['structuralFillAuc'] is None or m['structuralFillAuc'] is None else float(m['structuralFillAuc']-b['structuralFillAuc']),'microAucMinusShuffle':None if m['structuralFillAuc'] is None or s['structuralFillAuc'] is None else float(m['structuralFillAuc']-s['structuralFillAuc']),'microLagImprovementVsFull':float(b['logLagMae']-m['logLagMae']),'microLagImprovementVsShuffle':float(s['logLagMae']-m['logLagMae'])}
  reports.append(rec);print(json.dumps(rec,ensure_ascii=False),flush=True)
 da=[r['delta']['microAucMinusFull'] for r in reports if r['delta']['microAucMinusFull'] is not None];ds=[r['delta']['microAucMinusShuffle'] for r in reports if r['delta']['microAucMinusShuffle'] is not None];dl=[r['delta']['microLagImprovementVsFull'] for r in reports];dls=[r['delta']['microLagImprovementVsShuffle'] for r in reports];ma=[r['models']['FULL_MICRO']['structuralFillAuc'] for r in reports if r['models']['FULL_MICRO']['structuralFillAuc'] is not None]
 summary={'microFillAucMean':float(np.mean(ma)) if ma else None,'microFillAucMin':float(np.min(ma)) if ma else None,'microBeatsFullAucFolds':sum(x>0 for x in da),'microBeatsShuffleAucFolds':sum(x>0 for x in ds),'meanAucIncrementVsFull':float(np.mean(da)) if da else None,'meanAucIncrementVsShuffle':float(np.mean(ds)) if ds else None,'microLagBeatsFullFolds':sum(x>0 for x in dl),'microLagBeatsShuffleFolds':sum(x>0 for x in dls),'meanLagImprovementVsFull':float(np.mean(dl)),'meanLagImprovementVsShuffle':float(np.mean(dls))}
 fill=bool(summary['microBeatsFullAucFolds']>=2 and summary['microBeatsShuffleAucFolds']>=2 and (summary['meanAucIncrementVsFull'] or 0)>0 and (summary['meanAucIncrementVsShuffle'] or 0)>0)
 lagok=bool(summary['microLagBeatsFullFolds']>=2 and summary['microLagBeatsShuffleFolds']>=2 and summary['meanLagImprovementVsFull']>0 and summary['meanLagImprovementVsShuffle']>0)
 verdict='MICROSTRUCTURE_EXECUTION_SIGNAL_ESTABLISHED' if fill and lagok else ('PARTIAL_MICROSTRUCTURE_SIGNAL' if fill or lagok else 'MICROSTRUCTURE_SIGNAL_NOT_ESTABLISHED')
 out={'version':'MANAGEMENT_PHASEB_MICROSTRUCTURE_RESOLUTION_FORWARD_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'rows':len(rows),'markets':len(mids),'microFeatureCount':len(mfeat),'microFeatures':mfeat,'folds':reports,'summary':summary,'diagnosticGate':{'fillPass':fill,'lagPass':lagok},'verdict':verdict,'boundary':['strict forward market folds 40->20,60->20,80->20','FULL baseline identical clean current-state/action family','MICRO derived from Execution Tape with OUR receivedMs <= decision_ms semantics','raw matches use canonical second-granular MID offset; only effective past trades included','negative L2 depth is churn only, never synthetic trade','within-action-class shuffled MICRO control in same run','fixed ExtraTrees; no threshold/hyperparameter sweep','diagnostic only; no policy authority','no winner/Target future/no NEW24-B/no dream fill/no 8781']}
 op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary,'gate':out['diagnosticGate'],'verdict':verdict},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
