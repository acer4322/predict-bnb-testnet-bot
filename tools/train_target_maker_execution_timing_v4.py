from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'research' / 'target_maker_taker_coordination_big_v1'
SOURCE = OUT / 'target_maker_execution_hazard_v3_runtime_safe_states.csv'
ART = OUT / 'target_maker_execution_timing_v4_runtime_safe.joblib'
REPORT = OUT / 'target_maker_execution_timing_v4_runtime_safe_report.json'
SEED = 20260820

# Runtime-equivalent inputs only. `source_is_cancel_candidate` is teacher bookkeeping
# and is used solely to select the high-confidence filled-parent timing cohort.
FEATURES = [
    'seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
    'taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage',
    'combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
    'worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign',
    'last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms',
    'maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s',
    'maker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s',
    'up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth',
    'down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth',
    'pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge',
    'side_is_up','order_age_ms','quote_price','quote_offset_ticks','cum_depletion_qty','cum_replenish_qty',
    'depletion_last1s_qty','replenish_last1s_qty','level_zero_seen','level_zero_last1s','pass_through_now',
    'ask_touch_now','current_bid','current_ask','current_spread_ticks','current_bid_depth','time_since_last_depletion_ms',
]


def metrics(y, p):
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    both = len(set(y.tolist())) > 1
    return {
        'n': int(len(y)),
        'positives': int(y.sum()),
        'rate': float(y.mean()),
        'predMean': float(p.mean()),
        'auc': float(roc_auc_score(y, p)) if both else None,
        'ap': float(average_precision_score(y, p)) if y.sum() else None,
        'logLoss': float(log_loss(y, p, labels=[0, 1])),
        'brier': float(brier_score_loss(y, p)),
    }


def binary_proxy(y, trig):
    y = np.asarray(y, dtype=int)
    t = np.asarray(trig, dtype=int)
    tp = int(((y == 1) & (t == 1)).sum())
    fp = int(((y == 0) & (t == 1)).sum())
    fn = int(((y == 1) & (t == 0)).sum())
    tn = int(len(y) - tp - fp - fn)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision is not None and recall is not None and precision + recall else None
    return {'triggerRate': float(t.mean()), 'precision': precision, 'recall': recall, 'f1': f1, 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn}


def top_terms(model, n=20):
    imp = list(model.term_importances())
    names = list(model.term_names_)
    order = sorted(range(len(imp)), key=lambda i: float(imp[i]), reverse=True)[:n]
    return [{'term': str(names[i]), 'importance': float(imp[i])} for i in order]


