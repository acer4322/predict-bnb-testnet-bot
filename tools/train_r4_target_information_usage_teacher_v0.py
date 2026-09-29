from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score
from sklearn.tree import DecisionTreeClassifier, export_text

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
OUT = ROOT / 'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v0.json'
MODEL = ROOT / 'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v0.joblib'

VERSION = 'R4_TARGET_INFORMATION_USAGE_TEACHER_V0'

LOGIC = [
    'seconds_left',
    'pre_abs_payoff_gap',
    'pre_risk_deficit',
    'pre_maker_abs_gap',
]
PREDICT = LOGIC + [
    'predict_up_mid',
    'predict_edge',
    'predict_supports_dominant',
]
STRIKE = PREDICT + [
    'strike_toward_dominant_bps',
    'spot_supports_dominant',
]
FULL = STRIKE + [
    'moderate_confidence',
    'extreme_confidence',
    'moderate_x_strike_support',
    'extreme_x_strike_support',
    'gap_x_strike_support',
]


def metric(y, p):
    return {
        'n': int(len(y)),
        'positiveRateRepair': float(np.mean(y)),
        'auc': float(roc_auc_score(y, p)),
        'ap': float(average_precision_score(y, p)),
        'logLoss': float(log_loss(y, p, labels=[0, 1])),
    }


def make_hgb():
    return HistGradientBoostingClassifier(
        learning_rate=0.055,
        max_leaf_nodes=15,
        max_depth=4,
        min_samples_leaf=30,
        l2_regularization=1.0,
        max_iter=240,
        random_state=20260827,
    )


