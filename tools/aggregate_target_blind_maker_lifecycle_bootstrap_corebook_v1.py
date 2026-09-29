from pathlib import Path
import json
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

OUT = Path('data/research/target_maker_taker_coordination_big_v1')
SUFF = [f'_corebook_c{i}' for i in range(6)]
PREFIX = 'target_blind_maker_lifecycle_bootstrap_v0'
REPORT = OUT / 'target_blind_maker_lifecycle_bootstrap_corebook_v1_report.json'
OLD_REPORT = OUT / 'target_blind_maker_lifecycle_bootstrap_v0_report.json'


def cat(kind):
    return pd.concat([pd.read_csv(OUT / f'{PREFIX}{s}_{kind}.csv') for s in SUFF], ignore_index=True)


def stat(x):
    x = pd.to_numeric(x, errors='coerce').dropna()
    if len(x) == 0:
        return {'n': 0}
    return {
        'n': int(len(x)), 'mean': float(x.mean()), 'median': float(x.median()),
        'p25': float(x.quantile(.25)), 'p75': float(x.quantile(.75)),
        'min': float(x.min()), 'max': float(x.max())
    }


def multi(y, p):
    return {
        'n': len(y), 'accuracy': float(accuracy_score(y, p)),
        'balancedAccuracy': float(balanced_accuracy_score(y, p)),
        'macroF1': float(f1_score(y, p, average='macro', zero_division=0)),
        'truthDistribution': pd.Series(y).value_counts().to_dict(),
        'predictedDistribution': pd.Series(p).value_counts().to_dict(),
    }


ms = cat('markets')
ev = cat('target_event_eval')
st = cat('states')
pl = cat('placements')
fi = cat('fills')
intents = float(ms.makerIntentUp.sum() + ms.makerIntentDown.sum())
placements = float(ms.generatedPlacements.sum())
old = json.loads(OLD_REPORT.read_text(encoding='utf-8')) if OLD_REPORT.exists() else {}

old_act = old.get('makerActivity', {})
old_life = old.get('lifecycleAtTrueTaker', {})
old_taker = old.get('takerTransfer', {})

rep = {
    'reportVersion': 'TARGET_BLIND_MAKER_LIFECYCLE_BOOTSTRAP_COREBOOK_V1_AGGREGATE',
    'researchOnly': True,
    'runtimeTargetDataAllowed': False,
    'fixedCohort': 'first 240 post-General-Maker-training eligible OUR markets',
    'makerHazardFeatureSet': 'CORE_BOOK (no self-generated lifecycle / placement-memory inputs)',
    'coverage': {
        'markets': int(ms.ourMarketId.nunique()), 'states': len(st), 'placements': len(pl),
        'fills': len(fi), 'targetEventComparisons': len(ev)
    },
    'makerActivity': {
        'intentTotal': int(intents), 'effectivePlacements': int(placements),
        'placementRealizationRate': placements / intents if intents else None,
        'placementsPerMarket': stat(ms.generatedPlacements),
        'fillsPerMarket': stat(ms.generatedMakerFills),
        'targetMakerParentsPerMarket': stat(ms.targetMakerParents),
        'grossSharesPerMarket': stat(ms.makerGrossShares),
        'finalAbsNet': stat(ms.finalMakerAbsNet),
        'pairedCoverage': stat(ms.makerPairedCoverage),
    },
    'takerTransfer': {
        'side': multi(ev.truthSide.astype(str), ev.predSide.astype(str)),
        'effect': multi(ev.truthEffect.astype(str), ev.predEffect.astype(str)),
        'meanP1AtTrueTaker': float(ev.pTaker1s.mean()),
        'medianP1AtTrueTaker': float(ev.pTaker1s.median()),
    },
    'lifecycleAtTrueTaker': {
        'lastMakerAgeMs': stat(ev.lastMakerAgeMs),
        'makerFills5s': stat(ev.makerFills5s),
        'makerGross': stat(ev.makerGross),
        'makerPairedCoverage': stat(ev.makerPairedCoverage),
    },
    'deltaVsLifecycleFeedbackV0': {
        'intentTotal': int(intents) - int(old_act.get('intentTotal', 0)),
        'placementsPerMarketMean': float(ms.generatedPlacements.mean()) - float(old_act.get('placementsPerMarket', {}).get('mean', 0.0)),
        'fillsPerMarketMean': float(ms.generatedMakerFills.mean()) - float(old_act.get('fillsPerMarket', {}).get('mean', 0.0)),
        'placementRealizationRate': (placements / intents if intents else 0.0) - float(old_act.get('placementRealizationRate', 0.0)),
        'finalAbsNetMean': float(ms.finalMakerAbsNet.mean()) - float(old_act.get('finalAbsNet', {}).get('mean', 0.0)),
        'pairedCoverageMean': float(ms.makerPairedCoverage.mean()) - float(old_act.get('pairedCoverage', {}).get('mean', 0.0)),
        'sideBalancedAccuracy': float(balanced_accuracy_score(ev.truthSide.astype(str), ev.predSide.astype(str))) - float(old_taker.get('side', {}).get('balancedAccuracy', 0.0)),
        'effectBalancedAccuracy': float(balanced_accuracy_score(ev.truthEffect.astype(str), ev.predEffect.astype(str))) - float(old_taker.get('effect', {}).get('balancedAccuracy', 0.0)),
        'lastMakerAgeMedianMs': float(pd.to_numeric(ev.lastMakerAgeMs, errors='coerce').median()) - float(old_life.get('lastMakerAgeMs', {}).get('median', 0.0)),
        'makerFills5sMean': float(pd.to_numeric(ev.makerFills5s, errors='coerce').mean()) - float(old_life.get('makerFills5s', {}).get('mean', 0.0)),
    },
    'references': {
        'featureSwapOracleExpectedPlacementsPer300s': {'OUR_ALL': 28.71463119246426, 'TARGET_LIFECYCLE': 92.74086253110045},
        'generalCoreBookTestAUC': {'UP': 0.7423443809296115, 'DOWN': 0.7270680671663243},
        'targetTeacher': {'lastMakerAgeMedianMs': 1999.0, 'makerFills5sMedian': 3.0, 'makerGrossMedian': 864.8073450159809, 'makerPairedCoverageMedian': 0.8067955525253958},
    },
    'guards': [
        'No threshold tuning.',
        'No Target runtime data generated actions.',
        'CORE+BOOK frozen Maker hazards exclude self-generated lifecycle/placement-memory feedback.',
        'Fixed first-240 cohort; later collector growth excluded.',
        'Same stochastic seed/execution proxy/resting policy as V0 for controlled comparison.'
    ]
}
REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(rep, ensure_ascii=False, indent=2))
