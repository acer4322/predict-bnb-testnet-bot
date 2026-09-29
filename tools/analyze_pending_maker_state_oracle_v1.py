from __future__ import annotations

import importlib.util
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/target_maker_taker_coordination_big_v1'
HANDOFF=OUT/'post_taker_handoff_pending_oracle_v0.csv'
BOOK_DB=ROOT/'data/wallet_maker_book_inference.db'
REPORT=OUT/'pending_maker_state_oracle_v1_report.json'
ARTIFACT=OUT/'handoff_full_plus_pending_oracle_v1.joblib'
BASELINE=OUT/'report_handoff_full.json'
GRID=.01
MIN_CANCEL_CONF=.58


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path); mod=importlib.util.module_from_spec(spec); assert spec and spec.loader; spec.loader.exec_module(mod); return mod

coord=load(ROOT/'tools/train_target_maker_taker_coordination_big_v1.py','coord')
v0=load(ROOT/'tools/analyze_pending_maker_state_oracle_v0.py','oracle0')


def ro(path):
    con=sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro",uri=True,timeout=30); con.row_factory=sqlite3.Row; con.execute('pragma query_only=on'); return con


def finite(x):
    try:
        v=float(x); return v if math.isfinite(v) else math.nan
    except Exception: return math.nan


def load_cancel_intervals():
    con=ro(BOOK_DB)
    try:
        by=defaultdict(list)
        q='''select candidate_id,market_id,target_side,target_price,placement_source_ms,cancel_source_ms,allocated_quantity,confidence,confidence_label from maker_book_inference_v21_cancel_candidates where confidence>=?'''
        for r in con.execute(q,(MIN_CANCEL_CONF,)): by[int(r['market_id'])].append(dict(r))
        for m in by: by[m].sort(key=lambda x:(int(x['placement_source_ms']),int(x['cancel_source_ms']),str(x['candidate_id'])))
        return by
    finally: con.close()


def spec_features(row,by):
    m=int(row.market_id); cp=int(row.checkpoint_ms); intervention=str(row.intervention_side or '')
    bids={'UP':finite(row.up_bid),'DOWN':finite(row.down_bid)}
    active=[]
    for c in by.get(m,[]):
        a=int(c['placement_source_ms']); z=int(c['cancel_source_ms'])
        if a>cp: break
        if not (a<=cp<z): continue
        side=str(c['target_side']); px=float(c['target_price']); bid=bids.get(side,math.nan)
        active.append({'side':side,'shares':float(c['allocated_quantity']),'age':float(cp-a),'depth':((bid-px)/GRID if math.isfinite(bid) else math.nan),'confidence':float(c['confidence'])})
    out={}
    for side in ('UP','DOWN'):
        xs=[x for x in active if x['side']==side]; ds=[x['depth'] for x in xs if math.isfinite(x['depth'])]
        lo=side.lower(); out[f'oracle_spec_{lo}_count']=float(len(xs)); out[f'oracle_spec_{lo}_shares']=float(sum(x['shares'] for x in xs)); out[f'oracle_spec_{lo}_oldest_age_ms']=float(max([x['age'] for x in xs])) if xs else math.nan; out[f'oracle_spec_{lo}_nearest_depth_ticks']=float(min(ds,key=lambda v:abs(v))) if ds else math.nan; out[f'oracle_spec_{lo}_mean_confidence']=float(np.mean([x['confidence'] for x in xs])) if xs else math.nan
    su,sd=out['oracle_spec_up_shares'],out['oracle_spec_down_shares']
    out['oracle_spec_any']=float(bool(active)); out['oracle_spec_both']=float(su>0 and sd>0); out['oracle_spec_gross']=su+sd; out['oracle_spec_net']=su-sd
    out['oracle_spec_same_taker_shares']=su if intervention=='UP' else sd if intervention=='DOWN' else 0.; out['oracle_spec_opp_taker_shares']=sd if intervention=='UP' else su if intervention=='DOWN' else 0.; out['oracle_spec_same_minus_opp']=out['oracle_spec_same_taker_shares']-out['oracle_spec_opp_taker_shares']
    # Combined retrospective belief: high-confidence future-filled parent + unfilled placement->cancel interval.
    cu=float(row.oracle_pending_up_shares)+su; cd=float(row.oracle_pending_down_shares)+sd
    out['oracle_all_up_shares']=cu; out['oracle_all_down_shares']=cd; out['oracle_all_gross']=cu+cd; out['oracle_all_net']=cu-cd; out['oracle_all_any']=float(cu+cd>0); out['oracle_all_both']=float(cu>0 and cd>0)
    out['oracle_all_same_taker_shares']=cu if intervention=='UP' else cd if intervention=='DOWN' else 0.; out['oracle_all_opp_taker_shares']=cd if intervention=='UP' else cu if intervention=='DOWN' else 0.; out['oracle_all_same_minus_opp']=out['oracle_all_same_taker_shares']-out['oracle_all_opp_taker_shares']
    return out

