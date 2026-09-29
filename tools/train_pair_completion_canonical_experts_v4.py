from __future__ import annotations

import json, sys
from pathlib import Path
import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    roc_auc_score, average_precision_score, balanced_accuracy_score,
    precision_score, recall_score, log_loss, confusion_matrix,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = ROOT / 'data/research/execution_aware_fill_lifecycle_v0/pair_completion_tradeoff_curriculum_canonical_v3.jsonl'
OUT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
ART = OUT / 'pair_completion_canonical_experts_v4.joblib'
REPORT = OUT / 'pair_completion_canonical_experts_v4_report.json'
H = (5, 10, 20)
FROZEN_LAST = 1511912
FORWARD_FIRST = 1520549

NEED_FEATURES = [
    'workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks',
    'workingRecoveryRemainingQty','absTrackingError','actualCombinedGross',
    'secondsLeft','recoverySpreadTicks','pairAskSum','lastMakerFillAgeMs',
]
COST_BOOK_FEATURES = [
    'workingRecoveryExists','workingRecoveryAgeMs','workingRecoveryOffsetTicks',
    'workingRecoveryRemainingQty','absTrackingError','secondsLeft',
    'recoveryAsk','recoverySpreadTicks','pairAskSum',
    'marginalSurplusChunkShares','marginalSurplusChunkAvgCost',
    'recoveryTakerFeePerShare','lockedPairEdgePerShare',
]
COST_FLOW_FEATURES = COST_BOOK_FEATURES + [
    'directionTowardRecovery','spotReturn1sTowardRecovery','spotReturn3sTowardRecovery',
    'spotQueueTowardRecovery','spotTaker1sTowardRecovery',
    'futuresReturn1sTowardRecovery','futuresReturn3sTowardRecovery',
    'futuresQueueTowardRecovery','futuresTaker1sTowardRecovery',
]
AUTONOMY_FEATURES = NEED_FEATURES + [
    'recoveryAsk','marginalSurplusChunkShares','marginalSurplusChunkAvgCost',
    'recoveryTakerFeePerShare','lockedPairEdgePerShare',
]


def consensus(vals):
    neg = any(float(x) < 0 for x in vals)
    pos = any(float(x) > 0 for x in vals)
    if neg and not pos: return 'REPLACE'
    if pos and not neg: return 'KEEP'
    if not neg and not pos: return 'NEUTRAL'
    return 'MIXED'


def pipe(class_weight='balanced'):
    return Pipeline([
        ('imputer', SimpleImputer(strategy='median', add_indicator=True)),
        ('scaler', StandardScaler()),
        ('logit', LogisticRegression(C=0.5, max_iter=4000, solver='lbfgs', class_weight=class_weight)),
    ])


def arr(rows, features):
    return np.asarray([[ (r.get('features') or {}).get(k) for k in features] for r in rows], float)


def task_rows(rows, kind):
    out=[]
    for r in rows:
        if kind == 'need':
            lab = consensus([r[f'deltaTargetErrorArea{h}s'] for h in H])
        elif kind == 'cost':
            lab = consensus([r[f'deltaCompletionCost{h}s'] for h in H])
        else:
            raise ValueError(kind)
        if lab in {'REPLACE','KEEP'}:
            out.append((r, 1 if lab == 'REPLACE' else 0, lab))
    return out


def metrics(model, rows, features, y, prefix=''):
    if not rows:
        return {'n':0}
    X = arr(rows, features)
    p = model.predict_proba(X)[:,1]
    z = (p >= 0.5).astype(int)
    yy = np.asarray(y, int)
    cm = confusion_matrix(yy, z, labels=[0,1]).tolist()
    return {
        'n': int(len(yy)),
        'positiveRate': float(yy.mean()),
        'predictedPositiveRate': float(z.mean()),
        'predictedMean': float(p.mean()),
        'auc': float(roc_auc_score(yy,p)) if len(set(yy.tolist())) > 1 else None,
        'ap': float(average_precision_score(yy,p)) if len(set(yy.tolist())) > 1 else None,
        'balancedAccuracy': float(balanced_accuracy_score(yy,z)) if len(set(yy.tolist())) > 1 else None,
        'precision': float(precision_score(yy,z,zero_division=0)),
        'recall': float(recall_score(yy,z,zero_division=0)),
        'logLoss': float(log_loss(yy,p,labels=[0,1])),
        'confusionTnFpFnTp': [cm[0][0],cm[0][1],cm[1][0],cm[1][1]],
    }


