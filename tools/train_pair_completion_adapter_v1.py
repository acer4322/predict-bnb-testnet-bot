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

SRC = ROOT / 'data/research/execution_aware_fill_lifecycle_v0/pair_completion_curriculum_v1.jsonl'
OUT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
ART = OUT / 'pair_completion_adapter_v1.joblib'
REPORT = OUT / 'pair_completion_adapter_v1_report.json'

FEATURES = [
    'workingRecoveryExists',
    'workingRecoveryAgeMs',
    'workingRecoveryOffsetTicks',
    'absTrackingError',
    'actualCombinedGross',
    'secondsLeft',
    'recoverySpreadTicks',
    'pairAskSum',
    'lastMakerFillAgeMs',
    'directionTowardRecovery',
    'spotReturn3sTowardRecovery',
    'spotQueueTowardRecovery',
    'spotTaker1sTowardRecovery',
    'futuresReturn3sTowardRecovery',
    'futuresQueueTowardRecovery',
    'futuresTaker1sTowardRecovery',
]


def load_rows():
    rows = [json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    rows = [r for r in rows if r.get('trackingLabel') in {'REPLACE_BETTER','KEEP_BETTER'}]
    rows.sort(key=lambda r: int(r['checkpointMs']))
    return rows


def matrix(rows):
    X=[]; y=[]; w=[]
    for r in rows:
        f=r.get('features') or {}
        X.append([f.get(k) for k in FEATURES])
        y.append(1 if r['trackingLabel']=='REPLACE_BETTER' else 0)
        base=max(abs(float(r.get('baselineTargetErrorArea') or 0.0)),1.0)
        relative=min(abs(float(r.get('deltaTargetErrorArea') or 0.0))/base,1.0)
        w.append(1.0+relative)
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
        ('logit',LogisticRegression(C=0.5,max_iter=3000,solver='lbfgs')),
    ])
    model.fit(Xtr,ytr,logit__sample_weight=wtr)
    rep={
        'version':'PAIR_COMPLETION_ADAPTER_V1',
        'researchOnly':True,
        'liveTradingChanges':False,
        'actionSemantics':'REPLACE same recovery-side execution route: cancel passive child, wait venue outcome, recompute remaining target deficit, then Taker only remaining chunk if still needed. Never stack Taker on a live child for the same intent.',
        'teacherLabel':'REPLACE_BETTER iff V1 route replacement reduces HftBacktest counterfactual target-error area versus KEEP. Winner/PnL excluded from label and features.',
        'features':FEATURES,
        'directionInvariantDesign':'No UP/DOWN flag and no raw signed actual-net feature; market-flow features are aligned to recovery side.',
        'model':'median-impute + standardize + logistic regression C=0.5',
        'sampleWeight':'1 + min(abs(deltaTargetErrorArea)/max(abs(baselineTargetErrorArea),1),1)',
        'naturalThreshold':0.5,
        'dataset':{
            'rows':len(rows),'markets':len({int(r['marketId']) for r in rows}),
            'replaceBetter':sum(r['trackingLabel']=='REPLACE_BETTER' for r in rows),
            'keepBetter':sum(r['trackingLabel']=='KEEP_BETTER' for r in rows),
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
        'version':'PAIR_COMPLETION_ADAPTER_V1','researchOnly':True,
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
