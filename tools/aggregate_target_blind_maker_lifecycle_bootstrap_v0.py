from pathlib import Path
import json, math
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
OUT=Path('data/research/target_maker_taker_coordination_big_v1')
SUFF=[f'_c{i}' for i in range(6)]

def cat(kind):
    return pd.concat([pd.read_csv(OUT/f'target_blind_maker_lifecycle_bootstrap_v0{s}_{kind}.csv') for s in SUFF],ignore_index=True)

def stat(x):
    x=pd.to_numeric(x,errors='coerce').dropna()
    if len(x)==0:return {'n':0}
    return {'n':int(len(x)),'mean':float(x.mean()),'median':float(x.median()),'p25':float(x.quantile(.25)),'p75':float(x.quantile(.75)),'min':float(x.min()),'max':float(x.max())}

def multi(y,p):
    return {'n':len(y),'accuracy':float(accuracy_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p)),'macroF1':float(f1_score(y,p,average='macro',zero_division=0)),'truthDistribution':pd.Series(y).value_counts().to_dict(),'predictedDistribution':pd.Series(p).value_counts().to_dict()}

ms=cat('markets');ev=cat('target_event_eval');st=cat('states');pl=cat('placements');fi=cat('fills')
intents=float(ms.makerIntentUp.sum()+ms.makerIntentDown.sum());placements=float(ms.generatedPlacements.sum())
rep={
'reportVersion':'TARGET_BLIND_MAKER_LIFECYCLE_BOOTSTRAP_V0_AGGREGATE','researchOnly':True,'fixedCohort':'first 240 post-General-Maker-training eligible OUR markets',
'coverage':{'markets':int(ms.ourMarketId.nunique()),'states':len(st),'placements':len(pl),'fills':len(fi),'targetEventComparisons':len(ev)},
'makerActivity':{'intentTotal':int(intents),'effectivePlacements':int(placements),'placementRealizationRate':placements/intents if intents else None,'placementsPerMarket':stat(ms.generatedPlacements),'fillsPerMarket':stat(ms.generatedMakerFills),'targetMakerParentsPerMarket':stat(ms.targetMakerParents),'grossSharesPerMarket':stat(ms.makerGrossShares),'finalAbsNet':stat(ms.finalMakerAbsNet),'pairedCoverage':stat(ms.makerPairedCoverage)},
'takerTransfer':{'side':multi(ev.truthSide.astype(str),ev.predSide.astype(str)),'effect':multi(ev.truthEffect.astype(str),ev.predEffect.astype(str)),'meanP1AtTrueTaker':float(ev.pTaker1s.mean()),'medianP1AtTrueTaker':float(ev.pTaker1s.median())},
'lifecycleAtTrueTaker':{'lastMakerAgeMs':stat(ev.lastMakerAgeMs),'makerFills5s':stat(ev.makerFills5s),'makerGross':stat(ev.makerGross),'makerPairedCoverage':stat(ev.makerPairedCoverage)},
'references':{'priorOurOwnState':{'sideBalanced':.5965,'effectBalanced':.381,'meanP1AtTrueTaker':.0157,'lastMakerAgeMedianMs':19903.0,'makerFills5sMedian':0.0,'makerGrossMedian':72.0},'targetTeacher':{'lastMakerAgeMedianMs':1999.0,'makerFills5sMedian':3.0,'makerGrossMedian':864.8073450159809,'makerPairedCoverageMedian':.8067955525253958}},
'guards':['No threshold tuning.','No Target runtime data generated actions.','Fixed first-240 cohort; later collector growth excluded.']}
(OUT/'target_blind_maker_lifecycle_bootstrap_v0_report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rep,ensure_ascii=False,indent=2))