SPEC_FEATURES=['oracle_spec_up_count','oracle_spec_up_shares','oracle_spec_up_oldest_age_ms','oracle_spec_up_nearest_depth_ticks','oracle_spec_up_mean_confidence','oracle_spec_down_count','oracle_spec_down_shares','oracle_spec_down_oldest_age_ms','oracle_spec_down_nearest_depth_ticks','oracle_spec_down_mean_confidence','oracle_spec_any','oracle_spec_both','oracle_spec_gross','oracle_spec_net','oracle_spec_same_taker_shares','oracle_spec_opp_taker_shares','oracle_spec_same_minus_opp','oracle_all_up_shares','oracle_all_down_shares','oracle_all_gross','oracle_all_net','oracle_all_any','oracle_all_both','oracle_all_same_taker_shares','oracle_all_opp_taker_shares','oracle_all_same_minus_opp']


def main():
    df=pd.read_csv(HANDOFF); by=load_cancel_intervals(); sdf=pd.DataFrame([spec_features(r,by) for _,r in df.iterrows()]); aug=pd.concat([df.reset_index(drop=True),sdf],axis=1)
    split=coord.split_markets(aug); parts={k:aug[aug.market_id.astype(int).isin(v)].copy() for k,v in split.items()}
    features=list(coord.HANDOFF_FEATURE_SETS['FULL'])+v0.ORACLE_FEATURES+SPEC_FEATURES
    m=coord.ebm(features); m.fit(coord.numeric(parts['train'],features),parts['train'].label_handoff.astype(str).tolist()); classes=[str(x) for x in m.classes_]; joblib.dump({'version':'PENDING_MAKER_STATE_ORACLE_V1','teacherOnlyOracle':True,'features':features,'classes':classes,'model':m},ARTIFACT)
    metrics={}
    for k,d in parts.items(): X=coord.numeric(d,features); metrics[k]=coord.multi_metrics(d.label_handoff.astype(str),m.predict(X),m.predict_proba(X),classes)
    base=json.loads(BASELINE.read_text(encoding='utf-8')); cmp={k:{'baselineBalancedAccuracy':float(base[k]['balancedAccuracy']),'oracleBalancedAccuracy':float(metrics[k]['balancedAccuracy']),'balancedAccuracyLift':float(metrics[k]['balancedAccuracy']-base[k]['balancedAccuracy']),'baselineMacroF1':float(base[k]['macroF1']),'oracleMacroF1':float(metrics[k]['macroF1']),'macroF1Lift':float(metrics[k]['macroF1']-base[k]['macroF1'])} for k in ('validation','test')}
    cov={'rows':len(aug),'confirmedPendingRate':float((aug.oracle_pending_any>.5).mean()),'specPendingRate':float((aug.oracle_spec_any>.5).mean()),'allPendingRate':float((aug.oracle_all_any>.5).mean()),'specBothRate':float((aug.oracle_spec_both>.5).mean())}
    rep={'reportVersion':'PENDING_MAKER_STATE_ORACLE_V1','researchOnly':True,'teacherOnlyOracle':True,'runtimeDeployable':False,'question':'Does adding speculative unfilled placement-to-cancel active intervals rescue the pending-Maker-state explanation of post-Taker handoff?','antiLeakGuard':'Only placement intervals already started at checkpoint are exposed; no placements beginning after checkpoint. Future cancel/fill is used only retrospectively to identify whether a pre-existing anonymous placement survived as an inferred Target order.','cancelConfidenceMin':MIN_CANCEL_CONF,'coverage':cov,'splitMarkets':{k:len(v) for k,v in split.items()},'metrics':metrics,'comparisonToHistoricalFull':cmp,'topTerms':coord.top_terms(m,30),'artifact':str(ARTIFACT),'interpretationBoundary':'Teacher-only upper-bound diagnostic; never runtime-deployable.'}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
