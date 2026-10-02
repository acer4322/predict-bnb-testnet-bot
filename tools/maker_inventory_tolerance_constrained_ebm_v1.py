from __future__ import annotations

import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from interpret.glassbox import ExplainableBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
V0 = ROOT / "tools" / "maker_inventory_tolerance_contextual_ebm_v0.py"
spec = importlib.util.spec_from_file_location("inv_budget_v0", V0)
v0 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = v0
spec.loader.exec_module(v0)
base = v0.base

VERSION = "MAKER_INVENTORY_TOLERANCE_CONSTRAINED_EBM_V1"
REPORT = ROOT / "data" / "research" / "maker_inventory_tolerance_constrained_ebm_v1_report.json"
MODEL_DIR = ROOT / "data" / "research" / "maker_inventory_tolerance_ebm_v1"
DATASET = v0.DATASET
FEATURES = v0.FEATURES
EPS = 1e-9


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{f: r.get(f) for f in FEATURES} for r in rows], columns=FEATURES)


def model() -> ExplainableBoostingRegressor:
    return ExplainableBoostingRegressor(
        feature_names=FEATURES,
        max_bins=64,
        max_interaction_bins=32,
        interactions=4,
        outer_bags=4,
        learning_rate=0.04,
        max_rounds=3500,
        early_stopping_rounds=80,
        min_samples_leaf=6,
        n_jobs=-2,
        random_state=20260819,
    )


def top_terms(m: ExplainableBoostingRegressor, n: int = 10) -> list[dict[str, Any]]:
    imps = list(m.term_importances()); names = list(m.term_names_)
    idx = sorted(range(len(imps)), key=lambda i: float(imps[i]), reverse=True)[:n]
    return [{"term": str(names[i]), "importance": float(imps[i])} for i in idx]


def stat(xs: list[float]) -> dict[str, Any]:
    return base.stats([float(x) for x in xs])


def evaluate(models: dict[str, ExplainableBoostingRegressor], rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    X = frame(rows)
    p_mtm = models["mtm"].predict(X)
    p_floor = models["floor"].predict(X)
    p_net = models["net"].predict(X)
    chosen_high: list[bool] = []
    dyn_mtm=[]; low_mtm=[]; oracle_mtm=[]
    dyn_floor=[]; low_floor=[]; dyn_net=[]; low_net=[]; dyn_pair=[]; low_pair=[]
    safe_true=0; safe_chosen=0; unsafe_high=0
    for r,pm,pf,pn in zip(rows,p_mtm,p_floor,p_net):
        high = float(pm) > 0 and float(pf) >= 0 and float(pn) <= 0
        chosen_high.append(high)
        dm=float(r['highMinusLowMtm']); df=float(r['highMinusLowFloor']); dn=float(r['highMinusLowAbsNet'])
        true_safe = dm > EPS and df >= -EPS and dn <= EPS
        safe_true += int(true_safe)
        safe_chosen += int(high and true_safe)
        unsafe_high += int(high and not true_safe)
        dyn_mtm.append(float(r['highMtmUtility'] if high else r['lowMtmUtility']))
        low_mtm.append(float(r['lowMtmUtility']))
        oracle_mtm.append(max(float(r['lowMtmUtility']),float(r['highMtmUtility'])))
        dyn_floor.append(float(r['highFloorDelta'] if high else r['lowFloorDelta']))
        low_floor.append(float(r['lowFloorDelta']))
        dyn_net.append(float(r['highAbsNetDelta'] if high else r['lowAbsNetDelta']))
        low_net.append(float(r['lowAbsNetDelta']))
        dyn_pair.append(float(r['highPairEdgeDelta'] if high else r['lowPairEdgeDelta']))
        low_pair.append(float(r['lowPairEdgeDelta']))
    return {
        'n':len(rows),
        'chosenCounts':{'LOW18':sum(not x for x in chosen_high),'HIGH54':sum(chosen_high)},
        'trueSafeBeneficialHighStates':safe_true,
        'capturedSafeBeneficialHighStates':safe_chosen,
        'unsafeHighSelections':unsafe_high,
        'precisionOfHighSelection': safe_chosen/sum(chosen_high) if sum(chosen_high) else None,
        'recallOfSafeHigh': safe_chosen/safe_true if safe_true else None,
        'mtm':{
            'dynamic':stat(dyn_mtm),'fixedLOW18':stat(low_mtm),'oracle':stat(oracle_mtm),
            'dynamicMeanGainVsLOW18':statistics.mean(dyn_mtm)-statistics.mean(low_mtm),
            'dynamicSumGainVsLOW18':sum(dyn_mtm)-sum(low_mtm),
        },
        'risk':{
            'floorDynamic':stat(dyn_floor),'floorLOW18':stat(low_floor),
            'absNetDynamic':stat(dyn_net),'absNetLOW18':stat(low_net),
            'pairEdgeDynamic':stat(dyn_pair),'pairEdgeLOW18':stat(low_pair),
        }
    }


def main() -> int:
    rows = pd.read_csv(DATASET).to_dict('records')
    rep0 = json.loads(v0.REPORT.read_text(encoding='utf-8'))
    split = rep0['split']
    sets={k:set(split[k+'Markets']) for k in ('train','validation','test')}
    def part(k:str): return [r for r in rows if int(r['marketId']) in sets[k]]
    train=part('train')
    X=frame(train)
    targets={
        'mtm':[float(r['highMinusLowMtm']) for r in train],
        'floor':[float(r['highMinusLowFloor']) for r in train],
        'net':[float(r['highMinusLowAbsNet']) for r in train],
    }
    models={}
    for k,y in targets.items():
        m=model(); m.fit(X,y); models[k]=m
    MODEL_DIR.mkdir(parents=True,exist_ok=True)
    arts={}
    for k,m in models.items():
        p=MODEL_DIR/f'{k}_high54_minus_low18_20s.joblib'
        joblib.dump({'reportVersion':VERSION,'researchOnly':True,'runtimePromotionAllowed':False,'task':k,'features':FEATURES,'model':m},p)
        arts[k]=str(p)
    results={k:evaluate(models,part(k)) for k in ('train','validation','test')}
    report={
        'reportVersion':VERSION,'researchOnly':True,'liveTradingChanges':False,
        'purpose':'Constrained inventory-budget permission model. HIGH54 is allowed only if separate EBMs predict MTM improvement, no floor deterioration, and no abs-net deterioration versus LOW18.',
        'decisionRule':'HIGH54 iff pred(high-low MTM)>0 AND pred(high-low floor)>=0 AND pred(high-low absNet)<=0; otherwise LOW18. No weighted composite and no threshold sweep.',
        'dataset':str(DATASET),'split':split,'features':FEATURES,
        'models':{k:{'artifact':arts[k],'topTerms':top_terms(models[k])} for k in models},
        'results':results,
        'guard':[
            'Uses same chronological split as V0; no validation/test-driven threshold tuning.',
            'No winner/settlement/Target event is used in features or labels.',
            'Risk constraints are lexicographic gates rather than an arbitrary weighted utility.',
            'This remains local 20s counterfactual replay from LOW18 historical states, not a full-policy causal counterfactual.',
            'Do not modify frozen 8785 R1 from this reused historical experiment.'
        ]
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'validation':results['validation'],'test':results['test'],'topTerms':{k:report['models'][k]['topTerms'][:6] for k in models},'report':str(REPORT)},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
