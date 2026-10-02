from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

from sklearn.compose import TransformedTargetRegressor
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    log_loss,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

EPS = 1e-9
SEED = 20260907

# Strict-past / decision-time only. Explicitly exclude any baseline/candidate
# post-decision execution outcome, priority-loss delay, terminal state, winner,
# terminal PnL, future Target action, or market identity.
FEATURES = [
    'ageAtDecisionMs',
    'price',
    'qty',
    'rank',
    'depth',
    'bestPrice',
    'bestDepth',
    'distanceTicks',
    'pairSum',
    'floor',
    'best',
    'gap',
    'upQty',
    'downQty',
    'cost',
    'scopeDebtQty',
    'reservedRepairQuota',
    'availableExpandRiskCredit',
    'scopeRepairProgressClocks',
    'livePassiveSlots',
    'liveActiveSlots',
    'repairQtyAuthorized',
    'overflowQtyAuthorized',
]

REGRESSION_TARGETS = [
    'dBest5s', 'dFloor5s', 'dGap5s',
    'dBest10s', 'dFloor10s', 'dGap10s',
    'dActiveQtyTerminal',
]


def _finite(v):
    if v is None:
        return np.nan
    try:
        x = float(v)
        return x if math.isfinite(x) else np.nan
    except Exception:
        return np.nan


def _matrix(rows):
    return np.asarray([[_finite(r.get(f)) for f in FEATURES] for r in rows], dtype=float)


def _reg_models():
    return {
        'RIDGE_FIXED': Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('scale', StandardScaler()),
            ('model', Ridge(alpha=1.0)),
        ]),
        'EXTRATREES_FIXED': Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('model', ExtraTreesRegressor(
                n_estimators=400,
                max_depth=4,
                min_samples_leaf=4,
                max_features='sqrt',
                random_state=SEED,
                n_jobs=4,
            )),
        ]),
    }


def _clf_models():
    return {
        'LOGISTIC_FIXED': Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('scale', StandardScaler()),
            ('model', LogisticRegression(C=1.0, max_iter=2000, random_state=SEED)),
        ]),
        'EXTRATREES_CLF_FIXED': Pipeline([
            ('imputer', SimpleImputer(strategy='median')),
            ('model', ExtraTreesClassifier(
                n_estimators=400,
                max_depth=4,
                min_samples_leaf=4,
                max_features='sqrt',
                random_state=SEED,
                n_jobs=4,
            )),
        ]),
    }


def _macro_mae(y, p, groups):
    vals = []
    for g in sorted(set(groups)):
        ii = np.where(groups == g)[0]
        vals.append(mean_absolute_error(y[ii], p[ii]))
    return float(np.mean(vals)), {str(int(g)): float(mean_absolute_error(y[np.where(groups == g)[0]], p[np.where(groups == g)[0]])) for g in sorted(set(groups))}


def _sign_accuracy_nonzero(y, p):
    ii = np.where(np.abs(y) > EPS)[0]
    if len(ii) == 0:
        return None
    return float(np.mean(np.sign(y[ii]) == np.sign(p[ii])))


def _reg_summary(y, pred, groups):
    macro, by_market = _macro_mae(y, pred, groups)
    return {
        'mae': float(mean_absolute_error(y, pred)),
        'rmse': float(mean_squared_error(y, pred) ** 0.5),
        'r2': float(r2_score(y, pred)) if np.unique(y).size > 1 else None,
        'marketMacroMae': macro,
        'marketMae': by_market,
        'directionalAccuracyNonZero': _sign_accuracy_nonzero(y, pred),
    }


def _folds(groups):
    for g in sorted(set(groups)):
        te = np.where(groups == g)[0]
        tr = np.where(groups != g)[0]
        yield int(g), tr, te