def fit_task(train_rows, features, kind, class_weight='balanced'):
    pairs = task_rows(train_rows, kind)
    rr=[x[0] for x in pairs]; y=np.asarray([x[1] for x in pairs],int)
    m=pipe(class_weight=class_weight); m.fit(arr(rr,features),y)
    return m, rr, y


def eval_task(model, rows, features, kind):
    pairs=task_rows(rows,kind); rr=[x[0] for x in pairs]; y=[x[1] for x in pairs]
    return metrics(model,rr,features,y), rr, y


def gate_eval(rows, need_model, cost_model, cost_features):
    out=[]
    for r in rows:
        f=r.get('features') or {}
        pn=float(need_model.predict_proba(np.asarray([[f.get(k) for k in NEED_FEATURES]],float))[:,1][0])
        pc=float(cost_model.predict_proba(np.asarray([[f.get(k) for k in cost_features]],float))[:,1][0])
        auto=int(pn>=0.5 and pc>=0.5)
        truth=int(r.get('paretoLabel')=='REPLACE_DOMINATES')
        out.append((r,truth,auto,pn,pc))
    yy=np.asarray([x[1] for x in out],int); zz=np.asarray([x[2] for x in out],int)
    return {
        'n':len(out),'trueReplaceDominates':int(yy.sum()),'selectedAutoReplace':int(zz.sum()),
        'precision':float(precision_score(yy,zz,zero_division=0)),
        'recall':float(recall_score(yy,zz,zero_division=0)),
        'balancedAccuracy':float(balanced_accuracy_score(yy,zz)) if len(set(yy.tolist()))>1 else None,
        'selected':[{'marketId':int(x[0]['marketId']),'truth':x[0].get('paretoLabel'),'pNeed':x[3],'pCost':x[4]} for x in out if x[2]],
    }


def fit_autonomy(train_rows):
    # Conservative direct meta-skill: positive only for strict REPLACE_DOMINATES.
    # No class balancing: preserve rarity so natural 0.5 boundary remains conservative.
    y=np.asarray([1 if r.get('paretoLabel')=='REPLACE_DOMINATES' else 0 for r in train_rows],int)
    m=pipe(class_weight=None); m.fit(arr(train_rows,AUTONOMY_FEATURES),y)
    return m,y


def eval_autonomy(model, rows):
    y=[1 if r.get('paretoLabel')=='REPLACE_DOMINATES' else 0 for r in rows]
    return metrics(model,rows,AUTONOMY_FEATURES,y)


