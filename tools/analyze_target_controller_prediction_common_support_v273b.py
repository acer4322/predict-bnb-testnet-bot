from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import analyze_target_controller_fragment_ebm_v272 as v272

base = v272.base
REPORT_VERSION = "TARGET_CONTROLLER_PREDICTION_COMMON_SUPPORT_V273B"
DEFAULT_REPORT = base.ROOT / "data" / "research" / "target_controller_prediction_common_support_v273_report.json"
DEFAULT_ROWS = base.ROOT / "data" / "research" / "target_controller_prediction_common_support_v273_training_rows.csv"
CONTROL = v272.feature_specs()["CORE_STATE_PUBLIC_NO_PREDICTION"]
CURRENT_VALUE_FEATURES = CONTROL + [
    "micro_prediction_up_mid",
    "micro_prediction_distance_05",
]
CURRENT_PLUS_3S_FEATURES = CURRENT_VALUE_FEATURES + [
    "micro_prediction_delta_3s",
    "micro_prediction_range_3s",
    "micro_prediction_pressure_aligned_delta_3s",
]
EXPERIMENTS = {
    "CURRENT_VALUE_COMPLETE_CASE": {
        "required": ["micro_prediction_up_mid", "micro_prediction_distance_05"],
        "features": CURRENT_VALUE_FEATURES,
    },
    "CURRENT_PLUS_3S_COMPLETE_CASE": {
        "required": ["micro_prediction_up_mid", "micro_prediction_distance_05", "micro_prediction_delta_3s", "micro_prediction_range_3s", "micro_prediction_pressure_aligned_delta_3s"],
        "features": CURRENT_PLUS_3S_FEATURES,
    },
}


def complete(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows if all(r.get(f) is not None for f in fields)]


def fold_map(model: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    return {int(f["holdoutTargetMarketId"]): f for f in model.get("folds", []) if f.get("status") == "OK"}


def baselines(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for f in model.get("folds", []):
        if f.get("status") != "OK":
            continue
        p = int(f["trainPositives"]) / int(f["trainRows"])
        n, y = int(f["testRows"]), int(f["testPositives"])
        ll = None if not (0 < p < 1) else -(y * math.log(p) + (n-y) * math.log(1-p)) / n
        out.append({"holdoutTargetMarketId": int(f["holdoutTargetMarketId"]), "constantTrainPrevalenceLogLoss": ll, "modelLogLoss": f.get("logLoss"), "deltaModelMinusConstant": (float(f["logLoss"]) - ll if ll is not None and f.get("logLoss") is not None else None)})
    return out


def pair_summary(control: Mapping[str, Any], treatment: Mapping[str, Any]) -> dict[str, Any]:
    ca, ta = base._num(control["oof"].get("auc")), base._num(treatment["oof"].get("auc"))
    cl, tl = base._num(control["oof"].get("logLoss")), base._num(treatment["oof"].get("logLoss"))
    cf, tf = fold_map(control), fold_map(treatment)
    folds = []
    for mid in sorted(set(cf) & set(tf)):
        c, t = cf[mid], tf[mid]
        cfa, tfa = base._num(c.get("auc")), base._num(t.get("auc"))
        cfl, tfl = base._num(c.get("logLoss")), base._num(t.get("logLoss"))
        folds.append({"holdoutTargetMarketId": mid, "controlAuc": cfa, "treatmentAuc": tfa, "deltaAucTreatmentMinusControl": (tfa-cfa if tfa is not None and cfa is not None else None), "controlLogLoss": cfl, "treatmentLogLoss": tfl, "deltaLogLossTreatmentMinusControl": (tfl-cfl if tfl is not None and cfl is not None else None)})
    return {"control": control["oof"], "treatment": treatment["oof"], "deltaAucTreatmentMinusControl": (ta-ca if ta is not None and ca is not None else None), "deltaLogLossTreatmentMinusControl": (tl-cl if tl is not None and cl is not None else None), "foldDeltas": folds, "controlConstantBaselines": baselines(control), "treatmentConstantBaselines": baselines(treatment), "controlModel": control, "treatmentModel": treatment}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--states", type=Path, default=base.DEFAULT_STATES)
    p.add_argument("--micro-db", type=Path, default=base.DEFAULT_DB)
    p.add_argument("--book-db", type=Path, default=v272.DEFAULT_BOOK_DB)
    p.add_argument("--start", default=base.DEFAULT_START)
    p.add_argument("--end", default=base.DEFAULT_END)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = p.parse_args()
    start_ms, end_ms = base._iso_ms(args.start), base._iso_ms(args.end)
    states = base._load_states(args.states, start_ms, end_ms)
    snapshots, _ = base._load_micro(args.micro_db, start_ms, end_ms)
    pred_by_key, pred_audit = v272._load_8778_rows(states, args.book_db)
    if pred_audit["decision"].get("status") != "READY_FOR_V272_EBM_AB":
        raise RuntimeError("V2.7.3 requires the V2.7.2 coverage gate to pass")
    rows, join_audit = v272.build_ab_rows(states, snapshots, pred_by_key)
    results, export = {}, []
    for name, spec in EXPERIMENTS.items():
        xs = complete(rows, spec["required"])
        if len(xs) < 100:
            raise RuntimeError(f"too few complete-case rows for {name}: {len(xs)}")
        tasks = {}
        for task in ("ADD", "REPAIR"):
            c, _ = base.fit_one(xs, CONTROL, task, 3, name+"__CONTROL", False)
            t, _ = base.fit_one(xs, spec["features"], task, 3, name+"__TREATMENT", False)
            tasks[task+"_3S"] = pair_summary(c, t)
        counts = Counter(int(r["market_id"]) for r in xs)
        results[name] = {"rows": len(xs), "targetMarketRows": {str(k): v for k,v in sorted(counts.items())}, "requiredPredictionFields": spec["required"], "tasks": tasks}
        for r in xs:
            z = dict(r); z["v273_experiment"] = name; export.append(z)
    report = {"version": REPORT_VERSION, "policy": {"window": {"start": args.start, "end": args.end}, "validation": "LOMO; identical complete-case rows/folds within each pair", "horizonS": 3, "tasks": ["ADD", "REPAIR"], "purpose": "Falsify Prediction missingness/coverage as the main source of V2.7.2 lift", "guardrails": ["All included Prediction treatment fields are observed on every fitted row", "Current-value experiment excludes event age", "Identical labels/model parameters/random_state", "No live trading changes"]}, "source": {"baseTrainingRows": len(rows), "microJoinAudit": dict(join_audit), "predictionCoverageDecision": pred_audit["decision"]}, "experiments": results, "interpretation": {"valueEvidence": "Lift that persists on complete-case rows supports actual Prediction values rather than missingness", "artifactEvidence": "Lift that collapses suggests V2.7.2 benefited materially from missingness/coverage", "warning": "Still only four stress-regime Target markets"}}
    out = args.report.expanduser().resolve(); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    base._write_csv(args.rows, export)
    print(json.dumps({"version": REPORT_VERSION, "experiments": {k: {"rows": v["rows"], "targetMarketRows": v["targetMarketRows"]} for k,v in results.items()}, "report": str(out)}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
