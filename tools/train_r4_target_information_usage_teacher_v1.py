from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import average_precision_score, log_loss, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
PUBLIC_DB = ROOT / 'data/public_research_archive_v1.db'
OUT = ROOT / 'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1.json'
MODEL = ROOT / 'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1.joblib'
ROWS_OUT = ROOT / 'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
VERSION = 'R4_TARGET_INFORMATION_USAGE_TEACHER_V1'

LOGIC = ['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','pre_maker_abs_gap']
PREDICT = ['predict_up_mid','predict_edge','predict_supports_dominant']
STRIKE = ['strike_toward_dominant_bps','spot_supports_dominant']
SPOT_MICRO = [
    'spot_queue_imbalance','spot_taker_imbalance_250ms','spot_taker_imbalance_1s',
    'spot_return_250ms_bps','spot_return_1s_bps','spot_return_3s_bps','spot_return_5s_bps',
    'spot_micro_minus_spot_bps',
]
FUTURES_MICRO = [
    'futures_queue_imbalance','futures_taker_imbalance_250ms','futures_taker_imbalance_1s',
    'futures_return_250ms_bps','futures_return_1s_bps','futures_return_3s_bps','futures_return_5s_bps',
    'futures_micro_minus_futures_bps',
]
BASIS_REFERENCE = ['perp_spot_basis_bps','spot_minus_chainlink_bps','chainlink_minus_strike_bps']
ENGINEERED = ['direction_score','volatility_alert']


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


def load_public(ids):
    cols = [
        'market_id','sampled_at_ms','spot_price','spot_microprice','spot_queue_imbalance',
        'spot_taker_imbalance_250ms','spot_taker_imbalance_1s','spot_return_250ms_bps','spot_return_1s_bps',
        'spot_return_3s_bps','spot_return_5s_bps','futures_price','futures_microprice','futures_queue_imbalance',
        'futures_taker_imbalance_250ms','futures_taker_imbalance_1s','futures_return_250ms_bps','futures_return_1s_bps',
        'futures_return_3s_bps','futures_return_5s_bps','perp_spot_basis_bps','chainlink_minus_strike_bps',
        'spot_minus_chainlink_bps','direction_score','volatility_alert','feature_completeness_ratio','missing_feature_count',
    ]
    con = sqlite3.connect(f'file:{PUBLIC_DB}?mode=ro', uri=True)
    try:
        ph = ','.join('?' for _ in ids)
        q = f"select {','.join(cols)} from wallet_taker_signal_snapshots where market_id in ({ph})"
        pub = pd.read_sql_query(q, con, params=list(map(int, ids)))
    finally:
        con.close()
    pub = pub.sort_values(['market_id','sampled_at_ms']).drop_duplicates(['market_id','sampled_at_ms'], keep='last')
    return pub


