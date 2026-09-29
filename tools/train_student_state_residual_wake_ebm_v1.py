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
DATA = OUT / 'student_state_residual_wake_v0.csv'
ART = OUT / 'student_state_residual_wake_3s_ebm_v1.joblib'
REPORT = OUT / 'student_state_residual_wake_3s_ebm_v1_report.json'
SEED = 20260820

BASE = [
    'pMakerUpBase','pMakerDownBase','pMakerUp','pMakerDown','pTaker1s','pTaker3s',
    'seconds_left',
    'maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
    'taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage',
    'combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
    'worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign',
    'last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms',
    'maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s',
    'maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s','maker_side_streak','taker_side_streak',
    'combined_absnet_change_10s','maker_absnet_change_10s',
    'maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge','taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge','combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge',
    'up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth',
    'down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth',
    'pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge',
    'last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms','placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s','up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak',
]
DERIVED = [
    'maker_coverage_gap','combined_coverage_gap','floor_per_maker_gross','floor_per_combined_gross',
    'absnet_per_maker_gross','maker_absnet_velocity_10s_per_gross','recent_maker_share_rate_10s',
    'maker_pressure_sum','maker_pressure_gap_abs','corrective_pressure_sum','corrective_pressure_gap_abs',
]
FEATURES = BASE + DERIVED


def div(a, b):
    a = pd.to_numeric(a, errors='coerce')
    b = pd.to_numeric(b, errors='coerce')
    return a / b.where(b.abs() > 1e-9)


def enrich(d):
    d = d.copy()
    d['maker_coverage_gap'] = 1.0 - pd.to_numeric(d.maker_paired_coverage, errors='coerce')
    d['combined_coverage_gap'] = 1.0 - pd.to_numeric(d.combined_paired_coverage, errors='coerce')
    d['floor_per_maker_gross'] = div(d.worst_case_floor, d.maker_gross)
    d['floor_per_combined_gross'] = div(d.worst_case_floor, d.combined_gross)
    d['absnet_per_maker_gross'] = div(d.maker_abs_net, d.maker_gross)
    d['maker_absnet_velocity_10s_per_gross'] = div(d.maker_absnet_change_10s, d.maker_gross)
    d['recent_maker_share_rate_10s'] = pd.to_numeric(d.maker_shares_10s, errors='coerce') / 10.0
    d['maker_pressure_sum'] = pd.to_numeric(d.pMakerUpBase, errors='coerce') + pd.to_numeric(d.pMakerDownBase, errors='coerce')
    d['maker_pressure_gap_abs'] = (pd.to_numeric(d.pMakerUpBase, errors='coerce') - pd.to_numeric(d.pMakerDownBase, errors='coerce')).abs()
    d['corrective_pressure_sum'] = pd.to_numeric(d.pMakerUp, errors='coerce') + pd.to_numeric(d.pMakerDown, errors='coerce')
    d['corrective_pressure_gap_abs'] = (pd.to_numeric(d.pMakerUp, errors='coerce') - pd.to_numeric(d.pMakerDown, errors='coerce')).abs()
    return d


def metric(y, p):
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    both = len(set(y.tolist())) > 1
    return {
        'n': int(len(y)), 'positives': int(y.sum()), 'rate': float(y.mean()), 'predMean': float(p.mean()),
        'auc': float(roc_auc_score(y, p)) if both else None,
        'ap': float(average_precision_score(y, p)) if y.sum() else None,
        'logLoss': float(log_loss(y, p, labels=[0,1])), 'brier': float(brier_score_loss(y, p)),
    }


def top_terms(model, n=25):
    imp = list(model.term_importances()); names = list(model.term_names_)
    order = sorted(range(len(imp)), key=lambda i: float(imp[i]), reverse=True)[:n]
    return [{'term': str(names[i]), 'importance': float(imp[i])} for i in order]


