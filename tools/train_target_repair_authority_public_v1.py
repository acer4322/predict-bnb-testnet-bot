from __future__ import annotations

import bisect
import csv
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
RISKSET=OUT/'target_repair_authority_hazard_v0_riskset.csv'
PUBLIC_DB=ROOT/'data/strategy_target_compare_v1.db'
BASE_REPORT=OUT/'target_repair_authority_hazard_v0_report.json'
MODEL_OUT=OUT/'target_repair_authority_public_v1.joblib'
REPORT_OUT=OUT/'target_repair_authority_public_v1_report.json'
JOINED_OUT=OUT/'target_repair_authority_public_v1_riskset.csv'
MAX_PUBLIC_AGE_MS=2000
EPS=1e-9

INVENTORY_FEATURES=[
 'seconds_left','abs_combined_delta','abs_maker_delta','maker_gross','combined_gross','imbalance_to_maker_gross',
 'imbalance_to_combined_gross','typical_maker_parent_shares','imbalance_in_maker_parent_units','worsening_maker_parent_streak',
 'prior_maker_parent_count','prior_taker_parent_count','ms_since_last_maker_parent','ms_since_last_taker_parent'
]
PUBLIC_FEATURES=[
 'recovery_mid','recovery_bid','recovery_ask','recovery_spread_ticks','other_spread_ticks','pair_ask_sum','pair_bid_sum',
 'predict_edge_toward_recovery','direction_score_toward_recovery','spot_return_1s_toward_recovery','spot_return_3s_toward_recovery',
 'futures_return_1s_toward_recovery','futures_return_3s_toward_recovery','spot_queue_toward_recovery','futures_queue_toward_recovery',
 'spot_taker_1s_toward_recovery','futures_taker_1s_toward_recovery','abs_spot_minus_strike_bps','abs_chainlink_minus_strike_bps',
 'perp_spot_basis_toward_recovery','spot_minus_chainlink_toward_recovery','public_age_ms'
]
FEATURES=INVENTORY_FEATURES+PUBLIC_FEATURES


def finite(v:Any)->float|None:
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception:return None


def fnum(v:Any)->float:
    x=finite(v); return x if x is not None else math.nan


def pv(s:dict[str,Any],camel:str)->float|None:
    return finite(s.get(camel))


def load_public()->dict[int,list[tuple[int,dict[str,Any]]]]:
    c=sqlite3.connect(f"file:{PUBLIC_DB.resolve().as_posix()}?mode=ro",uri=True,timeout=10); c.row_factory=sqlite3.Row
    out=defaultdict(list)
    try:
        for r in c.execute("select market_id,decision_ms,source_snapshot_ms,public_state_json from our_decisions where public_state_json is not null order by market_id,source_snapshot_ms,decision_ms"):
            try:s=json.loads(str(r['public_state_json']))
            except Exception:continue
            if not isinstance(s,dict):continue
            ms=int(r['source_snapshot_ms'] or r['decision_ms']); out[int(r['market_id'])].append((ms,s))
    finally:c.close()
    return out


def asof(rows:list[tuple[int,dict[str,Any]]],at:int)->tuple[int,dict[str,Any]]|None:
    if not rows:return None
    times=[x[0] for x in rows]; i=bisect.bisect_right(times,at)-1
    if i<0:return None
    t,s=rows[i]
    return (t,s) if 0<=at-t<=MAX_PUBLIC_AGE_MS else None


def oriented_features(r:dict[str,Any],s:dict[str,Any],age:int)->dict[str,Any]:
    delta=finite(r.get('combined_delta')) or 0.0
    if delta>EPS: recovery='DOWN'; sign=-1.0
    elif delta<-EPS: recovery='UP'; sign=1.0
    else: recovery=None; sign=0.0
    ub=pv(s,'predictUpBid'); ua=pv(s,'predictUpAsk'); um=pv(s,'predictUpMid'); db=pv(s,'predictDownBid'); da=pv(s,'predictDownAsk'); dm=pv(s,'predictDownMid')
    if recovery=='UP': rb,ra,rm,ob,oa=ub,ua,um,db,da
    elif recovery=='DOWN': rb,ra,rm,ob,oa=db,da,dm,ub,ua
    else: rb=ra=rm=ob=oa=None
    def mul(name:str)->float|None:
        x=pv(s,name); return x*sign if x is not None and sign else None
    return {
      'recovery_side':recovery or 'FLAT','recovery_mid':rm,'recovery_bid':rb,'recovery_ask':ra,
      'recovery_spread_ticks':((ra-rb)/0.01) if ra is not None and rb is not None else None,
      'other_spread_ticks':((oa-ob)/0.01) if oa is not None and ob is not None else None,
      'pair_ask_sum':(ua+da) if ua is not None and da is not None else None,
      'pair_bid_sum':(ub+db) if ub is not None and db is not None else None,
      'predict_edge_toward_recovery':((rm-0.5) if rm is not None else None),
      'direction_score_toward_recovery':mul('directionScore'),
      'spot_return_1s_toward_recovery':mul('spotReturn1sBps'),'spot_return_3s_toward_recovery':mul('spotReturn3sBps'),
      'futures_return_1s_toward_recovery':mul('futuresReturn1sBps'),'futures_return_3s_toward_recovery':mul('futuresReturn3sBps'),
      'spot_queue_toward_recovery':mul('spotQueueImbalance'),'futures_queue_toward_recovery':mul('futuresQueueImbalance'),
      'spot_taker_1s_toward_recovery':mul('spotTakerImbalance1s'),'futures_taker_1s_toward_recovery':mul('futuresTakerImbalance1s'),
      'abs_spot_minus_strike_bps':abs(pv(s,'spotMinusStrikeBps')) if pv(s,'spotMinusStrikeBps') is not None else None,
      'abs_chainlink_minus_strike_bps':abs(pv(s,'chainlinkMinusStrikeBps')) if pv(s,'chainlinkMinusStrikeBps') is not None else None,
      'perp_spot_basis_toward_recovery':mul('perpSpotBasisBps'),'spot_minus_chainlink_toward_recovery':mul('spotMinusChainlinkBps'),
      'public_age_ms':age,
    }


