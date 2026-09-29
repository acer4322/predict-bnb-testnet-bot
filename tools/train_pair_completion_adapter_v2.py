from __future__ import annotations

import json
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, balanced_accuracy_score, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = ROOT / 'data/research/execution_aware_fill_lifecycle_v0/pair_completion_curriculum_v2.jsonl'
OUT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
ART = OUT / 'pair_completion_adapter_v2.joblib'
REPORT = OUT / 'pair_completion_adapter_v2_report.json'

# Intentionally execution-only. No raw UP/DOWN side, winner, PnL, direction score,
# returns, queue imbalance, taker imbalance, or future fill features.
FEATURES = [
    'workingRecoveryExists',
    'workingRecoveryAgeMs',
    'workingRecoveryOffsetTicks',
    'workingRecoveryRemainingQty',
    'absTrackingError',
    'actualCombinedGross',
    'secondsLeft',
    'recoverySpreadTicks',
    'pairAskSum',
    'lastMakerFillAgeMs',
]


def load_rows():
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    rows=[r for r in rows if r.get('trackingLabel') in {'REPLACE_BETTER','KEEP_BETTER'}]
    rows.sort(key=lambda r:int(r['checkpointMs']))
    return rows


def matrix(rows):
    X=[]; y=[]; w=[]
    for r in rows:
        f=r.get('features') or {}
        X.append([f.get(k) for k in FEATURES])
        y.append(1 if r['trackingLabel']=='REPLACE_BETTER' else 0)
        rel=[]
        for h in (5,10,20):
            d=abs(float(r.get(f'deltaTargetErrorArea{h}s') or 0.0))
            b=max(abs(float(r.get(f'baselineTargetErrorArea{h}s') or 0.0)),1.0)
            rel.append(min(d/b,1.0))
        # Effect-size weighting only; horizons were frozen before labels were generated.
        w.append(1.0+float(np.median(rel)))
    return np.asarray(X,dtype=float),np.asarray(y,dtype=int),np.asarray(w,dtype=float)


def metrics(model,X,y):
    p=model.predict_proba(X)[:,1]
    pred=(p>=0.5).astype(int)
    return {
        'n':int(len(y)),
        'positiveRate':float(y.mean()) if len(y) else None,
        'predictedPositiveRate':float(pred.mean()) if len(y) else None,
        'predictedMean':float(p.mean()) if len(y) else None,
        'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,
        'ap':float(average_precision_score(y,p)) if len(set(y.tolist()))>1 else None,
        'balancedAccuracy':float(balanced_accuracy_score(y,pred)) if len(set(y.tolist()))>1 else None,
        'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,
    }


def main():
    rows=load_rows()
    if len(rows)<30:
        raise RuntimeError(f'too few rows: {len(rows)}')
    cut=max(1,int(len(rows)*0.70))
    train=rows[:cut]; val=rows[cut:]
    Xtr,ytr,wtr=matrix(train); Xv,yv,_=matrix(val)
    model=Pipeline([
        ('imputer',SimpleImputer(strategy='median',add_indicator=True)),
        ('scaler',StandardScaler()),
        ('logit',LogisticRegression(C=0.5,max_iter=3000,solver='lbfgs',class_weight='balanced')),
    ])
    model.fit(Xtr,ytr,logit__sample_weight=wtr)
    rep={
        'version':'PAIR_COMPLETION_ADAPTER_V2',
        'researchOnly':True,
        'liveTradingChanges':False,
        'actionSemantics':'KEEP current passive recovery-side child versus REPLACE same execution route: cancel passive child, wait venue outcome, recompute remaining target deficit, then Taker only remaining chunk if still needed. If no passive child exists, REPLACE means establish active Taker route for the currently missing chunk.',
        'teacherLabel':'LOCAL multi-horizon execution teacher. REPLACE_BETTER iff 5s/10s/20s HftBacktest target-error-area deltas are all non-positive with at least one strictly negative; KEEP_BETTER iff all are non-negative with at least one strictly positive. Mixed signs are AMBIGUOUS and zeros are NEUTRAL; both excluded from fit. Winner/PnL/full-market target path excluded from labels and features.',
        'features':FEATURES,
        'model':'median-impute + standardize + logistic regression C=0.5 + balanced class weights',
        'sampleWeight':'1 + median_h(min(abs(deltaTargetErrorArea_h)/max(abs(baselineTargetErrorArea_h),1),1)), h in {5,10,20}',
        'naturalThreshold':0.5,
        'dataset':{
            'rows':len(rows),'markets':len({int(r['marketId']) for r in rows}),
            'replaceBetter':sum(r['trackingLabel']=='REPLACE_BETTER' for r in rows),
            'keepBetter':sum(r['trackingLabel']=='KEEP_BETTER' for r in rows),
            'excludedAmbiguousOrNeutral':57-len(rows),
        },
        'split':{
            'trainRows':len(train),'validationRows':len(val),
            'trainMaxCheckpointMs':max(int(r['checkpointMs']) for r in train),
            'validationMinCheckpointMs':min(int(r['checkpointMs']) for r in val),
            'trainMarketIds':[int(r['marketId']) for r in train],
            'validationMarketIds':[int(r['marketId']) for r in val],
        },
        'metrics':{'train':metrics(model,Xtr,ytr),'validation':metrics(model,Xv,yv)},
    }
    bundle={
        'version':'PAIR_COMPLETION_ADAPTER_V2','researchOnly':True,
        'features':FEATURES,'model':model,'threshold':0.5,
        'trainingMaxCheckpointMs':rep['split']['trainMaxCheckpointMs'],
        'trainingMarkets':rep['split']['trainMarketIds'],
        'validationMarkets':rep['split']['validationMarketIds'],
        'actionSemantics':rep['actionSemantics'],'teacherSemantics':rep['teacherLabel'],
    }
    joblib.dump(bundle,ART)
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'dataset':rep['dataset'],'metrics':rep['metrics']},ensure_ascii=False))

if __name__=='__main__':
    main()