def main():
    d = enrich(pd.read_csv(DATA)).sort_values(['windowEndMs','atMs','marketId']).reset_index(drop=True)
    markets = d[['marketId','windowEndMs']].drop_duplicates().sort_values(['windowEndMs','marketId'])
    ids = markets.marketId.astype(int).tolist()
    split = {'train': set(ids[:50]), 'validation': set(ids[50:62]), 'test': set(ids[62:75])}
    parts = {k: d[d.marketId.astype(int).isin(v)].copy() for k,v in split.items()}

    model = ExplainableBoostingClassifier(
        feature_names=FEATURES,
        max_bins=48,
        max_interaction_bins=12,
        interactions=4,
        outer_bags=4,
        learning_rate=.03,
        max_rounds=1000,
        early_stopping_rounds=80,
        min_samples_leaf=15,
        n_jobs=-2,
        random_state=SEED,
    )
    model.fit(parts['train'][FEATURES].apply(pd.to_numeric, errors='coerce'), parts['train'].label_target_residual_repair_next3s.astype(int))

    results = {}
    for name, x in parts.items():
        y = x.label_target_residual_repair_next3s.astype(int).to_numpy()
        p = model.predict_proba(x[FEATURES].apply(pd.to_numeric, errors='coerce'))[:,1]
        raw = pd.to_numeric(x.pTaker3s, errors='coerce').fillna(0).to_numpy()
        em = metric(y,p); bm = metric(y,raw)
        results[name] = {
            'studentStateEbm': em,
            'rawFrozenP3': bm,
            'lift': {
                'auc': em['auc']-bm['auc'] if em['auc'] is not None and bm['auc'] is not None else None,
                'ap': em['ap']-bm['ap'] if em['ap'] is not None and bm['ap'] is not None else None,
                'logLoss': em['logLoss']-bm['logLoss'],
                'brier': em['brier']-bm['brier'],
            },
        }

    payload = {
        'version':'STUDENT_STATE_RESIDUAL_WAKE_3S_EBM_V1',
        'model':model,
        'features':FEATURES,
        'trainingMarkets':sorted(split['train']),
        'trainingMaxWindowEndMs':int(parts['train'].windowEndMs.max()),
        'semantics':'OUR R1 own-state -> probability that same-market/time Target starts residual REPAIR_EFFECT regime within3s; teacher action is label only. Runtime use is arbitration wake, never direct Taker.',
    }
    joblib.dump(payload,ART)
    report = {
        'reportVersion':'STUDENT_STATE_RESIDUAL_WAKE_3S_EBM_V1',
        'researchOnly':True,'runtimeTargetDataAllowed':False,
        'dataset':{'file':str(DATA),'rows':int(len(d)),'markets':int(d.marketId.nunique()),'positiveRate':float(d.label_target_residual_repair_next3s.mean())},
        'splitMarkets':{k:len(v) for k,v in split.items()},
        'chronology':{'trainMaxWindowEndMs':int(parts['train'].windowEndMs.max()),'validationMaxWindowEndMs':int(parts['validation'].windowEndMs.max()),'testMaxWindowEndMs':int(parts['test'].windowEndMs.max()),'finalStart75Untouched':True},
        'results':results,'topTerms':top_terms(model),'artifact':str(ART),
        'promotionRule':'Only proceed to 8785/offline R2 if both validation and test improve materially over raw frozen p3 in ranking and/or calibration. No PnL selection.',
        'runtimeContract':{'input':'OUR own inventory/lifecycle + public book + frozen model pressures only','output':'residual arbitration wake probability','forbidden':'Target runtime state/action, winner, OUR future PnL','afterWake':'Residual Passive Repair -> frozen p1 active urgency -> SIDE/EFFECT'},
        'guards':['Teacher future Target repair is label only.','No winner/PnL input or model selection.','No final start75-99 usage.','8784 R1 remains frozen.','Single fixed EBM capacity; no hyperparameter sweep.'],
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