def regression_audit(rows, X, groups):
    results = {}
    for target in REGRESSION_TARGETS:
        y = np.asarray([float(r.get(target) or 0.0) for r in rows], dtype=float)
        models = _reg_models()
        preds = {'TRAIN_MEAN': np.zeros(len(rows), dtype=float)}
        fold_importance = defaultdict(list)
        fold_rows = []
        for g, tr, te in _folds(groups):
            mu = float(np.mean(y[tr]))
            preds['TRAIN_MEAN'][te] = mu
            fold = {'heldMarket': g, 'trainN': int(len(tr)), 'testN': int(len(te)), 'trainMean': mu}
            for name, model in models.items():
                model.fit(X[tr], y[tr])
                pp = model.predict(X[te])
                if name not in preds:
                    preds[name] = np.zeros(len(rows), dtype=float)
                preds[name][te] = pp
                try:
                    if name == 'RIDGE_FIXED':
                        imp = np.abs(model.named_steps['model'].coef_)
                    else:
                        imp = model.named_steps['model'].feature_importances_
                    for f, v in zip(FEATURES, imp):
                        fold_importance[(name, f)].append(float(v))
                except Exception:
                    pass
            fold_rows.append(fold)
        summaries = {name: _reg_summary(y, pred, groups) for name, pred in preds.items()}
        base_macro = summaries['TRAIN_MEAN']['marketMacroMae']
        base_mae = summaries['TRAIN_MEAN']['mae']
        for name in list(summaries):
            summaries[name]['maeImprovementVsTrainMean'] = float(base_mae - summaries[name]['mae'])
            summaries[name]['marketMacroMaeImprovementVsTrainMean'] = float(base_macro - summaries[name]['marketMacroMae'])
            if name != 'TRAIN_MEAN':
                wins = 0
                ties = 0
                for g in sorted(set(groups)):
                    b = summaries['TRAIN_MEAN']['marketMae'][str(int(g))]
                    m = summaries[name]['marketMae'][str(int(g))]
                    if m < b - 1e-12:
                        wins += 1
                    elif abs(m - b) <= 1e-12:
                        ties += 1
                summaries[name]['heldMarketMaeWinsVsTrainMean'] = wins
                summaries[name]['heldMarketMaeTiesVsTrainMean'] = ties
                summaries[name]['heldMarketCount'] = len(set(groups))
        avg_imp = {}
        for name in models:
            vals = [(f, float(np.mean(fold_importance[(name, f)]))) for f in FEATURES if fold_importance.get((name, f))]
            vals.sort(key=lambda z: z[1], reverse=True)
            avg_imp[name] = [{'feature': f, 'meanFoldImportance': v} for f, v in vals]
        results[target] = {
            'targetStats': {
                'mean': float(np.mean(y)), 'median': float(np.median(y)),
                'positive': int(np.sum(y > EPS)), 'negative': int(np.sum(y < -EPS)), 'zero': int(np.sum(np.abs(y) <= EPS)),
            },
            'models': summaries,
            'meanFoldFeatureImportance': avg_imp,
            'oofPredictions': [
                {'marketId': int(rows[i]['marketId']), 'key': rows[i]['key'], 'y': float(y[i]), **{name: float(preds[name][i]) for name in preds}}
                for i in range(len(rows))
            ],
        }
    return results


def _binary_metrics(y, p, groups):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    pred = (p >= 0.5).astype(int)
    out = {
        'brier': float(brier_score_loss(y, p)),
        'logLoss': float(log_loss(y, np.c_[1 - p, p], labels=[0, 1])),
        'accuracy': float(accuracy_score(y, pred)),
        'balancedAccuracy': float(balanced_accuracy_score(y, pred)),
        'auc': float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
    }
    market_brier = {}
    for g in sorted(set(groups)):
        ii = np.where(groups == g)[0]
        market_brier[str(int(g))] = float(np.mean((p[ii] - y[ii]) ** 2))
    out['marketMacroBrier'] = float(np.mean(list(market_brier.values())))
    out['marketBrier'] = market_brier
    return out


