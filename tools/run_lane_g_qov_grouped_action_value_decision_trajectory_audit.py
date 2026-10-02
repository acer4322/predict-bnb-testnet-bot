from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / 'run_lane_g_qov_grouped_action_value_predictability_audit.py'
spec = importlib.util.spec_from_file_location('lane_g_qov_base_audit', BASE_PATH)
if spec is None or spec.loader is None:
    raise ImportError(BASE_PATH)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

SUBMIT_FEATURES = list(base.FEATURES)

DECISION_FEATURES = [
    'dt_orderQty',
    'dt_cumQtyAtDecision',
    'dt_remainingQtyAtDecision',
    'dt_ageAtDecisionMs',
    'dt_currentBestPrice',
    'dt_currentSecondPrice',
    'dt_currentBestDepth',
    'dt_currentSecondDepth',
    'dt_currentGapTicks',
    'dt_replacementPrice',
    'dt_replacementQty',
    'dt_replacementVsOwnTicks',
    'dt_replacementPairSum',
    'dt_replacementFloorDeltaIfFilled',
    'dt_physicalFloorAtDecision',
    'dt_physicalBestAtDecision',
    'dt_physicalGapAtDecision',
    'dt_scopeDebtQtyAtDecision',
    'dt_reservedRepairQuotaAtDecision',
    'dt_ownRepairQuotaRemaining',
    'dt_reservedRepairQuotaWithoutOwn',
    'dt_unreservedDebtIfOwnReleased',
    'dt_availableExpandRiskCreditAtDecision',
    'dt_scopeRepairProgressClocksAtDecision',
    'dt_livePassiveSlotsAtDecision',
    'dt_liveActiveSlotsAtDecision',
    'dt_sameSideLiveCountAtDecision',
    'dt_oppositeUnmatchedAvgAtDecision',
]
for w in (500, 1000, 2000):
    for name in (
        'bestDelta', 'bestRange', 'depthDelta', 'depthRange', 'bestMoveCount',
        'gapNowTicks', 'gapStartTicks', 'gapDeltaTicks', 'imbDelta', 'levelCountDelta',
    ):
        DECISION_FEATURES.append(f'dt_w{w}_{name}')

FEATURE_SETS = {
    'SUBMIT_STATE': SUBMIT_FEATURES,
    'DECISION_TRAJECTORY': DECISION_FEATURES,
    'SUBMIT_PLUS_DECISION': list(dict.fromkeys(SUBMIT_FEATURES + DECISION_FEATURES)),
}


def target_verdicts(reg):
    out = {}
    for t, z in reg.items():
        winners = []
        for name in ('RIDGE_FIXED', 'EXTRATREES_FIXED'):
            m = z['models'][name]
            if (
                m['maeImprovementVsTrainMean'] > 0
                and m['marketMacroMaeImprovementVsTrainMean'] > 0
                and m.get('heldMarketMaeWinsVsTrainMean', 0) >= 9
            ):
                winners.append(name)
        out[t] = {
            'verdict': 'GROUPED_PREDICTABILITY_SIGNAL' if winners else 'NO_GROUPED_GENERALIZATION',
            'qualifyingModels': winners,
        }
    return out


def compact_metrics(reg):
    out = {}
    for t, z in reg.items():
        out[t] = {}
        for name in ('TRAIN_MEAN', 'RIDGE_FIXED', 'EXTRATREES_FIXED'):
            m = z['models'][name]
            out[t][name] = {k: m.get(k) for k in (
                'mae', 'marketMacroMae', 'maeImprovementVsTrainMean',
                'marketMacroMaeImprovementVsTrainMean', 'heldMarketMaeWinsVsTrainMean',
                'directionalAccuracyNonZero',
            ) if k in m}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dataset', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    d = json.loads(Path(a.dataset).read_text(encoding='utf-8'))
    rows = d['rows']
    groups = np.asarray([int(r['marketId']) for r in rows], dtype=int)
    all_results = {}
    for set_name, features in FEATURE_SETS.items():
        base.FEATURES = list(features)
        X = base._matrix(rows)
        missing = {f: int(sum(1 for r in rows if r.get(f) is None)) for f in features}
        reg = base.regression_audit(rows, X, groups)
        clf = base.classification_audit(rows, X, groups)
        verdicts = target_verdicts(reg)
        all_results[set_name] = {
            'features': list(features),
            'featureCount': len(features),
            'featureMissingCounts': missing,
            'regression': reg,
            'classification': clf,
            'targetVerdicts': verdicts,
        }
        print(json.dumps({
            'featureSet': set_name,
            'featureCount': len(features),
            'targetVerdicts': verdicts,
            'regression': compact_metrics(reg),
        }, ensure_ascii=False), flush=True)

    out = {
        'version': 'LANE_G_QOV_GROUPED_ACTION_VALUE_DECISION_TRAJECTORY_AUDIT_V1_20260907',
        'researchOnly': True,
        'runtimeAuthority': False,
        'dataset': str(a.dataset),
        'nRows': len(rows),
        'nMarkets': len(set(groups)),
        'grouping': 'LEAVE_ONE_MARKET_OUT_ONLY',
        'featureSets': all_results,
        'boundary': [
            'same fixed 67 causal decisions and exact fork labels',
            'no cohort replacement and no fresh data',
            'SUBMIT_STATE reproduces prior strict-past submit features',
            'DECISION_TRAJECTORY uses only state observable at successful native reanchor decision plus trailing 0.5/1/2s strict-past book trajectory',
            'post-decision fill/cancel/priority-loss/winner/PnL/Target future action excluded from features',
            'fixed Ridge/ExtraTrees specs inherited from V1; no hyperparameter tuning',
            'Leave-One-Market-Out only; marketId never a feature',
            'no runtime promotion/no 8781',
        ],
    }
    op = (Path(os.environ['BTC5M_LAN_RESULT_DIR']) / 'result.json') if str(a.output).upper() == 'AUTO' else Path(a.output)
    op.parent.mkdir(parents=True, exist_ok=True)
    op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': True, 'featureSets': {k: v['targetVerdicts'] for k, v in all_results.items()}}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
