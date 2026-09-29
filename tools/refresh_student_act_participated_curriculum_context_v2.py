from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CUR = ROOT / 'data' / 'research' / 'supervisor_curriculum_v0'
SRC = CUR / 'student_act_participated_curriculum_context_v1_markets.csv'
CAT = CUR / 'ordinary_market_curriculum_catalog_v1.csv'
OUT_CSV = CUR / 'student_act_participated_curriculum_context_v2_markets.csv'
OUT_JSON = CUR / 'student_act_participated_curriculum_context_v2_report.json'

CAT_COLS = [
    'primaryLesson','difficultyTier','lessonTags','lessonPurity','skillEntropy','distinctSkills',
    'channelSwitches','skillSwitches','transitionDensity','stressScore','switchScore','difficultyScore',
    'makerRepairRate','takerRepairRate','makerActions','takerActions'
]


def group_stats(df: pd.DataFrame, col: str):
    out = {}
    for key, g in df.groupby(col, dropna=False):
        k = 'MISSING' if pd.isna(key) else str(key)
        out[k] = {
            'evaluationAppearances': int(len(g)),
            'uniqueMarkets': int(g.market_id.nunique()),
            'meanLlDeltaSoftMinusHard': float(g.ll_delta_soft_minus_hard.mean()),
            'medianLlDeltaSoftMinusHard': float(g.ll_delta_soft_minus_hard.median()),
            'softLlBetterFrac': float((g.ll_delta_soft_minus_hard < 0).mean()),
            'meanBrierDeltaSoftMinusHard': float(g.brier_delta_soft_minus_hard.mean()),
            'softBrierBetterFrac': float((g.brier_delta_soft_minus_hard < 0).mean()),
            'meanPositiveRate': float(g.positive_rate.mean()),
            'meanTakerShareOfAct': float(g.takerShareOfAct.mean()),
            'meanModeSwitchRate': float(g.modeSwitchRate.mean()),
            'meanActClusteredFrac': float(g.actClusteredFrac.mean()),
            'meanReadinessRate': float(g.readinessRate.mean()),
        }
    return out


def main():
    d = pd.read_csv(SRC)
    d = d.drop(columns=[c for c in CAT_COLS if c in d.columns], errors='ignore')
    cat = pd.read_csv(CAT).rename(columns={'marketId': 'market_id'})
    keep = ['market_id'] + [c for c in CAT_COLS if c in cat.columns]
    d = d.merge(cat[keep], on='market_id', how='left')
    d.to_csv(OUT_CSV, index=False)

    per_window = {}
    for n, g in d.groupby('window_train_n'):
        per_window[f'n{int(n)}'] = {
            'evaluationAppearances': int(len(g)),
            'catalogMatched': int(g.primaryLesson.notna().sum()),
            'lessonCounts': {str(k): int(v) for k, v in g.primaryLesson.value_counts(dropna=False).items()},
            'difficultyCounts': {str(k): int(v) for k, v in g.difficultyTier.value_counts(dropna=False).items()},
            'meanLlDeltaSoftMinusHard': float(g.ll_delta_soft_minus_hard.mean()),
            'byPrimaryLesson': group_stats(g, 'primaryLesson'),
            'byDifficultyTier': group_stats(g, 'difficultyTier'),
        }

    report = {
        'reportVersion': 'STUDENT_ACT_PARTICIPATED_CURRICULUM_CONTEXT_V2',
        'researchOnly': True,
        'question': 'With the refreshed 779-market ordinary curriculum catalog, which lesson/context is associated with unstable Soft Teacher transfer in participated-only ACT/HOLD rolling exams?',
        'coverage': {
            'evaluationAppearances': int(len(d)),
            'uniqueEvaluationMarkets': int(d.market_id.nunique()),
            'catalogMatchedAppearances': int(d.primaryLesson.notna().sum()),
            'catalogMatchedUniqueMarkets': int(d.loc[d.primaryLesson.notna(), 'market_id'].nunique()),
            'catalogVersion': 'SUPERVISOR_CURRICULUM_CATALOG_V1 / 779 ordinary markets',
        },
        'perWindow': per_window,
        'allWindowsByPrimaryLesson': group_stats(d, 'primaryLesson'),
        'allWindowsByDifficultyTier': group_stats(d, 'difficultyTier'),
        'failureWindowsN30N40ByPrimaryLesson': group_stats(d[d.window_train_n.isin([30,40])], 'primaryLesson'),
        'failureWindowsN30N40ByDifficultyTier': group_stats(d[d.window_train_n.isin([30,40])], 'difficultyTier'),
        'worstSoftMinusHardMarkets': d.sort_values('ll_delta_soft_minus_hard', ascending=False)[
            ['window_train_n','market_id','ll_delta_soft_minus_hard','positive_rate','primaryLesson','difficultyTier','lessonPurity','skillEntropy','transitionDensity','takerShareOfAct','modeSwitchRate','actClusteredFrac','readinessRate']
        ].head(15).replace({np.nan: None}).to_dict(orient='records'),
        'bestSoftMinusHardMarkets': d.sort_values('ll_delta_soft_minus_hard', ascending=True)[
            ['window_train_n','market_id','ll_delta_soft_minus_hard','positive_rate','primaryLesson','difficultyTier','lessonPurity','skillEntropy','transitionDensity','takerShareOfAct','modeSwitchRate','actClusteredFrac','readinessRate']
        ].head(15).replace({np.nan: None}).to_dict(orient='records'),
        'interpretationGuard': 'Descriptive diagnosis only. No threshold/model/context gate may be tuned from these same rolling labels; reproduce any lesson interaction on independent future ordinary chronology.',
        'guards': [
            'Confirmed Target-participating ordinary markets only in the rolling exams.',
            'No winner/PnL.',
            '2026-08-16 remains sealed.',
            'final75-99 remains sealed.',
            'No threshold sweep or runtime changes.',
        ],
        'files': {'marketDetail': str(OUT_CSV.relative_to(ROOT)).replace('\\','/')}
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
