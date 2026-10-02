from __future__ import annotations

import bisect
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    log_loss,
    roc_auc_score,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'data' / 'research' / 'target_maker_taker_coordination_big_v1'
OUT = ROOT / 'data' / 'research' / 'supervisor_options_v0'
GENERAL = SRC / 'target_general_maker_side_hazard_v1.csv'
TAKER_EVENTS = SRC / 'taker_event_states_v1.csv'
STATES = OUT / 'supervisor_options_v0_states.csv'
REPORT = OUT / 'supervisor_options_v0_report.json'
ART = OUT / 'supervisor_options_v0_hgb.joblib'
SEED = 20260820
TAKER_HORIZON_MS = 3000

CURRENT_FEATURES = [
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
]

MEMORY_BASE = [
    'maker_net','maker_abs_net','maker_paired_coverage',
    'taker_net','taker_abs_net','taker_paired_coverage',
    'combined_net','combined_abs_net','combined_paired_coverage',
    'worst_case_floor','best_case_pnl','abs_payoff_gap',
    'maker_avg_pair_edge','taker_avg_pair_edge','combined_avg_pair_edge',
    'pair_bid_edge','pair_ask_edge','dominant_opp_bid_pair_edge',
    'up_bid','up_ask','down_bid','down_ask',
    'placements_5s','placements_10s','maker_fills_5s','maker_fills_10s',
    'taker_fills_5s','taker_fills_10s','maker_shares_10s','taker_shares_10s',
]
LAGS = (3, 10, 30)


def _num(x: pd.Series) -> pd.Series:
    return pd.to_numeric(x, errors='coerce')


def build_states() -> pd.DataFrame:
    d = pd.read_csv(GENERAL).sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
    d = d[_num(d.seconds_left).between(3.0, 297.0, inclusive='both')].copy()

    # This Level-1 source begins 2026-08-17 02:05 +08, after the 2026-08-16 special-market stress period.
    # No winner/PnL is read anywhere in this dataset builder.
    te = pd.read_csv(TAKER_EVENTS, usecols=['market_id','checkpoint_ms','label_effect','label_side'])
    te = te.sort_values(['market_id','checkpoint_ms']).copy()
    taker_by_market: dict[int, tuple[list[int], list[str], list[str]]] = {}
    for mid, x in te.groupby('market_id', sort=False):
        times = _num(x.checkpoint_ms).dropna().astype('int64').tolist()
        # Keep aligned rows after dropping invalid times.
        xx = x[_num(x.checkpoint_ms).notna()].copy()
        taker_by_market[int(mid)] = (
            times,
            xx.label_effect.astype(str).tolist(),
            xx.label_side.astype(str).tolist(),
        )

    next_taker_effect = []
    next_taker_side = []
    next_taker_delay = []
    for r in d[['market_id','checkpoint_ms']].itertuples(index=False):
        mid = int(r.market_id); t = int(r.checkpoint_ms)
        bundle = taker_by_market.get(mid)
        if bundle is None:
            next_taker_effect.append(None); next_taker_side.append(None); next_taker_delay.append(np.nan); continue
        times, effects, sides = bundle
        i = bisect.bisect_right(times, t)
        if i >= len(times):
            next_taker_effect.append(None); next_taker_side.append(None); next_taker_delay.append(np.nan); continue
        delay = int(times[i]) - t
        if 0 < delay <= TAKER_HORIZON_MS:
            next_taker_effect.append(effects[i]); next_taker_side.append(sides[i]); next_taker_delay.append(delay)
        else:
            next_taker_effect.append(None); next_taker_side.append(None); next_taker_delay.append(delay)
    d['next_taker_effect_3s'] = next_taker_effect
    d['next_taker_side_3s'] = next_taker_side
    d['next_taker_delay_ms'] = next_taker_delay

    up = _num(d.label_up_next1s).fillna(0).astype(int).clip(0,1)
    dn = _num(d.label_down_next1s).fillna(0).astype(int).clip(0,1)
    maker_any = (up | dn).astype(int)
    mnet = _num(d.maker_net).fillna(0.0)
    minority_quote = ((mnet > 1e-9) & (dn > 0)) | ((mnet < -1e-9) & (up > 0))
    d['maker_repair_next1s'] = (maker_any.astype(bool) & minority_quote).astype(int)

    # Hierarchical high-level teacher label. Taker has priority over Maker because it is the higher-urgency actuator.
    mode = np.full(len(d), 'HOLD', dtype=object)
    mode[maker_any.to_numpy(dtype=bool)] = 'MAKER'
    has_taker = d.next_taker_effect_3s.notna().to_numpy()
    mode[has_taker] = 'TAKER'
    d['option_mode'] = mode
    d['maker_option'] = np.where(d.maker_repair_next1s.eq(1), 'PASSIVE_REPAIR', 'NORMAL_MAKER')
    d.loc[d.option_mode.ne('MAKER'), 'maker_option'] = None
    d['taker_option'] = d.next_taker_effect_3s.map({
        'REPAIR_EFFECT':'ACTIVE_REPAIR',
        'ADD_EFFECT':'ACTIVE_ADD',
        'BUILD_FROM_FLAT':'ACTIVE_BUILD',
    })
    d.loc[d.option_mode.ne('TAKER'), 'taker_option'] = None

    # Strict-past memory: lags/deltas are built only from prior checkpoints of the same market.
    g = d.groupby('market_id', sort=False)
    memory_features = []
    for base in MEMORY_BASE:
        cur = _num(d[base])
        for lag in LAGS:
            lag_name = f'{base}_lag{lag}s'
            delta_name = f'{base}_delta{lag}s'
            past = g[base].shift(lag)
            d[lag_name] = _num(past)
            d[delta_name] = cur - d[lag_name]
            memory_features.extend([lag_name, delta_name])
    d.attrs['memory_features'] = memory_features
    return d