def main():
    d = pd.read_csv(SOURCE)
    # Filled-parent timing cohort only. These rows come from high-confidence parent lifecycles
    # with known placement and first fill. Earlier checkpoints are strict-past negatives;
    # the checkpoint immediately preceding first fill is positive.
    d = d[pd.to_numeric(d['source_is_cancel_candidate'], errors='coerce').fillna(1) == 0].copy()
    d = d.sort_values(['market_end_ms', 'checkpoint_ms', 'market_id']).reset_index(drop=True)
    markets = d[['market_id', 'market_end_ms']].drop_duplicates().sort_values(['market_end_ms', 'market_id'])
    ids = markets.market_id.astype(int).tolist()
    n = len(ids)
    a = int(n * .70)
    b = int(n * .85)
    split = {'train': set(ids[:a]), 'validation': set(ids[a:b]), 'test': set(ids[b:])}
    parts = {k: d[d.market_id.astype(int).isin(v)].copy() for k, v in split.items()}

    model = ExplainableBoostingClassifier(
        feature_names=FEATURES,
        max_bins=64,
        max_interaction_bins=16,
        interactions=4,
        outer_bags=4,
        learning_rate=.03,
        max_rounds=1200,
        early_stopping_rounds=80,
        min_samples_leaf=12,
        n_jobs=-2,
        random_state=SEED,
    )
    xtr = parts['train'][FEATURES].apply(pd.to_numeric, errors='coerce')
    ytr = parts['train'].label_fill_next1s.astype(int)
    model.fit(xtr, ytr)

    report_metrics = {}
    for name, x in parts.items():
        y = x.label_fill_next1s.astype(int).to_numpy()
        p = model.predict_proba(x[FEATURES].apply(pd.to_numeric, errors='coerce'))[:, 1]
        recent = ((pd.to_numeric(x.depletion_last1s_qty, errors='coerce').fillna(0) > 0) | (pd.to_numeric(x.pass_through_now, errors='coerce').fillna(0) > 0)).to_numpy(int)
        ever = ((pd.to_numeric(x.cum_depletion_qty, errors='coerce').fillna(0) > 0) | (pd.to_numeric(x.pass_through_now, errors='coerce').fillna(0) > 0)).to_numpy(int)
        qzero = ((pd.to_numeric(x.level_zero_seen, errors='coerce').fillna(0) > 0) | (pd.to_numeric(x.pass_through_now, errors='coerce').fillna(0) > 0)).to_numpy(int)
        # Runtime-safe opportunity-gated timing hazards. These are probabilities, not hard thresholds.
        gated_recent = p * recent
        gated_ever = p * ever
        report_metrics[name] = {
            'timingEbm': metrics(y, p),
            'recentDepletionOrPass': binary_proxy(y, recent),
            'everDepletionOrPass': binary_proxy(y, ever),
            'levelZeroEverOrPass': binary_proxy(y, qzero),
            'timingEbmGatedRecent': metrics(y, gated_recent),
            'timingEbmGatedEver': metrics(y, gated_ever),
        }

    payload = {
        'version': 'TARGET_MAKER_EXECUTION_TIMING_V4_RUNTIME_SAFE',
        'model': model,
        'features': FEATURES,
        'trainingMarkets': sorted(split['train']),
        'trainingMaxEndMs': int(parts['train'].market_end_ms.max()),
        'semantics': 'conditional next-1s fill timing hazard among high-confidence Target Maker parents known to eventually fill; runtime use must be gated by public execution evidence and must not be treated as unconditional eventual-fill probability',
    }
    joblib.dump(payload, ART)
    report = {
        'reportVersion': 'TARGET_MAKER_EXECUTION_TIMING_V4_RUNTIME_SAFE',
        'researchOnly': True,
        'runtimeTargetDataAllowed': False,
        'goal': 'Learn when a known resting Maker order is close to filling, without pretending speculative cancel candidates prove Target ownership.',
        'dataset': {
            'source': str(SOURCE),
            'rows': int(len(d)),
            'markets': int(d.market_id.nunique()),
            'positiveRate': float(d.label_fill_next1s.mean()),
            'teacherSelection': 'source_is_cancel_candidate==0 selects high-confidence filled parents; selector is never a model feature',
        },
        'splitMarkets': {k: len(v) for k, v in split.items()},
        'chronology': {
            'trainMaxEndMs': int(parts['train'].market_end_ms.max()),
            'testMaxEndMs': int(parts['test'].market_end_ms.max()),
            'reservedFinalClosedLoopStartMs': 1787137800000,
        },
        'metrics': report_metrics,
        'topTerms': top_terms(model),
        'artifact': str(ART),
        'runtimeContract': {
            'allowed': 'own resting-order state + public top book + public quote-level depletion/replenishment + own lifecycle analogue',
            'forbidden': 'Target identity, Target future fill, cancel-candidate source identity, winner, PnL',
            'recommendedV0Use': 'public execution evidence gate first; then Bernoulli sample the timing hazard. Keep QUEUECLEAR/PASS and DEPLETION/PASS as sensitivity bounds.',
        },
        'guards': [
            'This is conditional timing, not unconditional fill probability.',
            'No speculative cancel candidate is used as a negative order identity.',
            'No hard probability threshold or PnL tuning is used.',
            'Final start75 closed-loop cohort remains untouched.',
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
