from __future__ import annotations

"""Strict-forward predictability test for branch-specific execution resolution.

Targets are exact-fork branch resolution semantics only:
- structural fill vs slot release
- resolution lag

This test asks whether current strict-past state/action features are sufficient for
an execution head aligned to the exact branch, before adding queue/depth/flow state.
No runtime authority and no strategy threshold search.
"""

import argparse, json, math, os
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor
from sklearn.metrics import roc_auc_score, mean_absolute_error

EPS=1e-9
FOLDS=((0,40,40,60),(0,60,60,80),(0,80,80,100))
ROLES=("PROBE_CORE","ECONOMIC_CORE","SATELLITE_REPAIR","SATELLITE_EXPAND")

GEOM=(
 "state_floor","state_best","state_abs_net","state_total_debt","state_initial_debt_qty","state_paid_debt_qty",
 "state_remaining_debt_qty","state_repair_progress_frac","state_free_slots",
 "action_price","action_qty","action_price_to_bid","action_ask_to_price","action_pair_legal",
 "context_immediate_delta_floor","context_immediate_delta_best","context_immediate_delta_gap",
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

def load(p:Path):
 c=duckdb.connect(database=':memory:')
 try:
  q="select * from read_parquet('"+p.resolve().as_posix().replace("'","''")+"') order by window_end_ms,market_id,decision_ms,action_class"
  cur=c.execute(q);cols=[x[0] for x in cur.description];return [dict(zip(cols,r)) for r in cur.fetchall()]
 finally:c.close()
def cats(r):
 role=str(r.get('action_role') or '');side=str(r.get('action_side') or '');cls=str(r.get('action_class') or '')
 return [1.0 if role==x else 0.0 for x in ROLES]+[1.0 if side=='UP' else 0.0,1.0 if cls=='REPAIR' else 0.0,1.0 if cls=='EXPAND' else 0.0]
def X(rows,full):
 cols=list(GEOM)+(list(FULL_EXTRA) if full else [])
 return np.asarray([[safe(r.get(k)) for k in cols]+cats(r) for r in rows],float)
def auc(y,p):
 return float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phaseb',required=True,type=Path);ap.add_argument('--output',required=True);a=ap.parse_args()
 rows=load(a.phaseb);mids=[]
 for r in rows:
  m=int(r['market_id'])
  if m not in mids:mids.append(m)
 if len(mids)!=100:raise RuntimeError(f'expected100 got{len(mids)}')
 reports=[];oos=[]
 for fi,(tr0,tr1,te0,te1) in enumerate(FOLDS,1):
  trm=set(mids[tr0:tr1]);tem=set(mids[te0:te1]);tr=[r for r in rows if int(r['market_id']) in trm];te=[r for r in rows if int(r['market_id']) in tem]
  rec={'fold':fi,'trainMarkets':len(trm),'testMarkets':len(tem),'trainRows':len(tr),'testRows':len(te),'models':{}}
  y=np.asarray([int(r['label_structural_fill']) for r in tr],int);yy=np.asarray([int(r['label_structural_fill']) for r in te],int)
  lag=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in tr],float));lagy=np.log1p(np.asarray([safe(r['label_resolution_lag_ms']) for r in te],float))
  for name,full in [('GEOM',False),('FULL',True)]:
   x=X(tr,full);xx=X(te,full)
   clf=ExtraTreesClassifier(n_estimators=400,min_samples_leaf=3,max_features=.75,class_weight='balanced',random_state=260907+fi,n_jobs=1).fit(x,y)
   pp=clf.predict_proba(xx)[:,1]
   reg=ExtraTreesRegressor(n_estimators=400,min_samples_leaf=3,max_features=.75,random_state=261907+fi,n_jobs=1).fit(x,lag)
   lp=reg.predict(xx)
   rec['models'][name]={'structuralFillAuc':auc(yy,pp),'structuralFillBaseRate':float(np.mean(yy)),'logLagMae':float(mean_absolute_error(lagy,lp)),'lagNaiveMedianMae':float(mean_absolute_error(lagy,np.full(len(lagy),float(np.median(lag)))))}
   for j,r in enumerate(te):
    if len(oos)<=0 or True:
     pass
   if name=='FULL':
    for j,r in enumerate(te):oos.append({'fold':fi,'marketId':int(r['market_id']),'actionClass':str(r['action_class']),'trueFill':int(yy[j]),'pFillFull':float(pp[j]),'trueLogLag':float(lagy[j]),'predLogLagFull':float(lp[j])})
  ga=rec['models']['GEOM']['structuralFillAuc'];fa=rec['models']['FULL']['structuralFillAuc']
  rec['delta']={'aucFullMinusGeom':None if ga is None or fa is None else float(fa-ga),'fullLagMaeImprovementVsGeom':float(rec['models']['GEOM']['logLagMae']-rec['models']['FULL']['logLagMae']),'fullLagMaeImprovementVsNaive':float(rec['models']['FULL']['lagNaiveMedianMae']-rec['models']['FULL']['logLagMae'])}
  reports.append(rec);print(json.dumps(rec,ensure_ascii=False),flush=True)
 aucs=[r['models']['FULL']['structuralFillAuc'] for r in reports if r['models']['FULL']['structuralFillAuc'] is not None]
 aucd=[r['delta']['aucFullMinusGeom'] for r in reports if r['delta']['aucFullMinusGeom'] is not None]
 lagi=[r['delta']['fullLagMaeImprovementVsNaive'] for r in reports]
 summary={'fullFillAucMean':float(np.mean(aucs)) if aucs else None,'fullFillAucMin':float(np.min(aucs)) if aucs else None,'fullBeatsGeomAucFolds':int(sum(x>0 for x in aucd)),'foldsWithAuc':len(aucd),'fullLagBeatsNaiveFolds':int(sum(x>0 for x in lagi)),'fullLagMeanImprovementVsNaive':float(np.mean(lagi))}
 fill_pass=bool(summary['fullFillAucMean'] is not None and summary['fullFillAucMean']>=.65 and summary['fullFillAucMin']>=.55 and summary['fullBeatsGeomAucFolds']>=2)
 lag_pass=bool(summary['fullLagBeatsNaiveFolds']>=2 and summary['fullLagMeanImprovementVsNaive']>0)
 if fill_pass and lag_pass:verdict='BRANCH_RESOLUTION_STATE_SIGNAL_ESTABLISHED'
 elif fill_pass or lag_pass:verdict='PARTIAL_BRANCH_RESOLUTION_SIGNAL'
 else:verdict='CURRENT_STATE_INSUFFICIENT_FOR_BRANCH_RESOLUTION'
 out={'version':'MANAGEMENT_PHASEB_BRANCH_RESOLUTION_PREDICTABILITY_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'rows':len(rows),'markets':len(mids),'features':{'GEOM':list(GEOM)+['roleOneHot','sideUp','repairFlag','expandFlag'],'FULL_EXTRA':list(FULL_EXTRA)},'folds':reports,'summary':summary,'diagnosticGate':{'fillPass':fill_pass,'lagPass':lag_pass},'verdict':verdict,'boundary':['strict forward market folds 40->20,60->20,80->20','exact-fork branch resolution labels only','all features strict-past/current candidate','no winner/settlement/Target future feature','fixed ExtraTrees only; no tuning/threshold sweep','diagnostic gate only; no policy authority','if current-state insufficient, next evidence target is queue/depth/flow execution state rather than more policy rules','no NEW24-B/no dream fill/no 8781']}
 op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' and os.environ.get('BTC5M_LAN_RESULT_DIR') else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary,'gate':out['diagnosticGate'],'verdict':verdict},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
