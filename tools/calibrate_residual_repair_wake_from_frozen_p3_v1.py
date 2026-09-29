from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'research' / 'target_maker_taker_coordination_big_v1'
ROWS = OUT / 'residual_drift_repair_arbitration_3s_states_v0.csv'
FROZEN = OUT / 'frozen_hazard_3s_full.joblib'
ART = OUT / 'residual_repair_wake_p3_platt_v1.joblib'
REPORT = OUT / 'residual_repair_wake_p3_platt_v1_report.json'
EPS = 1e-6


def metric(y, p):
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    return {
        'n': int(len(y)),
        'positives': int(y.sum()),
        'rate': float(y.mean()),
        'predMean': float(p.mean()),
        'auc': float(roc_auc_score(y, p)),
        'ap': float(average_precision_score(y, p)),
        'logLoss': float(log_loss(y, p, labels=[0, 1])),
        'brier': float(brier_score_loss(y, p)),
    }


def frozen_prob(artifact, frame):
    feats = list(artifact['features'])
    return artifact['model'].predict_proba(frame[feats].apply(pd.to_numeric, errors='coerce'))[:, 1]


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    return np.log(p / (1 - p)).reshape(-1, 1)


def main():
    d = pd.read_csv(ROWS).sort_values(['market_end_ms', 'checkpoint_ms', 'market_id']).reset_index(drop=True)
    markets = d[['market_id', 'market_end_ms']].drop_duplicates().sort_values(['market_end_ms', 'market_id'])
    ids = markets.market_id.astype(int).tolist()
    a = int(len(ids) * .70)
    b = int(len(ids) * .85)
    split = {'train': set(ids[:a]), 'validation': set(ids[a:b]), 'test': set(ids[b:])}
    parts = {k: d[d.market_id.astype(int).isin(v)].copy() for k, v in split.items()}

    frozen = joblib.load(FROZEN)
    train_raw = frozen_prob(frozen, parts['train'])
    train_y = parts['train'].label_repair_next3s.astype(int).to_numpy()
    # Deliberately simple: one-dimensional Platt scaling of the already-strong frozen p3 ranking.
    # No class weighting, threshold, PnL, phase bucket, or hyperparameter sweep.
    cal = LogisticRegression(C=1.0, solver='lbfgs', max_iter=1000, random_state=20260820)
    cal.fit(logit(train_raw), train_y)

    results = {}
    for name, x in parts.items():
        y = x.label_repair_next3s.astype(int).to_numpy()
        raw = frozen_prob(frozen, x)
        calibrated = cal.predict_proba(logit(raw))[:, 1]
        r = metric(y, raw)
        c = metric(y, calibrated)
        results[name] = {
            'rawFrozenP3': r,
            'repairWakeCalibrated': c,
            'delta': {
                'auc': c['auc'] - r['auc'],
                'ap': c['ap'] - r['ap'],
                'logLoss': c['logLoss'] - r['logLoss'],
                'brier': c['brier'] - r['brier'],
                'meanProbability': c['predMean'] - r['predMean'],
            },
        }

    payload = {
        'version': 'RESIDUAL_REPAIR_WAKE_P3_PLATT_V1',
        'calibrator': cal,
        'input': 'logit(frozen_hazard_3s_full probability)',
        'trainingMarkets': sorted(split['train']),
        'trainingMaxEndMs': int(parts['train'].market_end_ms.max()),
        'semantics': 'Calibrates frozen any-Taker 3s hazard into probability of REPAIR_EFFECT Taker onset within 3s on non-overlap residual states. Runtime use is residual arbitration wake only, never direct Taker execution.',
    }
    joblib.dump(payload, ART)
    report = {
        'reportVersion': 'RESIDUAL_REPAIR_WAKE_P3_PLATT_V1',
        'researchOnly': True,
        'runtimeTargetDataAllowed': False,
        'method': 'one-dimensional chronological Platt scaling on logit(frozen pTaker3s); ranking intentionally unchanged',
        'dataset': {
            'rows': int(len(d)),
            'markets': int(d.market_id.nunique()),
            'positiveRate': float(d.label_repair_next3s.mean()),
            'label': 'future Target REPAIR_EFFECT Taker begins within 3s; overlap-excursion +15s states excluded upstream',
            'source': str(ROWS),
        },
        'splitMarkets': {k: len(v) for k, v in split.items()},
        'chronology': {
            'trainMaxEndMs': int(parts['train'].market_end_ms.max()),
            'validationMaxEndMs': int(parts['validation'].market_end_ms.max()),
            'testMaxEndMs': int(parts['test'].market_end_ms.max()),
            'reservedFinalClosedLoopStartMs': 1787137800000,
        },
        'calibrator': {
            'coef': float(cal.coef_[0, 0]),
            'intercept': float(cal.intercept_[0]),
        },
        'results': results,
        'artifact': str(ART),
        'runtimeContract': {
            'use': 'Bernoulli residual-arbitration wake probability outside an episode when Maker inventory is non-flat',
            'notUse': 'direct Taker execution, winner/PnL filtering, hard thresholding',
            'afterWake': 'Residual Passive Repair hazard -> frozen p1 immediate Taker urgency -> existing SIDE/EFFECT',
        },
        'guards': [
            'No PnL/winner input or label.',
            'No threshold or scaling-factor sweep.',
            'Calibration is fit on train markets only and checked chronologically on validation/test.',
            '8784 R1 remains frozen; final offline start75-99 remains untouched.',
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
