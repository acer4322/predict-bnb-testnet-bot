from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import analyze_target_controller_fragment_ebm_v272 as v272

base = v272.base

REPORT_VERSION = "TARGET_CONTROLLER_PREDICTION_COMMON_SUPPORT_V273"
DEFAULT_REPORT = base.ROOT / "data" / "research" / "target_controller_prediction_common_support_v273_report.json"
DEFAULT_ROWS = base.ROOT / "data" / "research" / "target_controller_prediction_common_support_v273_training_rows.csv"

CONTROL_FEATURES = v272.feature_specs()["CORE_STATE_PUBLIC_NO_PREDICTION"]
CURRENT_VALUE_FEATURES = CONTROL_FEATURES + [
    "micro_prediction_up_mid",
    "micro_prediction_distance_05",
]
CURRENT_3S_FEATURES = CURRENT_VALUE_FEATURES + [
    "micro_prediction_delta_3s",
    "micro_prediction_range_3s",
    "micro_prediction_pressure_aligned_delta_3s",
]

EXPERIMENTS = {
    "CURRENT_VALUE_COMPLETE_CASE": {
        "required": ["micro_prediction_up_mid", "micro_prediction_distance_05"],
        "treatment": CURRENT_VALUE_FEATURES,
        "description": "Restrict to rows with fresh <=2s Prediction mid; compare control vs actual current Prediction price value. Event age is excluded.",
    },
    "CURRENT_PLUS_3S_COMPLETE_CASE": {
        "required": [
            "micro_prediction_up_mid",
            "micro_prediction_distance_05",
            "micro_prediction_delta_3s",
            "micro_prediction_range_3s",
            "micro_prediction_pressure_aligned_delta_3s",
        ],
        "treatment": CURRENT_3S_FEATURES,
        "description": "Restrict to rows where all included current+3s Prediction values exist; compare identical rows/folds with no Prediction missingness in treatment columns.",
    },
}


def _complete(rows: Sequence[Mapping[str, Any]], required: Sequence[str]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows if all(r.get(f) is not None for f in required)]


def _fold_baselines(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fold in model.get("folds", []):
        if fold.get("status") != "OK":
            continue
        n_train = int(fold["trainRows"])
        pos_train = int(fold["trainPositives"])
        n_test = int(fold["testRows"])
        pos_test = int(fold["testPositives"])
        p = pos_train / n_train if n_train else 0.0
        if not (0.0 < p < 1.0) or not n_test:
            baseline = None
        else:
            baseline = -(
                pos_test * math.log(p) + (n_test - pos_test) * math.log(1.0 - p)
            ) / n_test
        out.append({
            "holdoutTargetMarketId": int(fold["holdoutTargetMarketId"]),
            "trainPrevalence": p,
            "constantTrainPrevalenceLogLoss": baseline,
            "modelLogLoss": fold.get("logLoss"),
            "deltaModelMinusConstant": (
                float(fold["logLoss"]) - baseline
                if baseline is not None and fold.get("logLoss") is not None
                else None
            ),
        })
    return out


def _run_pair(rows: list[dict[str, Any]], task: str, treatment_features: list[str], experiment: str) -> dict[str, Any]:
    control, _ = base.fit_one(rows, CONTROL_FEATURES, task, 3, f"{experiment}__CONTROL", collect_local=False)
    treatment, _ = base.fit_one(rows, treatment_features, task, 3, f"{experiment}__TREATMENT", collect_local=False)
    summary = v272._ab_summary({
        f"{task}_3S__CORE_STATE_PUBLIC_NO_PREDICTION": control,
        f"{task}_3S__CORE_STATE_PUBLIC_8778_PREDICTION": treatment,
    }, [3])[f"{task}_3S"]
    summary["controlConstantBaselines"] = _fold_baselines(control)
    summary["treatmentConstantBaselines"] = _fold_baselines(treatment)
    summary["controlModel"] = control
    summary["treatmentModel"] = treatment
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="V2.7.3 complete-case falsification test for Prediction value vs missingness artifact.")
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
        raise RuntimeError("V2.7.3 requires the V2.7.2 Prediction coverage gate to pass")

    rows, join_audit = v272.build_ab_rows(states, snapshots, pred_by_key)
    results: dict[str, Any] = {}
    row_export: list[dict[str, Any]] = []

    for exp_name, spec in EXPERIMENTS.items():
        complete = _complete(rows, spec["required"])
        if len(complete) < 100:
            raise RuntimeError(f"too few complete-case rows for {exp_name}: {len(complete)}")
        market_counts = Counter(int(r["market_id"]) for r in complete)
        task_results = {}
        for task in ("ADD", "REPAIR"):
            task_results[f"{task}_3S"] = _run_pair(complete, task, list(spec["treatment"]), exp_name)
        results[exp_name] = {
            "description": spec["description"],
            "requiredPredictionFields": list(spec["required"]),
            "rows": len(complete),
            "targetMarketRows": {str(k): v for k, v in sorted(market_counts.items())},
            "tasks": task_results,
        }
        for r in complete:
            x = dict(r)
            x["v273_experiment"] = exp_name
            row_export.append(x)

    report = {
        "version": REPORT_VERSION,
        "policy": {
            "window": {"start": args.start, "end": args.end},
            "validation": "leave-one-Target-market-out; identical complete-case rows/folds within each A/B pair",
            "horizonS": 3,
            "tasks": ["ADD", "REPAIR"],
            "strictPast": "same receivedStrict 8778 source as V2.7.2",
            "purpose": "Falsify the alternative explanation that V2.7.2 lift came mainly from Prediction missingness/coverage rather than Prediction values.",
            "guardrails": [
                "Rows are restricted before fitting so every included Prediction treatment field is observed in both train and holdout rows.",
                "Current-value experiment excludes Prediction event age.",
                "Control and treatment use identical rows, labels, LOMO folds, EBM parameters and random_state.",
                "No live/Echtgeld strategy changes.",
            ],
        },
        "source": {
            "states": str(args.states.expanduser().resolve()),
            "microDb": str(args.micro_db.expanduser().resolve()),
            "bookDb": str(args.book_db.expanduser().resolve()),
            "baseTrainingRows": len(rows),
            "microJoinAudit": dict(join_audit),
            "predictionCoverageDecision": pred_audit["decision"],
        },
        "experiments": results,
        "interpretation": {
            "strongValueEvidence": "Treatment improves AUC and logloss across multiple LOMO holdouts on complete-case rows, especially if the model also beats or approaches the train-prevalence constant baseline.",
            "missingnessArtifact": "V2.7.2 lift disappears on complete-case rows; then NaN/coverage pattern likely contributed materially.",
            "warning": "Four stress-regime Target markets remain a small fragment; this is a falsification step, not deployment validation.",
        },
    }

    out = args.report.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    base._write_csv(args.rows, row_export)
    print(json.dumps({
        "version": REPORT_VERSION,
        "experiments": {k: {"rows": v["rows"], "targetMarketRows": v["targetMarketRows"]} for k, v in results.items()},
        "report": str(out),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
