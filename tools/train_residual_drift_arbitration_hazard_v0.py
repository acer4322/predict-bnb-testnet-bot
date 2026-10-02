from __future__ import annotations

import bisect
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'research' / 'target_maker_taker_coordination_big_v1'
GENERAL = OUT / 'target_general_maker_side_hazard_v1.csv'
TAKER_EVENTS = OUT / 'taker_event_states_v1.csv'
EXCURSIONS = OUT / 'target_temporary_imbalance_recovery_v0_rows.csv'
FROZEN_3S = OUT / 'frozen_hazard_3s_full.joblib'
ART = OUT / 'residual_drift_repair_arbitration_3s_ebm_v0.joblib'
ROWS = OUT / 'residual_drift_repair_arbitration_3s_states_v0.csv'
REPORT = OUT / 'residual_drift_repair_arbitration_3s_v0_report.json'
SEED = 20260820
FINAL_CLOSED_LOOP_START_MS = 1787137800000
HORIZON_MS = 3000
EXCLUSION_MS = 15000

FEATURES = [
    'seconds_left',
    'maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
    'taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage',
    'combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
    'worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign',
    'last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms',
    'last_taker_up_age_ms','last_taker_down_age_ms',
    'maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s',
    'maker_shares_5s','maker_shares_10s','taker_shares_5s','taker_shares_10s',
    'maker_side_streak','taker_side_streak','combined_absnet_change_10s','maker_absnet_change_10s',
    'maker_up_avg_price','maker_down_avg_price','maker_avg_pair_edge',
    'taker_up_avg_price','taker_down_avg_price','taker_avg_pair_edge',
    'combined_up_avg_price','combined_down_avg_price','combined_avg_pair_edge',
    'up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','up_top3_bid_depth',
    'down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','down_top3_bid_depth',
    'pair_bid_edge','pair_ask_edge','dominant_bid','dominant_ask','opposite_bid','opposite_ask','dominant_opp_bid_pair_edge',
    'last_place_age_ms','last_up_place_age_ms','last_down_place_age_ms',
    'placements_1s','placements_5s','placements_10s','up_placements_5s','down_placements_5s',
    'up_placements_10s','down_placements_10s','placement_side_balance_5s','placement_side_balance_10s','placement_side_streak',
    # Derived runtime-equivalent geometry / drift features. No winner/future data.
    'maker_coverage_gap','combined_coverage_gap','floor_per_maker_gross','floor_per_combined_gross',
    'absnet_per_maker_gross','maker_absnet_velocity_10s_per_gross','recent_maker_share_rate_10s',
]


def safe_div(a, b):
    a = pd.to_numeric(a, errors='coerce')
    b = pd.to_numeric(b, errors='coerce')
    return a / b.where(b.abs() > 1e-9)


def metrics(y, p):
    y = np.asarray(y, dtype=int)
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    both = len(set(y.tolist())) > 1
    return {
        'n': int(len(y)), 'positives': int(y.sum()), 'rate': float(y.mean()) if len(y) else None,
        'predMean': float(p.mean()) if len(p) else None,
        'auc': float(roc_auc_score(y, p)) if both else None,
        'ap': float(average_precision_score(y, p)) if y.sum() else None,
        'logLoss': float(log_loss(y, p, labels=[0,1])) if len(y) else None,
        'brier': float(brier_score_loss(y, p)) if len(y) else None,
    }


def model_prob(artifact, frame):
    feats = list(artifact['features'])
    return artifact['model'].predict_proba(frame[feats].apply(pd.to_numeric, errors='coerce'))[:,1]


def top_terms(model, n=25):
    imp = list(model.term_importances()); names = list(model.term_names_)
    order = sorted(range(len(imp)), key=lambda i: float(imp[i]), reverse=True)[:n]
    return [{'term': str(names[i]), 'importance': float(imp[i])} for i in order]


def build_rows():
    g = pd.read_csv(GENERAL)
    g = g[pd.to_numeric(g.market_end_ms, errors='coerce') < FINAL_CLOSED_LOOP_START_MS].copy()
    g = g[pd.to_numeric(g.seconds_left, errors='coerce').between(3.0, 297.0, inclusive='both')].copy()
    # Residual risk requires an existing non-flat Maker portfolio, but no arbitrary risk threshold.
    g = g[(pd.to_numeric(g.maker_gross, errors='coerce') > 1e-9) & (pd.to_numeric(g.maker_abs_net, errors='coerce') > 1e-9)].copy()

    te = pd.read_csv(TAKER_EVENTS, usecols=['market_id','checkpoint_ms','label_effect','label_side'])
    te = te[te.label_effect.astype(str).eq('REPAIR_EFFECT')].copy()
    repair_times = {}
    for mid, x in te.groupby('market_id'):
        repair_times[int(mid)] = sorted(pd.to_numeric(x.checkpoint_ms, errors='coerce').dropna().astype('int64').tolist())

    ex = pd.read_csv(EXCURSIONS, usecols=['marketId','anchorFillEndMs'])
    intervals = {}
    for mid, x in ex.groupby('marketId'):
        intervals[int(mid)] = sorted((int(t)+1, int(t)+1+EXCLUSION_MS) for t in pd.to_numeric(x.anchorFillEndMs, errors='coerce').dropna().astype('int64'))

    excluded = []
    labels = []
    next_delay = []
    for r in g[['market_id','checkpoint_ms']].itertuples(index=False):
        mid = int(r.market_id); t = int(r.checkpoint_ms)
        inside = any(a <= t <= b for a,b in intervals.get(mid, []))
        excluded.append(inside)
        times = repair_times.get(mid, [])
        i = bisect.bisect_right(times, t)
        nt = times[i] if i < len(times) else None
        delay = nt - t if nt is not None else None
        labels.append(int(delay is not None and 0 < delay <= HORIZON_MS))
        next_delay.append(delay if delay is not None else np.nan)
    g['excluded_overlap_excursion_15s'] = excluded
    g['label_repair_next3s'] = labels
    g['next_repair_delay_ms'] = next_delay
    g = g[~g.excluded_overlap_excursion_15s].copy()

    g['maker_coverage_gap'] = 1.0 - pd.to_numeric(g.maker_paired_coverage, errors='coerce')
    g['combined_coverage_gap'] = 1.0 - pd.to_numeric(g.combined_paired_coverage, errors='coerce')
    g['floor_per_maker_gross'] = safe_div(g.worst_case_floor, g.maker_gross)
    g['floor_per_combined_gross'] = safe_div(g.worst_case_floor, g.combined_gross)
    g['absnet_per_maker_gross'] = safe_div(g.maker_abs_net, g.maker_gross)
    g['maker_absnet_velocity_10s_per_gross'] = safe_div(g.maker_absnet_change_10s, g.maker_gross)
    g['recent_maker_share_rate_10s'] = safe_div(g.maker_shares_10s, pd.Series(np.full(len(g),10.0), index=g.index))
    return g.sort_values(['market_end_ms','checkpoint_ms','market_id']).reset_index(drop=True)


