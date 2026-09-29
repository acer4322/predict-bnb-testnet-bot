from __future__ import annotations

import csv
import json
import math
import sqlite3
import statistics
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'research' / 'execution_aware_fill_lifecycle_v0'
TARGET_DB = ROOT / 'data' / 'target_wallet_official_v1.db'
TEACHER_CSV = OUT / 'target_repair_responsibility_teacher_v0.csv'
CANONICAL = OUT / 'pair_completion_canonical_cohort_v1.json'
REGISTRY = OUT / 'r2_execution_graduation_exam_registry_v1.json'
MODEL_OUT = OUT / 'target_repair_authority_hazard_v0.joblib'
REPORT_OUT = OUT / 'target_repair_authority_hazard_v0_report.json'
RISKSET_OUT = OUT / 'target_repair_authority_hazard_v0_riskset.csv'
EPS = 1e-9
HORIZON_MS = 5000
BURST_GAP_MS = 5000

NUMERIC = [
    'seconds_left','abs_combined_delta','abs_maker_delta','maker_gross','combined_gross',
    'imbalance_to_maker_gross','imbalance_to_combined_gross','typical_maker_parent_shares',
    'imbalance_in_maker_parent_units','worsening_maker_parent_streak','prior_maker_parent_count',
    'prior_taker_parent_count','ms_since_last_maker_parent','ms_since_last_taker_parent',
]
CATEGORICAL = ['phase','last_parent_role']


def ro(path: Path) -> sqlite3.Connection:
    c=sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro",uri=True,timeout=10); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c


def signed(side:str, quote_type:str, shares:float)->tuple[float,float]:
    q=float(shares) if quote_type=='BID' else -float(shares)
    return (q,0.0) if side=='UP' else (0.0,q)


def directional(side:str, quote_type:str, shares:float)->float:
    u,d=signed(side,quote_type,shares); return u-d


def phase(seconds_left:float)->str:
    if seconds_left>240:return 'EARLY'
    if seconds_left>60:return 'MID'
    return 'TAIL'


def finite(v:Any)->float:
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception:return math.nan


def build_bursts()->dict[int,list[dict[str,Any]]]:
    repair=[]
    with TEACHER_CSV.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if r['responsibility']!='REPAIR_BALANCE':continue
            repair.append({'market_id':int(r['market_id']),'side':r['side'],'first_event_ms':int(float(r['first_event_ms'])),'last_event_ms':int(float(r['last_event_ms']))})
    by=defaultdict(list)
    for r in repair:by[r['market_id']].append(r)
    out=defaultdict(list)
    for mid,rs in by.items():
        cur=None
        for r in sorted(rs,key=lambda x:(x['first_event_ms'],x['last_event_ms'])):
            if cur is None or r['side']!=cur['side'] or r['first_event_ms']-cur['end']>BURST_GAP_MS:
                if cur is not None:out[mid].append(cur)
                cur={'side':r['side'],'start':r['first_event_ms'],'end':r['last_event_ms']}
            else: cur['end']=max(cur['end'],r['last_event_ms'])
        if cur is not None:out[mid].append(cur)
    return out


