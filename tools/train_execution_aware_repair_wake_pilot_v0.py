from __future__ import annotations

import argparse, json, math, statistics, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import accuracy_score, balanced_accuracy_score, roc_auc_score

SRC=ROOT/'data'/'research'/'hftbacktest_execution_shift_v0'
OUT=ROOT/'data'/'research'/'execution_aware_repair_wake_v0'
SEED=20260821

# Deliberately tiny own-state/public/frozen-pressure feature set. No winner/future PnL inputs.
FEATURES=[
    'risk_age_ms','seconds_left','maker_gross','maker_net','maker_abs_net','maker_paired_coverage',
    'worst_case_floor','pTaker1s','pTaker3s','pMakerUpBase','pMakerDownBase','pMakerUp','pMakerDown',
    'up_bid','up_ask','down_bid','down_ask','last_maker_age_ms','maker_fills_5s','maker_shares_10s',
]

def num(v:Any)->float:
    try:
        x=float(v)
        return x if math.isfinite(x) else np.nan
    except Exception: return np.nan

def flatten(row:dict[str,Any])->dict[str,Any]:
    c=row.get('candidate') or {}; p=c.get('portfolio') or {}; m=c.get('models') or {}; d=c.get('direction') or {}
    z={'marketId':int(row['marketId']),'label':1 if row.get('label')=='BENEFICIAL' else 0,'deltaUsdt':num(row.get('deltaUsdt')),
       'risk_age_ms':num(c.get('riskAgeMs')),'seconds_left':num(p.get('seconds_left'))}
    aliases={
        'maker_gross':'maker_gross','maker_net':'maker_net','maker_abs_net':'maker_abs_net','maker_paired_coverage':'maker_paired_coverage',
        'worst_case_floor':'worst_case_floor','last_maker_age_ms':'last_maker_age_ms','maker_fills_5s':'maker_fills_5s','maker_shares_10s':'maker_shares_10s',
    }
    for k,s in aliases.items(): z[k]=num(p.get(s))
    for k in ['pTaker1s','pTaker3s','pMakerUpBase','pMakerDownBase','pMakerUp','pMakerDown']: z[k]=num(m.get(k))
    # direction/public field names vary slightly; preserve only values actually present.
    for k in ['up_bid','up_ask','down_bid','down_ask']:
        z[k]=num(d.get(k) if k in d else d.get({'up_bid':'upBid','up_ask':'upAsk','down_bid':'downBid','down_ask':'downAsk'}[k]))
    return z

def load(pattern:str)->pd.DataFrame:
    rows=[]
    for p in sorted(SRC.glob(pattern)):
        j=json.loads(p.read_text(encoding='utf-8'))
        for r in j.get('rows') or []:
            if r.get('forcedWakeApplied') and r.get('label') in {'BENEFICIAL','HARMFUL','NEUTRAL'} and r.get('candidate'):
                # Neutral is treated as no-wake for the pilot; >$1 benefit is positive teacher label.
                rows.append(flatten(r))
    if not rows: raise RuntimeError('no valid teacher rows')
    return pd.DataFrame(rows).drop_duplicates(['marketId','risk_age_ms'],keep='last').sort_values(['marketId','risk_age_ms']).reset_index(drop=True)

def metrics(y,p,thr=.5):
    y=np.asarray(y,dtype=int); p=np.asarray(p,dtype=float); pred=(p>=thr).astype(int)
    return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()),'predMean':float(p.mean()),
            'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(set(y.tolist()))>1 else None,
            'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--pattern',default='repair_wake_teacher_*_v0.json'); ap.add_argument('--train-markets',type=int,default=14); args=ap.parse_args()
    d=load(args.pattern); mids=sorted(d.marketId.unique().tolist()); cut=min(args.train_markets,max(1,len(mids)-3)); tr=set(mids[:cut]); te=set(mids[cut:]); train=d[d.marketId.isin(tr)].copy(); test=d[d.marketId.isin(te)].copy()
    if train.label.nunique()<2: raise RuntimeError('training split has only one class')
    model=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=16,max_interaction_bins=8,interactions=2,outer_bags=2,learning_rate=.04,max_rounds=500,early_stopping_rounds=40,min_samples_leaf=2,n_jobs=-2,random_state=SEED)
    Xtr=train[FEATURES].apply(pd.to_numeric,errors='coerce'); model.fit(Xtr,train.label.astype(int))
    OUT.mkdir(parents=True,exist_ok=True); art=OUT/'execution_aware_repair_wake_ebm_pilot_v0.joblib'; joblib.dump({'version':'EXECUTION_AWARE_REPAIR_WAKE_EBM_PILOT_V0','model':model,'features':FEATURES,'trainingMarkets':sorted(tr),'researchOnly':True},art)
    res={}
    for name,x in [('train',train),('holdout',test)]:
        if len(x): res[name]=metrics(x.label.astype(int),model.predict_proba(x[FEATURES].apply(pd.to_numeric,errors='coerce'))[:,1])
    imp=list(model.term_importances()); names=list(model.term_names_); order=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:12]
    report={'version':'EXECUTION_AWARE_REPAIR_WAKE_EBM_PILOT_V0','researchOnly':True,'dataset':{'rows':int(len(d)),'markets':int(d.marketId.nunique()),'positiveRate':float(d.label.mean())},'split':{'trainMarkets':len(tr),'holdoutMarkets':len(te),'trainIds':sorted(tr),'holdoutIds':sorted(te)},'results':res,'topTerms':[{'term':str(names[i]),'importance':float(imp[i])} for i in order],'artifact':str(art),'boundary':'Tiny pilot only. Labels are counterfactual end-PnL benefit (>+1 USDT) of one realistic-state RESIDUAL wake. No winner/future PnL feature is used. Do not promote to runtime from this report.'}
    (OUT/'execution_aware_repair_wake_ebm_pilot_v0_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__': main()