def main():
    base = pd.read_csv(SRC)
    base['market_id'] = base.market_id.astype(int)
    pub = load_public(sorted(base.market_id.unique()))
    df = base.merge(pub, on=['market_id','sampled_at_ms'], how='left', validate='many_to_one')
    df['repair'] = 1 - df['is_add'].astype(int)
    for a,b,out in [
        ('spot_microprice','spot_price','spot_micro_minus_spot_bps'),
        ('futures_microprice','futures_price','futures_micro_minus_futures_bps'),
    ]:
        df[out] = np.where(
            pd.to_numeric(df[b], errors='coerce').abs() > 1e-12,
            (pd.to_numeric(df[a], errors='coerce') / pd.to_numeric(df[b], errors='coerce') - 1.0) * 10000.0,
            np.nan,
        )
    df = df.replace([np.inf,-np.inf], np.nan)

    # Strictly fixed chronological market split identical in spirit to V0.
    mt = df.groupby('market_id', as_index=False)['first_event_ms'].min().sort_values('first_event_ms')
    markets = mt.market_id.astype(int).tolist(); n=len(markets)
    a=max(1,int(n*.70)); b=min(n,max(a+1,int(n*.85)))
    split={'train':markets[:a],'validation':markets[a:b],'test':markets[b:]}
    parts={k:df[df.market_id.isin(set(v))].copy() for k,v in split.items()}

    base_info = LOGIC + PREDICT + STRIKE
    feature_sets = {
        'LOGIC_ONLY': LOGIC,
        'LOGIC_PREDICT_STRIKE': base_info,
        'PLUS_SPOT_MICRO': base_info + SPOT_MICRO,
        'PLUS_FUTURES_MICRO': base_info + FUTURES_MICRO,
        'PLUS_SPOT_FUTURES': base_info + SPOT_MICRO + FUTURES_MICRO,
        'PLUS_BASIS_REFERENCE': base_info + BASIS_REFERENCE,
        'ALL_RAW_PUBLIC': base_info + SPOT_MICRO + FUTURES_MICRO + BASIS_REFERENCE,
        'ALL_RAW_PLUS_ENGINEERED': base_info + SPOT_MICRO + FUTURES_MICRO + BASIS_REFERENCE + ENGINEERED,
    }

    models={}; results={}
    for name,feats in feature_sets.items():
        m=make_hgb(); m.fit(parts['train'][feats], parts['train']['repair'])
        models[name]=m
        results[name]={k:metric(p.repair.to_numpy(),m.predict_proba(p[feats])[:,1]) for k,p in parts.items()}

    raw_name='ALL_RAW_PUBLIC'; raw=results[raw_name]
    base_name='LOGIC_PREDICT_STRIKE'; bres=results[base_name]
    for name in results:
        for sk in ('validation','test'):
            results[name][sk]['deltaAucVsLogicPredictStrike']=results[name][sk]['auc']-bres[sk]['auc']
            results[name][sk]['deltaApVsLogicPredictStrike']=results[name][sk]['ap']-bres[sk]['ap']
            results[name][sk]['logLossImprovementVsLogicPredictStrike']=bres[sk]['logLoss']-results[name][sk]['logLoss']
            results[name][sk]['deltaAucVsAllRaw']=results[name][sk]['auc']-raw[sk]['auc']

    # Group ablation from the full raw public model, without outcome-tuned thresholds.
    group_ablation={}
    full_groups={
        'NO_STRIKE': LOGIC+PREDICT+SPOT_MICRO+FUTURES_MICRO+BASIS_REFERENCE,
        'NO_SPOT_MICRO': base_info+FUTURES_MICRO+BASIS_REFERENCE,
        'NO_FUTURES_MICRO': base_info+SPOT_MICRO+BASIS_REFERENCE,
        'NO_BASIS_REFERENCE': base_info+SPOT_MICRO+FUTURES_MICRO,
    }
    for name,feats in full_groups.items():
        m=make_hgb(); m.fit(parts['train'][feats],parts['train']['repair'])
        group_ablation[name]={}
        for sk,p in parts.items():
            z=metric(p.repair.to_numpy(),m.predict_proba(p[feats])[:,1])
            if sk in ('validation','test'):
                z['aucDropVsAllRaw']=raw[sk]['auc']-z['auc']
                z['apDropVsAllRaw']=raw[sk]['ap']-z['ap']
                z['logLossPenaltyVsAllRaw']=z['logLoss']-raw[sk]['logLoss']
            group_ablation[name][sk]=z

    # Coverage diagnostics: information may be missing at the beginning of a market; HGB handles NaN.
    all_info=SPOT_MICRO+FUTURES_MICRO+BASIS_REFERENCE
    coverage={}
    for f in all_info+ENGINEERED:
        coverage[f]={'nonNullRate':float(df[f].notna().mean()),'testNonNullRate':float(parts['test'][f].notna().mean())}
    coverage['featureCompletenessRatio']={
        'mean':float(pd.to_numeric(df.feature_completeness_ratio,errors='coerce').mean()),
        'testMean':float(pd.to_numeric(parts['test'].feature_completeness_ratio,errors='coerce').mean()),
    }

    df.to_csv(ROWS_OUT,index=False)
    artifact={
        'version':VERSION,'researchOnly':True,'runtimePromotionAllowed':False,
        'purpose':'Extend the Target information-usage teacher with strict-past public spot/futures microstructure and basis/reference context. Test whether distinct public information channels add Formation-mode information beyond portfolio + Predict + strike. No direct action authority.',
        'coverage':{'rows':int(len(df)),'markets':int(df.market_id.nunique()),'repair':int(df.repair.sum()),'add':int((1-df.repair).sum()),'publicExactJoinRate':float(df.spot_price.notna().mean())},
        'split':{'method':'chronological market 70/15/15','trainMarkets':split['train'],'validationMarkets':split['validation'],'testMarkets':split['test']},
        'featureGroups':{'logic':LOGIC,'predict':PREDICT,'strike':STRIKE,'spotMicro':SPOT_MICRO,'futuresMicro':FUTURES_MICRO,'basisReference':BASIS_REFERENCE,'engineered':ENGINEERED},
        'featureSets':feature_sets,'results':results,'groupAblationVsAllRaw':group_ablation,'informationCoverage':coverage,
        'guards':[
            'No winner/settlement feature.','All public features come from the exact strict-past sampled_at_ms already attached to the Target parent state.',
            'No threshold/hyperparameter sweep.','Missing public microstructure is left as NaN and handled by HGB; missingness/completeness is diagnostic only, not an action signal.',
            'Information teacher predicts Formation mode only and remains KEEP_SIGNAL / NOT_ACTION_AUTHORITY under R4_INFORMATION_LAYER_RESEARCH_CONTRACT_V1.'
        ]
    }
    OUT.write_text(json.dumps(artifact,ensure_ascii=False,indent=2),encoding='utf-8')
    joblib.dump({'version':VERSION,'researchOnly':True,'features':feature_sets,'models':models},MODEL)
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':artifact['coverage'],'results':{k:{'val':v['validation'],'test':v['test']} for k,v in results.items()},'groupAblation':group_ablation,'infoCoverage':coverage},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