def build_riskset(ids:set[int], cohort_name:str, bursts:dict[int,list[dict[str,Any]]])->list[dict[str,Any]]:
    if not ids:return []
    db=ro(TARGET_DB)
    try:
        q=','.join('?' for _ in ids)
        metas={int(r['market_id']):dict(r) for r in db.execute(f'select market_id,window_end_ms from target_markets where market_id in ({q})',[*sorted(ids)])}
        parents=[dict(r) for r in db.execute(f'''select parent_id,market_id,role,side,quote_type,first_event_ms,last_event_ms,average_price,shares
          from target_parent_orders where market_id in ({q}) and role in ('MAKER','TAKER') order by market_id,last_event_ms,first_event_ms,parent_id''',[*sorted(ids)])]
    finally:db.close()
    by=defaultdict(list)
    for p in parents:by[int(p['market_id'])].append(p)
    rows=[]
    for mid in sorted(ids):
        meta=metas.get(mid); ps=by.get(mid,[])
        if not meta or not ps or meta.get('window_end_ms') is None:continue
        end=int(meta['window_end_ms']); open_ms=end-300000
        bs=sorted(bursts.get(mid,[]),key=lambda b:b['start']); starts=[b['start'] for b in bs]
        maker_up=maker_down=taker_up=taker_down=0.0
        maker_sizes=[]; maker_effects=[]; mp=tp=0; last_maker=None; last_taker=None
        for p in ps:
            role=str(p['role']); side=str(p['side']); qt=str(p['quote_type']); sh=float(p['shares']); at=int(p['last_event_ms'])
            u,d=signed(side,qt,sh)
            if role=='MAKER': maker_up+=u; maker_down+=d; mp+=1; maker_sizes.append(sh); maker_effects.append(directional(side,qt,sh)); last_maker=at
            else: taker_up+=u; taker_down+=d; tp+=1; last_taker=at
            # Checkpoint is AFTER this observed parent; never look inside current parent.
            if mp<=0:continue
            active=any(int(b['start'])<=at<=int(b['end'])+BURST_GAP_MS for b in bs)
            if active:continue
            pos=bisect_right(starts,at)
            next_start=starts[pos] if pos<len(starts) else None
            delta=(maker_up+taker_up)-(maker_down+taker_down); maker_delta=maker_up-maker_down
            maker_gross=abs(maker_up)+abs(maker_down); combined_gross=abs(maker_up+taker_up)+abs(maker_down+taker_down)
            typical=statistics.median(maker_sizes) if maker_sizes else None
            streak=0
            if abs(delta)>EPS:
                for eff in reversed(maker_effects):
                    if abs(eff)<=EPS:break
                    if (eff>0)==(delta>0):streak+=1
                    else:break
            sec_left=(end-at)/1000.0
            rows.append({
                'market_id':mid,'cohort':cohort_name,'checkpoint_ms':at,'seconds_left':sec_left,'phase':phase(sec_left),'last_parent_role':role,
                'combined_delta':delta,'maker_delta':maker_delta,'abs_combined_delta':abs(delta),'abs_maker_delta':abs(maker_delta),'maker_gross':maker_gross,'combined_gross':combined_gross,
                'imbalance_to_maker_gross':abs(delta)/maker_gross if maker_gross>EPS else None,
                'imbalance_to_combined_gross':abs(delta)/combined_gross if combined_gross>EPS else None,
                'typical_maker_parent_shares':typical,
                'imbalance_in_maker_parent_units':abs(delta)/typical if typical is not None and typical>EPS else None,
                'worsening_maker_parent_streak':streak,'prior_maker_parent_count':mp,'prior_taker_parent_count':tp,
                'ms_since_last_maker_parent':at-last_maker if last_maker is not None else None,
                'ms_since_last_taker_parent':at-last_taker if last_taker is not None else None,
                'ms_to_next_repair_burst':(next_start-at) if next_start is not None else None,
                'label_repair_onset_5s':int(next_start is not None and 0<next_start-at<=HORIZON_MS),
            })
    return rows


def score(y:np.ndarray,p:np.ndarray)->dict[str,Any]:
    return {'rows':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,
            'rocAuc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,
            'prAuc':float(average_precision_score(y,p)) if y.sum()>0 else None,
            'meanPPositive':float(p[y==1].mean()) if (y==1).any() else None,
            'meanPNegative':float(p[y==0].mean()) if (y==0).any() else None}