def main():
    df = pd.read_csv(SRC)
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=['market_id', 'first_event_ms', 'is_add'] + STRIKE).copy()
    df['repair'] = 1 - df['is_add'].astype(int)
    df['moderate_confidence'] = ((df['predict_edge'] >= 0.10) & (df['predict_edge'] < 0.30)).astype(int)
    df['extreme_confidence'] = ((df['predict_edge'] >= 0.30) & (df['predict_edge'] <= 0.50)).astype(int)
    df['moderate_x_strike_support'] = df['moderate_confidence'] * df['spot_supports_dominant'].astype(int)
    df['extreme_x_strike_support'] = df['extreme_confidence'] * df['spot_supports_dominant'].astype(int)
    df['gap_x_strike_support'] = df['pre_abs_payoff_gap'] * df['spot_supports_dominant'].astype(int)

    market_time = df.groupby('market_id', as_index=False)['first_event_ms'].min().sort_values('first_event_ms')
    markets = market_time['market_id'].astype(int).tolist()
    n = len(markets)
    a = max(1, int(n * 0.70))
    b = min(n, max(a + 1, int(n * 0.85)))
    split = {'train': markets[:a], 'validation': markets[a:b], 'test': markets[b:]}
    sets = {k: set(v) for k, v in split.items()}
    parts = {k: df[df.market_id.astype(int).isin(v)].copy() for k, v in sets.items()}

    feature_sets = {
        'LOGIC_ONLY': LOGIC,
        'LOGIC_PLUS_PREDICT': PREDICT,
        'LOGIC_PLUS_PREDICT_STRIKE': STRIKE,
        'FULL_WITH_EXPLICIT_INTERACTIONS': FULL,
    }
    models = {}
    results = {}
    for name, feats in feature_sets.items():
        m = make_hgb()
        m.fit(parts['train'][feats], parts['train']['repair'])
        models[name] = m
        results[name] = {
            k: metric(part['repair'].to_numpy(), m.predict_proba(part[feats])[:, 1])
            for k, part in parts.items()
        }

    base = results['LOGIC_ONLY']
    for name in results:
        if name == 'LOGIC_ONLY':
            continue
        for split_name in ('validation', 'test'):
            results[name][split_name]['deltaAucVsLogic'] = results[name][split_name]['auc'] - base[split_name]['auc']
            results[name][split_name]['deltaApVsLogic'] = results[name][split_name]['ap'] - base[split_name]['ap']
            results[name][split_name]['logLossImprovementVsLogic'] = base[split_name]['logLoss'] - results[name][split_name]['logLoss']

    shallow_feats = FULL
    tree = DecisionTreeClassifier(
        max_depth=4,
        min_samples_leaf=80,
        class_weight='balanced',
        random_state=20260827,
    )
    tree.fit(parts['train'][shallow_feats], parts['train']['repair'])
    tree_metrics = {
        k: metric(part['repair'].to_numpy(), tree.predict_proba(part[shallow_feats])[:, 1])
        for k, part in parts.items()
    }
    importances = sorted(
        ({'feature': f, 'importance': float(v)} for f, v in zip(shallow_feats, tree.feature_importances_)),
        key=lambda x: x['importance'], reverse=True,
    )

    def conditional(rows):
        out = []
        for conf_name, mask in [
            ('LOW_LT_0.10', rows.predict_edge < 0.10),
            ('MOD_0.10_0.30', (rows.predict_edge >= 0.10) & (rows.predict_edge < 0.30)),
            ('EXT_0.30_0.50', rows.predict_edge >= 0.30),
        ]:
            z = rows[mask]
            for support in (0, 1):
                q = z[z.spot_supports_dominant.astype(int) == support]
                if len(q):
                    out.append({
                        'confidence': conf_name,
                        'spotSupportsDominant': support,
                        'n': int(len(q)),
                        'repairRate': float(q.repair.mean()),
                        'meanAbsGap': float(q.pre_abs_payoff_gap.mean()),
                        'meanSecondsLeft': float(q.seconds_left.mean()),
                    })
        return out

    artifact = {
        'version': VERSION,
        'researchOnly': True,
        'runtimePromotionAllowed': False,
        'purpose': 'Learn how Target combines information-layer evidence with strict-past portfolio logic to choose Maker Formation mode REPAIR/BUILD_WEAK_SIDE versus ADD/ALLOW_ASYMMETRY. This is a teacher/diagnostic only, not an action authority.',
        'label': {
            'positive': 'REPAIR / BUILD_WEAK_SIDE',
            'negative': 'ADD / ALLOW_ASYMMETRY',
            'source': 'observed Target Maker BID parent classified from strict-past event-level portfolio replay',
        },
        'coverage': {
            'rows': int(len(df)),
            'markets': int(df.market_id.nunique()),
            'repair': int(df.repair.sum()),
            'add': int((1-df.repair).sum()),
        },
        'split': {
            'method': 'chronological market 70/15/15; no random row split',
            'trainMarkets': split['train'],
            'validationMarkets': split['validation'],
            'testMarkets': split['test'],
        },
        'featureSets': feature_sets,
        'results': results,
        'shallowRuleTeacher': {
            'features': shallow_feats,
            'metrics': tree_metrics,
            'featureImportances': importances,
            'rules': export_text(tree, feature_names=shallow_feats, decimals=4),
        },
        'conditionalAudit': {
            'validation': conditional(parts['validation']),
            'test': conditional(parts['test']),
        },
        'guards': [
            'No winner or settlement outcome as feature.',
            'Public information is strict-past relative to Target parent onset.',
            'No threshold sweep or model hyperparameter sweep.',
            'Explicit confidence bins reuse the previously observed 0.10-0.30 moderate and >=0.30 extreme regimes; they are not tuned on this model result.',
            'The teacher predicts Formation mode only. It must not directly create orders, change owner caps, size, quote level, or bypass MPQ/responsibility.',
        ],
    }
    OUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding='utf-8')
    joblib.dump({
        'version': VERSION,
        'researchOnly': True,
        'runtimePromotionAllowed': False,
        'features': feature_sets,
        'models': models,
        'shallowTree': tree,
    }, MODEL)
    print(json.dumps({
        'artifact': str(OUT.relative_to(ROOT)).replace('\\', '/'),
        'model': str(MODEL.relative_to(ROOT)).replace('\\', '/'),
        'coverage': artifact['coverage'],
        'results': results,
        'shallowRuleTeacher': artifact['shallowRuleTeacher'],
        'testConditional': artifact['conditionalAudit']['test'],
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