def main():
    d = pd.read_csv(ROWS) if ROWS.exists() else build_rows()
    markets = d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id'])
    ids = markets.market_id.astype(int).tolist(); n = len(ids); a=int(n*.70); b=int(n*.85)
    split = {'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])}
    parts = {k:d[d.market_id.astype(int).isin(v)].copy() for k,v in split.items()}

    model = ExplainableBoostingClassifier(
        feature_names=FEATURES, max_bins=48, max_interaction_bins=12, interactions=4,
        outer_bags=2, learning_rate=.03, max_rounds=800, early_stopping_rounds=60,
        min_samples_leaf=40, n_jobs=-2, random_state=SEED,
    )
    model.fit(parts['train'][FEATURES].apply(pd.to_numeric, errors='coerce'), parts['train'].label_repair_next3s.astype(int))
    frozen = joblib.load(FROZEN_3S)

    results = {}
    for name,x in parts.items():
        y=x.label_repair_next3s.astype(int).to_numpy()
        p=model.predict_proba(x[FEATURES].apply(pd.to_numeric, errors='coerce'))[:,1]
        base=model_prob(frozen,x)
        results[name]={'residualRepairEbm':metrics(y,p),'frozenGeneralTaker3s':metrics(y,base),
                       'lift':{'auc': (roc_auc_score(y,p)-roc_auc_score(y,base)) if len(set(y.tolist()))>1 else None,
                               'ap': (average_precision_score(y,p)-average_precision_score(y,base)) if y.sum() else None,
                               'logLoss': log_loss(y,p,labels=[0,1])-log_loss(y,np.clip(base,1e-7,1-1e-7),labels=[0,1])}}

    payload={'version':'RESIDUAL_DRIFT_REPAIR_ARBITRATION_3S_EBM_V0','model':model,'features':FEATURES,
             'trainingMarkets':sorted(split['train']),'trainingMaxEndMs':int(parts['train'].market_end_ms.max()),
             'semantics':'3s hazard that Target begins a REPAIR_EFFECT Taker action from a non-flat Maker residual state outside the 15s window after any known realized overlap excursion. Runtime use is episode eligibility only, never direct Taker execution.'}
    joblib.dump(payload,ART)
    d.to_csv(ROWS,index=False)
    report={
        'reportVersion':'RESIDUAL_DRIFT_REPAIR_ARBITRATION_3S_V0','researchOnly':True,'runtimeTargetDataAllowed':False,
        'question':'Can strict-past residual inventory geometry/lifecycle identify Target repair-arbitration onset outside the overlap-excursion path that R1 already handles?',
        'dataset':{'rows':int(len(d)),'markets':int(d.market_id.nunique()),'positiveRate':float(d.label_repair_next3s.mean()),
                   'overlapExclusion':'drop checkpoints in [anchorFillEnd+1ms, +15s] after any realized Target temporary-imbalance excursion',
                   'riskFilter':'Maker gross>0 and Maker abs-net>0 only; no hand-picked risk threshold',
                   'label':'future Target REPAIR_EFFECT Taker begins within 3s; post-hoc label only',
                   'cutoffMs':FINAL_CLOSED_LOOP_START_MS,'rowsFile':str(ROWS)},
        'splitMarkets':{k:len(v) for k,v in split.items()},
        'chronology':{'trainMaxEndMs':int(parts['train'].market_end_ms.max()),'validationMaxEndMs':int(parts['validation'].market_end_ms.max()),'testMaxEndMs':int(parts['test'].market_end_ms.max()),'reservedFinalClosedLoopStartMs':FINAL_CLOSED_LOOP_START_MS},
        'results':results,'topTerms':top_terms(model),'artifact':str(ART),
        'promotionRule':'Only consider for 8785 Flash if chronological validation AND test show meaningful repair-label lift over the frozen general 3s Taker hazard. Never use forward R1 PnL to select a threshold.',
        'runtimeContract':{'use':'episode eligibility / residual-risk arbitration entry only','afterEntry':'existing Passive Repair EBM -> frozen Taker readiness/urgency/SIDE/EFFECT','forbidden':'winner, PnL, Target current/future action, overlap teacher state'},
        'guards':['R1 8784 remains frozen.','Final offline start75-99 cohort remains untouched.','No probability threshold or PnL sweep.','REPAIR_EFFECT is an inventory-effect proxy, not semantic intent ground truth.'],
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