def metric(y:np.ndarray,p:np.ndarray)->dict[str,Any]:
    return {'rows':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,
      'rocAuc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'prAuc':float(average_precision_score(y,p)) if y.sum()>0 else None,
      'meanPPositive':float(p[y==1].mean()) if (y==1).any() else None,'meanPNegative':float(p[y==0].mean()) if (y==0).any() else None}


def main()->int:
    public=load_public(); rows=[]; dropped=0
    with RISKSET.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            mid=int(r['market_id']); at=int(float(r['checkpoint_ms'])); a=asof(public.get(mid,[]),at)
            if a is None:dropped+=1;continue
            t,s=a; rr=dict(r); rr.update(oriented_features(rr,s,at-t)); rows.append(rr)
    fields=list(rows[0].keys()) if rows else []
    with JOINED_OUT.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    cohorts={k:[r for r in rows if r['cohort']==k] for k in ['TRAIN_FROZEN126','VALIDATION_FROZEN126','FORWARD23','FORMAL10_OPENED_OOS']}
    train=cohorts['TRAIN_FROZEN126']; y=np.asarray([int(r['label_repair_onset_5s']) for r in train],int)
    X=np.asarray([[fnum(r.get(k)) for k in FEATURES] for r in train],float)
    # V1 deliberately uses modest-capacity models; no tuning on validation/forward/formal.
    hgb=HistGradientBoostingClassifier(max_iter=140,max_leaf_nodes=9,learning_rate=0.04,l2_regularization=2.0,class_weight='balanced',random_state=42).fit(X,y)
    logit=Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('clf',LogisticRegression(max_iter=1200,class_weight='balanced',C=0.5,random_state=42))]).fit(X,y)
    evals={}
    predictions={}
    for name,rs in cohorts.items():
        yy=np.asarray([int(r['label_repair_onset_5s']) for r in rs],int); xx=np.asarray([[fnum(r.get(k)) for k in FEATURES] for r in rs],float)
        ph=hgb.predict_proba(xx)[:,1] if len(rs) else np.asarray([]); pl=logit.predict_proba(xx)[:,1] if len(rs) else np.asarray([])
        evals[name]={'hgbPublic':metric(yy,ph) if len(rs) else {},'logitPublic':metric(yy,pl) if len(rs) else {}}
        predictions[name]=(rs,ph,pl)
    base=json.loads(BASE_REPORT.read_text(encoding='utf-8'))
    lift={}
    for name in ['VALIDATION_FROZEN126','FORWARD23','FORMAL10_OPENED_OOS']:
        b=base['evaluations'][name]['logit']; n=evals[name]['logitPublic']
        lift[name]={'rocAucLiftVsInventoryLogit':(n['rocAuc']-b['rocAuc']) if n.get('rocAuc') is not None and b.get('rocAuc') is not None else None,
                    'prAucLiftVsInventoryLogit':(n['prAuc']-b['prAuc']) if n.get('prAuc') is not None and b.get('prAuc') is not None else None}
    formal_rows,formal_hgb,formal_logit=predictions['FORMAL10_OPENED_OOS']; per=defaultdict(lambda:{'rows':0,'positives':0,'pred05':0,'maxP':0.0})
    for r,p in zip(formal_rows,formal_logit):
        d=per[int(r['market_id'])];d['rows']+=1;d['positives']+=int(r['label_repair_onset_5s']);d['pred05']+=int(p>=0.5);d['maxP']=max(d['maxP'],float(p))
    report={'version':'TARGET_REPAIR_AUTHORITY_PUBLIC_V1','researchOnly':True,'runtimeTargetInput':False,'strictPastPublicJoinMaxAgeMs':MAX_PUBLIC_AGE_MS,
      'rowsJoined':len(rows),'rowsDroppedNoStrictPastPublic':dropped,'features':FEATURES,'evaluations':evals,'liftVsInventoryOnlyLogit':lift,
      'formal10PerMarketLogitDiagnostic':dict(sorted(per.items())),
      'guardrails':['Labels remain Target repair-burst onset only; public features are strict-past <=2s.','No winner/PnL features.','Forward23/formal10 are evaluation only; no threshold sweep.','Sensor study only; no strategy/live changes.']}
    joblib.dump({'version':'TARGET_REPAIR_AUTHORITY_PUBLIC_V1','features':FEATURES,'hgb':hgb,'logit':logit,'report':report},MODEL_OUT)
    REPORT_OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(REPORT_OUT),'model':str(MODEL_OUT),'joined':str(JOINED_OUT),'evaluations':evals,'lift':lift,'formal10':report['formal10PerMarketLogitDiagnostic']},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