def weights_for(y: pd.Series) -> np.ndarray:
    counts = y.value_counts()
    # sqrt inverse frequency is deliberately mild; prevents HOLD from dominating without making rare classes explosive.
    w = y.map({k: float(np.sqrt(len(y) / max(1, v))) for k,v in counts.items()}).astype(float).to_numpy()
    return w / float(np.mean(w))


def fit_hgb(x: pd.DataFrame, y: pd.Series, features: list[str]) -> HistGradientBoostingClassifier:
    model = HistGradientBoostingClassifier(
        learning_rate=0.06,
        max_iter=180,
        max_leaf_nodes=15,
        min_samples_leaf=80,
        l2_regularization=1.0,
        early_stopping=True,
        validation_fraction=0.1,
        n_iter_no_change=20,
        random_state=SEED,
    )
    model.fit(x[features].apply(pd.to_numeric, errors='coerce'), y, sample_weight=weights_for(y))
    return model


def multiclass_metrics(model, x: pd.DataFrame, y: pd.Series, features: list[str]) -> dict:
    pred = model.predict(x[features].apply(pd.to_numeric, errors='coerce'))
    proba = model.predict_proba(x[features].apply(pd.to_numeric, errors='coerce'))
    labels = list(model.classes_)
    return {
        'n': int(len(y)),
        'balancedAccuracy': float(balanced_accuracy_score(y, pred)),
        'macroF1': float(f1_score(y, pred, average='macro')),
        'logLoss': float(log_loss(y, proba, labels=labels)),
        'labels': [str(z) for z in labels],
        'confusionMatrix': confusion_matrix(y, pred, labels=labels).astype(int).tolist(),
        'classification': classification_report(y, pred, labels=labels, output_dict=True, zero_division=0),
    }