def main()->int:
    canonical=json.loads(CANONICAL.read_text(encoding='utf-8'))
    frozen=[int(x) for x in canonical['frozen126R2MarketIds']]; forward=set(int(x) for x in canonical['completeForwardV1MarketIds'])
    # chronological split inside frozen126; no threshold/model selection on forward or formal10
    cut=max(1,int(len(frozen)*0.8)); train_ids=set(frozen[:cut]); val_ids=set(frozen[cut:])
    registry=json.loads(REGISTRY.read_text(encoding='utf-8')); formal=set(int(x['marketId']) for x in registry.get('examMarkets',[]) if x.get('scoreStatus')=='SCORED')
    bursts=build_bursts()
    rows=build_riskset(train_ids,'TRAIN_FROZEN126',bursts)+build_riskset(val_ids,'VALIDATION_FROZEN126',bursts)+build_riskset(forward,'FORWARD23',bursts)+build_riskset(formal,'FORMAL10_OPENED_OOS',bursts)
    fields=list(rows[0].keys()) if rows else []
    with RISKSET_OUT.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    by={name:[r for r in rows if r['cohort']==name] for name in ['TRAIN_FROZEN126','VALIDATION_FROZEN126','FORWARD23','FORMAL10_OPENED_OOS']}
    Xtrain=by['TRAIN_FROZEN126']; ytrain=np.asarray([r['label_repair_onset_5s'] for r in Xtrain],int)
    numeric=np.asarray([[finite(r.get(k)) for k in NUMERIC] for r in Xtrain],float)
    # HGB inventory-only sensor: simple, no sweep.
    hgb=HistGradientBoostingClassifier(max_iter=160,max_leaf_nodes=15,learning_rate=0.05,l2_regularization=1.0,class_weight='balanced',random_state=42)
    hgb.fit(numeric,ytrain)
    # Logistic baseline includes phase/last role for sanity and interpretability.
    pre=ColumnTransformer([('num',Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler())]),NUMERIC),('cat',OneHotEncoder(handle_unknown='ignore'),CATEGORICAL)])
    logit=Pipeline([('pre',pre),('clf',LogisticRegression(max_iter=1000,class_weight='balanced',C=1.0,random_state=42))])
    logit.fit(pd.DataFrame(Xtrain),ytrain)
    evals={}
    for name,rs in by.items():
        y=np.asarray([r['label_repair_onset_5s'] for r in rs],int)
        xn=np.asarray([[finite(r.get(k)) for k in NUMERIC] for r in rs],float)
        ph=hgb.predict_proba(xn)[:,1] if len(rs) else np.asarray([])
        pl=logit.predict_proba(pd.DataFrame(rs))[:,1] if len(rs) else np.asarray([])
        evals[name]={'hgb':score(y,ph) if len(rs) else {},'logit':score(y,pl) if len(rs) else {}}
    # Fixed natural 0.5 diagnostic only; never tuned on OOS.
    formal_rows=by['FORMAL10_OPENED_OOS']; xf=np.asarray([[finite(r.get(k)) for k in NUMERIC] for r in formal_rows],float); pf=hgb.predict_proba(xf)[:,1] if formal_rows else np.asarray([])
    per_market=defaultdict(lambda:{'rows':0,'positives':0,'pred05':0,'maxP':0.0})
    for r,p in zip(formal_rows,pf):
        d=per_market[int(r['market_id'])]; d['rows']+=1; d['positives']+=int(r['label_repair_onset_5s']); d['pred05']+=int(p>=0.5); d['maxP']=max(d['maxP'],float(p))
    report={'version':'TARGET_REPAIR_AUTHORITY_HAZARD_V0','researchOnly':True,'runtimeTargetInput':False,
            'label':'next Target REPAIR_BALANCE burst onset within 5s; checkpoints during active repair bursts excluded',
            'features':NUMERIC+CATEGORICAL,'split':{'trainFrozen126Markets':len(train_ids),'validationFrozen126Markets':len(val_ids),'forwardMarkets':len(forward),'formalOpenedOosMarkets':len(formal)},
            'evaluations':evals,'formal10PerMarketDiagnostic':dict(sorted(per_market.items())),
            'guardrails':['No winner/PnL in labels or features.','Forward23 and formal10 are evaluation-only; no threshold sweep.','This is a sensor study, not action authority or graduation candidate.']}
    joblib.dump({'version':'TARGET_REPAIR_AUTHORITY_HAZARD_V0','features':NUMERIC,'hgb':hgb,'logit':logit,'report':report},MODEL_OUT)
    REPORT_OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'riskset':str(RISKSET_OUT),'model':str(MODEL_OUT),'report':str(REPORT_OUT),'evaluations':evals,'formal10PerMarket':report['formal10PerMarketDiagnostic']},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