def main():
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    rows.sort(key=lambda r:int(r['checkpointMs']))
    frozen=[r for r in rows if int(r['marketId']) <= FROZEN_LAST]
    forward=[r for r in rows if int(r['marketId']) >= FORWARD_FIRST]
    if len(frozen)!=126 or len(forward)!=23:
        raise RuntimeError(f'canonical split mismatch frozen={len(frozen)} forward={len(forward)}')
    cut=int(len(frozen)*0.80)
    tr=frozen[:cut]; va=frozen[cut:]

    need, need_tr, y_need_tr = fit_task(tr, NEED_FEATURES, 'need')
    cb, cb_tr, y_cb_tr = fit_task(tr, COST_BOOK_FEATURES, 'cost')
    cf, cf_tr, y_cf_tr = fit_task(tr, COST_FLOW_FEATURES, 'cost')
    auto, y_auto_tr = fit_autonomy(tr)

    need_rep={
        'train':metrics(need,need_tr,NEED_FEATURES,y_need_tr),
        'validation':eval_task(need,va,NEED_FEATURES,'need')[0],
        'forwardOos':eval_task(need,forward,NEED_FEATURES,'need')[0],
    }
    cb_rep={
        'train':metrics(cb,cb_tr,COST_BOOK_FEATURES,y_cb_tr),
        'validation':eval_task(cb,va,COST_BOOK_FEATURES,'cost')[0],
        'forwardOos':eval_task(cb,forward,COST_BOOK_FEATURES,'cost')[0],
    }
    cf_rep={
        'train':metrics(cf,cf_tr,COST_FLOW_FEATURES,y_cf_tr),
        'validation':eval_task(cf,va,COST_FLOW_FEATURES,'cost')[0],
        'forwardOos':eval_task(cf,forward,COST_FLOW_FEATURES,'cost')[0],
    }
    auto_rep={
        'train':metrics(auto,tr,AUTONOMY_FEATURES,y_auto_tr),
        'validation':eval_autonomy(auto,va),
        'forwardOos':eval_autonomy(auto,forward),
    }

    gates={
        'bookkeepingCost':{
            'validation':gate_eval(va,need,cb,COST_BOOK_FEATURES),
            'forwardOos':gate_eval(forward,need,cb,COST_BOOK_FEATURES),
        },
        'flowCost':{
            'validation':gate_eval(va,need,cf,COST_FLOW_FEATURES),
            'forwardOos':gate_eval(forward,need,cf,COST_FLOW_FEATURES),
        },
    }

    def counts(rs):
        d={}
        for r in rs: d[r.get('paretoLabel')]=d.get(r.get('paretoLabel'),0)+1
        return d

    rep={
        'version':'PAIR_COMPLETION_CANONICAL_EXPERTS_V4',
        'researchOnly':True,'liveTradingChanges':False,'dreamFillAllowed':False,
        'teacher':'Canonical V3 Pareto execution teacher: local 5/10/20s target tracking plus same-intent completion cost. Winner/PnL excluded.',
        'cohort':{
            'all':len(rows),'frozen126':len(frozen),'completeForwardV1':len(forward),
            'trainFrozen':len(tr),'validationFrozen':len(va),
            'trainCounts':counts(tr),'validationCounts':counts(va),'forwardCounts':counts(forward),
            'trainMaxCheckpointMs':max(int(r['checkpointMs']) for r in tr),
            'validationMinCheckpointMs':min(int(r['checkpointMs']) for r in va),
            'forwardMinCheckpointMs':min(int(r['checkpointMs']) for r in forward),
        },
        'needExpert':{'features':NEED_FEATURES,**need_rep},
        'costBookkeepingExpert':{'features':COST_BOOK_FEATURES,**cb_rep},
        'costFlowExpertDiagnostic':{'features':COST_FLOW_FEATURES,**cf_rep},
        'directAutonomyExpertDiagnostic':{
            'semantics':'Predict strict REPLACE_DOMINATES versus every other state. Natural class prevalence, no class balancing.',
            'features':AUTONOMY_FEATURES,**auto_rep,
        },
        'jointGates':gates,
        'guardrails':[
            'Frozen126 only for train/validation; COMPLETE_FORWARD_V1 23 markets is cross-generation OOS.',
            'No threshold sweep; natural 0.5 only.',
            'No winner, settlement PnL, Target runtime state, or future-fill runtime feature.',
            'TRADEOFF and NEUTRAL are never forced into REPLACE/KEEP teacher labels for the subexperts.',
            'Cost-flow model is diagnostic; bookkeeping model is preferred if performance is comparable because it avoids short-alpha dependence.',
        ],
    }
    bundle={
        'version':'PAIR_COMPLETION_CANONICAL_EXPERTS_V4','researchOnly':True,
        'needModel':need,'needFeatures':NEED_FEATURES,
        'costBookkeepingModel':cb,'costBookkeepingFeatures':COST_BOOK_FEATURES,
        'costFlowModelDiagnostic':cf,'costFlowFeatures':COST_FLOW_FEATURES,
        'autonomyModelDiagnostic':auto,'autonomyFeatures':AUTONOMY_FEATURES,
        'threshold':0.5,
        'trainingMarketIds':[int(r['marketId']) for r in tr],
        'validationMarketIds':[int(r['marketId']) for r in va],
        'forwardOosMarketIds':[int(r['marketId']) for r in forward],
    }
    joblib.dump(bundle,ART)
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'cohort':rep['cohort'],'need':need_rep,'costBook':cb_rep,'costFlow':cf_rep,'autonomy':auto_rep,'gates':gates},ensure_ascii=False))

if __name__=='__main__': main()