def binary_metrics(model, x: pd.DataFrame, y: pd.Series, features: list[str], positive_label: str) -> dict:
    z = x[features].apply(pd.to_numeric, errors='coerce')
    classes = list(model.classes_)
    idx = classes.index(positive_label)
    p = model.predict_proba(z)[:, idx]
    pred = model.predict(z)
    yy = y.eq(positive_label).astype(int).to_numpy()
    ppred = pd.Series(pred, index=y.index).eq(positive_label).astype(int).to_numpy()
    both = len(set(yy.tolist())) > 1
    return {
        'n': int(len(y)), 'positive': positive_label, 'positiveRate': float(yy.mean()),
        'auc': float(roc_auc_score(yy,p)) if both else None,
        'ap': float(average_precision_score(yy,p)) if yy.sum() else None,
        'balancedAccuracy': float(balanced_accuracy_score(yy,ppred)) if both else None,
        'f1': float(f1_score(yy,ppred,zero_division=0)),
        'logLoss': float(log_loss(yy,np.column_stack([1-p,p]),labels=[0,1])) if both else None,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    d = build_states()
    memory_features = list(d.attrs.get('memory_features') or [])
    all_features = CURRENT_FEATURES + memory_features

    markets = d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id'])
    ids = markets.market_id.astype(int).tolist(); n = len(ids); a=int(n*.70); b=int(n*.85)
    split_ids = {'train':set(ids[:a]), 'validation':set(ids[a:b]), 'test':set(ids[b:])}
    parts = {k:d[d.market_id.astype(int).isin(v)].copy() for k,v in split_ids.items()}

    models = {}
    results = {}
    for variant, features in [('currentOnly', CURRENT_FEATURES), ('currentPlusMemory', all_features)]:
        train = parts['train']
        mode_model = fit_hgb(train, train.option_mode.astype(str), features)
        maker_train = train[train.option_mode.eq('MAKER')].copy()
        taker_train = train[train.option_mode.eq('TAKER')].copy()
        maker_model = fit_hgb(maker_train, maker_train.maker_option.astype(str), features)
        taker_model = fit_hgb(taker_train, taker_train.taker_option.astype(str), features)
        models[variant] = {'mode':mode_model,'maker':maker_model,'taker':taker_model,'features':features}
        vr = {}
        for split_name, x in parts.items():
            mx = x[x.option_mode.eq('MAKER')].copy(); tx = x[x.option_mode.eq('TAKER')].copy()
            vr[split_name] = {
                'mode': multiclass_metrics(mode_model,x,x.option_mode.astype(str),features),
                'makerRepairVsNormal': binary_metrics(maker_model,mx,mx.maker_option.astype(str),features,'PASSIVE_REPAIR') if len(mx) else None,
                'takerRepairVsOther': binary_metrics(taker_model,tx,tx.taker_option.astype(str),features,'ACTIVE_REPAIR') if len(tx) else None,
                'classCounts': {
                    'mode': x.option_mode.value_counts().to_dict(),
                    'maker': mx.maker_option.value_counts().to_dict(),
                    'taker': tx.taker_option.value_counts().to_dict(),
                },
            }
        results[variant] = vr

    # Persist the memory model only as a research artifact; no runtime promotion.
    artifact = {
        'version':'SUPERVISOR_OPTIONS_V0_HGB_RESEARCH',
        'researchOnly':True,
        'runtimePromotion':False,
        'features':all_features,
        'models':models['currentPlusMemory'],
        'labelContract':{
            'mode':'TAKER if earliest Target Taker event starts within3s; else MAKER if Target Maker placement side label occurs within1s; else HOLD',
            'maker':'PASSIVE_REPAIR when the upcoming Maker side is minority to strict-past Maker net; else NORMAL_MAKER',
            'taker':'Target effect teacher: ACTIVE_REPAIR / ACTIVE_ADD / ACTIVE_BUILD; post-hoc label only',
        },
        'guards':['No winner or PnL features/labels.','All memory features are same-market strict-past lags/deltas.','Special 2026-08-16 market is absent from this Level-1 source.','No 8784/8786/live action is controlled by this artifact.'],
    }
    joblib.dump(artifact, ART)

    keep = ['market_id','market_end_ms','checkpoint_ms','seconds_left','option_mode','maker_option','taker_option','next_taker_effect_3s','next_taker_side_3s','next_taker_delay_ms','maker_repair_next1s'] + CURRENT_FEATURES[1:] + memory_features
    d[keep].to_csv(STATES,index=False)

    def lift(path: str, metric: str) -> dict:
        a0 = results['currentOnly'][path]
        a1 = results['currentPlusMemory'][path]
        # path is split; metric names below route through mode/binary objects.
        return {'currentOnly':a0,'currentPlusMemory':a1}

    report = {
        'reportVersion':'SUPERVISOR_OPTIONS_V0',
        'researchOnly':True,
        'goal':'Level-1 ordinary-market high-level driver learnability: hierarchical gating over existing Maker/Taker actuators.',
        'source':{
            'generalStates':str(GENERAL),'takerEvents':str(TAKER_EVENTS),
            'markets':int(d.market_id.nunique()),'rows':int(len(d)),
            'startEndMs':int(d.market_end_ms.min()),'lastEndMs':int(d.market_end_ms.max()),
            'specialMarketTraining':'ABSENT; source begins after 2026-08-16 stress period',
        },
        'labels':{
            'priority':'TAKER > MAKER > HOLD',
            'takerHorizonMs':TAKER_HORIZON_MS,
            'makerHorizon':'existing strict-past dataset next1s placement labels',
            'modeCounts':d.option_mode.value_counts().to_dict(),
            'makerCounts':d[d.option_mode.eq('MAKER')].maker_option.value_counts().to_dict(),
            'takerCounts':d[d.option_mode.eq('TAKER')].taker_option.value_counts().to_dict(),
        },
        'splitMarkets':{k:len(v) for k,v in split_ids.items()},
        'chronology':{k:{'minEndMs':int(parts[k].market_end_ms.min()),'maxEndMs':int(parts[k].market_end_ms.max())} for k in parts},
        'features':{'current':len(CURRENT_FEATURES),'memory':len(memory_features),'total':len(all_features),'lagsSeconds':list(LAGS)},
        'results':results,
        'memoryLiftSummary':{
            split:{
                'modeBalancedAccuracy':results['currentPlusMemory'][split]['mode']['balancedAccuracy']-results['currentOnly'][split]['mode']['balancedAccuracy'],
                'modeMacroF1':results['currentPlusMemory'][split]['mode']['macroF1']-results['currentOnly'][split]['mode']['macroF1'],
                'makerRepairAuc':(results['currentPlusMemory'][split]['makerRepairVsNormal']['auc']-results['currentOnly'][split]['makerRepairVsNormal']['auc']) if results['currentOnly'][split]['makerRepairVsNormal']['auc'] is not None else None,
                'takerRepairAuc':(results['currentPlusMemory'][split]['takerRepairVsOther']['auc']-results['currentOnly'][split]['takerRepairVsOther']['auc']) if results['currentOnly'][split]['takerRepairVsOther']['auc'] is not None else None,
            } for split in results['currentOnly']
        },
        'artifact':str(ART),'statesFile':str(STATES),
        'decisionRule':'Do not deploy. Continue only if chronological validation/test show that high-level mode is learnable above trivial behavior and memory gives stable or structurally useful lift. Next step is shadow-advisor evaluation on OUR-state, not live control.',
        'guards':['No winner/PnL teacher or feature.','No 2026-08-16 special market.','No threshold/PnL sweep.','8784 R2 and 8786 CAP100 remain frozen and untouched.','No Echtgeld changes.'],
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