def classification_audit(rows, X, groups):
    labels = {
        'ANY_PHYSICAL_EFFECT_10S': np.asarray([
            1 if abs(float(r.get('dBest10s') or 0.0)) > EPS or abs(float(r.get('dFloor10s') or 0.0)) > EPS else 0 for r in rows
        ], dtype=int),
        'FLOOR_IMPROVES_10S': np.asarray([1 if float(r.get('dFloor10s') or 0.0) > EPS else 0 for r in rows], dtype=int),
        'BEST_IMPROVES_10S': np.asarray([1 if float(r.get('dBest10s') or 0.0) > EPS else 0 for r in rows], dtype=int),
    }
    results = {}
    for lname, y in labels.items():
        preds = {'TRAIN_PREVALENCE': np.zeros(len(rows), dtype=float)}
        models = _clf_models()
        failed_folds = defaultdict(list)
        for g, tr, te in _folds(groups):
            prevalence = float(np.mean(y[tr]))
            preds['TRAIN_PREVALENCE'][te] = prevalence
            for name, model in models.items():
                if len(np.unique(y[tr])) < 2:
                    preds.setdefault(name, np.zeros(len(rows), dtype=float))[te] = prevalence
                    failed_folds[name].append({'heldMarket': g, 'reason': 'single_class_train'})
                    continue
                model.fit(X[tr], y[tr])
                pp = model.predict_proba(X[te])[:, 1]
                preds.setdefault(name, np.zeros(len(rows), dtype=float))[te] = pp
        metrics = {name: _binary_metrics(y, p, groups) for name, p in preds.items()}
        base = metrics['TRAIN_PREVALENCE']['marketMacroBrier']
        for name in metrics:
            metrics[name]['marketMacroBrierImprovementVsPrevalence'] = float(base - metrics[name]['marketMacroBrier'])
            if name != 'TRAIN_PREVALENCE':
                metrics[name]['failedFolds'] = failed_folds.get(name, [])
        results[lname] = {
            'labelStats': {'positive': int(np.sum(y)), 'negative': int(len(y) - np.sum(y)), 'prevalence': float(np.mean(y))},
            'models': metrics,
            'oofPredictions': [
                {'marketId': int(rows[i]['marketId']), 'key': rows[i]['key'], 'y': int(y[i]), **{name: float(preds[name][i]) for name in preds}}
                for i in range(len(rows))
            ],
        }
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()

    d = json.loads(Path(a.dataset).read_text(encoding='utf-8'))
    rows = d['rows']
    groups = np.asarray([int(r['marketId']) for r in rows], dtype=int)
    X = _matrix(rows)

    # Hard leakage audit: document excluded post-decision fields present in source.
    source_fields = sorted({k for r in rows for k in r.keys()})
    excluded_post = [k for k in source_fields if k not in FEATURES and k not in REGRESSION_TARGETS and k not in {'marketId', 'key'}]
    missing_features = {f: int(sum(1 for r in rows if r.get(f) is None)) for f in FEATURES}

    reg = regression_audit(rows, X, groups)
    clf = classification_audit(rows, X, groups)

    # Conservative gate: only call a target predictably structured when a fixed model
    # beats the train-mean baseline on BOTH row-weighted and market-macro MAE, and wins
    # at least half held markets. This gate is research-only and not a runtime threshold.
    target_verdicts = {}
    for t, z in reg.items():
        verdict = 'NO_GROUPED_GENERALIZATION'
        winners = []
        for name in ('RIDGE_FIXED', 'EXTRATREES_FIXED'):
            m = z['models'][name]
            if m['maeImprovementVsTrainMean'] > 0 and m['marketMacroMaeImprovementVsTrainMean'] > 0 and m.get('heldMarketMaeWinsVsTrainMean', 0) >= 9:
                winners.append(name)
        if winners:
            verdict = 'GROUPED_PREDICTABILITY_SIGNAL'
        target_verdicts[t] = {'verdict': verdict, 'qualifyingModels': winners}

    out = {
        'version': 'LANE_G_QOV_GROUPED_ACTION_VALUE_PREDICTABILITY_AUDIT_V1_20260907',
        'researchOnly': True,
        'runtimeAuthority': False,
        'dataset': str(a.dataset),
        'nRows': len(rows),
        'nMarkets': len(set(groups)),
        'grouping': 'LEAVE_ONE_MARKET_OUT_ONLY',
        'features': FEATURES,
        'featureMissingCounts': missing_features,
        'excludedSourceFields': excluded_post,
        'regressionTargets': REGRESSION_TARGETS,
        'regression': reg,
        'classification': clf,
        'targetVerdicts': target_verdicts,
        'boundary': [
            'all 67 fixed consumed decision forks used; no cohort substitution',
            'marketId used only as grouping key, never as feature',
            'strict-past decision-time features only',
            'baseline/candidate post-decision outcomes excluded from features',
            'winner/terminal PnL/Target future action absent from features',
            'fixed model hyperparameters; no label-driven tuning',
            'Leave-One-Market-Out OOF only; no random row split',
            'Delta Best / Delta Floor / Delta Active quantity evaluated separately',
            'no fresh data, no runtime rule promotion, no 8781',
        ],
    }

    op = (Path(os.environ['BTC5M_LAN_RESULT_DIR']) / 'result.json') if str(a.output).upper() == 'AUTO' else Path(a.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')

    compact = {
        'ok': True,
        'nRows': out['nRows'], 'nMarkets': out['nMarkets'],
        'targetVerdicts': out['targetVerdicts'],
        'regression': {
            t: {name: {k: v for k, v in z['models'][name].items() if k in ('mae', 'marketMacroMae', 'maeImprovementVsTrainMean', 'marketMacroMaeImprovementVsTrainMean', 'heldMarketMaeWinsVsTrainMean', 'directionalAccuracyNonZero')}
                for name in ('TRAIN_MEAN', 'RIDGE_FIXED', 'EXTRATREES_FIXED')}
            for t, z in reg.items()
        },
        'classification': {
            t: {name: {k: v for k, v in z['models'][name].items() if k in ('brier', 'marketMacroBrier', 'marketMacroBrierImprovementVsPrevalence', 'auc', 'balancedAccuracy')}
                for name in ('TRAIN_PREVALENCE', 'LOGISTIC_FIXED', 'EXTRATREES_CLF_FIXED')}
            for t, z in clf.items()
        },
    }
    print(json.dumps(compact, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
