from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.metrics import accuracy_score, balanced_accuracy_score
from sklearn.tree import DecisionTreeClassifier, export_text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_r21_cooperation_response_belief_curriculum_v1 import (  # noqa: E402
    BRANCHES,
    FAULTS,
    run_branch,
    summarize,
)


OUT = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
VERSION = "HFT_R21_COOPERATION_RESPONSE_HEAD_V1"
DEVELOPMENT_FILES = (
    OUT / "hft_r21_cooperation_response_belief_curriculum_v1_market1573662.json",
    OUT / "hft_r21_cooperation_response_belief_curriculum_v1_market1573848.json",
)
MODEL_PATH = OUT / "hft_r21_cooperation_response_head_v1.joblib"
TRAIN_REPORT_PATH = OUT / "hft_r21_cooperation_response_head_v1_train_report.json"
FEATURE_NAMES = (
    "r21_actionable_residual_mass_fraction",
)
ACTIVE = "ACTIVE_ONCE"
WAIT = "NATIVE_PASSIVE"
MATERIAL_REDUCTION = 0.30
MAX_ALLOWED_METRIC_WORSENING = 0.10
EPS = 1e-9


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return float(default)
    return number if math.isfinite(number) else float(default)


def reduction(baseline: float, candidate: float) -> float:
    baseline = finite(baseline)
    candidate = finite(candidate)
    if abs(baseline) <= EPS:
        return 0.0 if abs(candidate) <= EPS else -math.inf
    return (baseline - candidate) / abs(baseline)


def context_features(context: dict[str, Any]) -> list[float]:
    anchor = context.get("anchor") or {}
    belief = anchor.get("belief") or {}
    features = anchor.get("features") or {}
    abs_tracking = max(abs(finite(anchor.get("trackingError"))), EPS)
    bounded_qty = max(finite(belief.get("originalQty")), 0.0)
    working_remaining = max(finite(features.get("workingRecoveryRemainingQty")), 0.0)
    passive_probability = min(max(finite(belief.get("probability")), 0.0), 1.0)
    working_fraction = min(working_remaining / abs_tracking, 2.0)
    bounded_capacity = min(bounded_qty / abs_tracking, 1.0)
    return [
        passive_probability * working_fraction * bounded_capacity,
    ]


def recovery_label(context: dict[str, Any]) -> tuple[str, dict[str, float]]:
    tracking = context["trackingByBranch"]
    area = context["areaByBranch"]
    residual_reduction = reduction(tracking[WAIT], tracking[ACTIVE])
    area_reduction = reduction(area[WAIT], area[ACTIVE])
    material = max(residual_reduction, area_reduction) >= MATERIAL_REDUCTION
    no_material_harm = (
        residual_reduction >= -MAX_ALLOWED_METRIC_WORSENING
        and area_reduction >= -MAX_ALLOWED_METRIC_WORSENING
    )
    return (ACTIVE if material and no_material_harm else WAIT), {
        "terminalResidualReduction": residual_reduction,
        "trackingErrorAreaReduction": area_reduction,
    }


def load_development_contexts() -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    for path in DEVELOPMENT_FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for context in payload.get("contexts") or []:
            if bool(context.get("usable")):
                contexts.append(context)
    contexts.sort(key=lambda row: (int(row["marketId"]), str(row["fault"])))
    return contexts


def fit_response_head(contexts: list[dict[str, Any]]) -> tuple[DecisionTreeClassifier, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    x: list[list[float]] = []
    y: list[int] = []
    for context in contexts:
        label, advantage = recovery_label(context)
        vector = context_features(context)
        x.append(vector)
        y.append(int(label == ACTIVE))
        rows.append(
            {
                "marketId": int(context["marketId"]),
                "fault": str(context["fault"]),
                "stateHash": str((context.get("anchor") or {}).get("stateHash") or ""),
                "features": dict(zip(FEATURE_NAMES, vector)),
                "label": label,
                **advantage,
            }
        )
    if len(set(y)) != 2:
        raise RuntimeError("Development curriculum lacks WAIT/ACTIVE label dispersion")
    model = DecisionTreeClassifier(
        max_depth=1,
        min_samples_leaf=1,
        class_weight="balanced",
        random_state=20260824,
    )
    model.fit(np.asarray(x, dtype=float), np.asarray(y, dtype=int))
    prediction = model.predict(np.asarray(x, dtype=float))
    probability = model.predict_proba(np.asarray(x, dtype=float))[:, list(model.classes_).index(1)]
    for row, pred, prob in zip(rows, prediction, probability):
        row["trainPrediction"] = ACTIVE if int(pred) else WAIT
        row["trainActiveProbability"] = float(prob)
    return model, rows


def build_training_artifacts() -> tuple[dict[str, Any], DecisionTreeClassifier]:
    contexts = load_development_contexts()
    model, rows = fit_response_head(contexts)
    truth = [int(row["label"] == ACTIVE) for row in rows]
    prediction = [int(row["trainPrediction"] == ACTIVE) for row in rows]
    model_artifact = {
        "version": VERSION,
        "researchOnly": True,
        "runtimeDeployable": False,
        "role": "R2_LOGIC_RESPONSE_HEAD",
        "r21ExecutionAuthority": False,
        "r21OrderMutationAuthority": False,
        "r21DesiredPortfolioMutationAuthority": False,
        "featureNames": list(FEATURE_NAMES),
        "labelRule": {
            "active": "ACTIVE_ONCE only when either recovery metric improves >=30% and neither worsens >10% versus native passive",
            "wait": "otherwise preserve native passive continuation",
            "pnlAndFloor": "audit only",
        },
        "learner": {
            "type": "DecisionTreeClassifier",
            "maxDepth": 1,
            "minSamplesLeaf": 1,
            "classWeight": "balanced",
            "randomState": 20260824,
            "hyperparameterSweep": False,
        },
        "model": model,
    }
    joblib.dump(model_artifact, MODEL_PATH)
    report = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "developmentOutcomeStatus": "REVEALED_DEVELOPMENT_ONLY",
        "developmentFiles": [str(path.relative_to(ROOT)) for path in DEVELOPMENT_FILES],
        "featureNames": list(FEATURE_NAMES),
        "rows": rows,
        "summary": {
            "contexts": len(rows),
            "waitLabels": sum(row["label"] == WAIT for row in rows),
            "activeLabels": sum(row["label"] == ACTIVE for row in rows),
            "trainWaitPredictions": sum(row["trainPrediction"] == WAIT for row in rows),
            "trainActivePredictions": sum(row["trainPrediction"] == ACTIVE for row in rows),
            "trainAccuracy": float(accuracy_score(truth, prediction)),
            "trainBalancedAccuracy": float(balanced_accuracy_score(truth, prediction)),
        },
        "tree": export_text(model, feature_names=list(FEATURE_NAMES)),
        "modelPath": str(MODEL_PATH.relative_to(ROOT)),
        "interpretation": "Training fit is diagnostic only; the response head is frozen before holdout ACTIVE outcomes are generated.",
    }
    TRAIN_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, model


def predict_context(model: DecisionTreeClassifier, context: dict[str, Any]) -> tuple[str, float, list[float]]:
    vector = context_features(context)
    array = np.asarray([vector], dtype=float)
    pred = int(model.predict(array)[0])
    active_index = list(model.classes_).index(1)
    probability = float(model.predict_proba(array)[0, active_index])
    return (ACTIVE if pred else WAIT), probability, vector


def aggregate_metrics(contexts: list[dict[str, Any]], predictions: dict[tuple[int, str], str]) -> dict[str, Any]:
    native_residual = 0.0
    selected_residual = 0.0
    oracle_residual = 0.0
    native_area = 0.0
    selected_area = 0.0
    oracle_area = 0.0
    negative_active = 0
    material_oracle = 0
    captured_material = 0
    rows = []
    for context in contexts:
        key = (int(context["marketId"]), str(context["fault"]))
        selected = predictions[key]
        label, advantages = recovery_label(context)
        tracking = context["trackingByBranch"]
        area = context["areaByBranch"]
        native_residual += finite(tracking[WAIT])
        selected_residual += finite(tracking[selected])
        oracle_residual += min(finite(tracking[WAIT]), finite(tracking[ACTIVE]))
        native_area += finite(area[WAIT])
        selected_area += finite(area[selected])
        oracle_area += min(finite(area[WAIT]), finite(area[ACTIVE]))
        if selected == ACTIVE and (
            advantages["terminalResidualReduction"] < 0
            or advantages["trackingErrorAreaReduction"] < 0
        ):
            negative_active += 1
        if label == ACTIVE:
            material_oracle += 1
            captured_material += int(selected == ACTIVE)
        rows.append(
            {
                "marketId": key[0],
                "fault": key[1],
                "usable": bool(context.get("usable")),
                "predictedAction": selected,
                "materialRecoveryLabel": label,
                **advantages,
                "nativeResidual": finite(tracking[WAIT]),
                "selectedResidual": finite(tracking[selected]),
                "nativeArea": finite(area[WAIT]),
                "selectedArea": finite(area[selected]),
                "nativeFloorAudit": finite(context["floorAuditByBranch"][WAIT]),
                "selectedFloorAudit": finite(context["floorAuditByBranch"][selected]),
            }
        )
    residual_improvement = reduction(native_residual, selected_residual)
    area_improvement = reduction(native_area, selected_area)
    return {
        "rows": rows,
        "waitCount": sum(row["predictedAction"] == WAIT for row in rows),
        "activeCount": sum(row["predictedAction"] == ACTIVE for row in rows),
        "nativeResidual": native_residual,
        "selectedResidual": selected_residual,
        "oracleResidual": oracle_residual,
        "terminalResidualImprovement": residual_improvement,
        "nativeTrackingErrorArea": native_area,
        "selectedTrackingErrorArea": selected_area,
        "oracleTrackingErrorArea": oracle_area,
        "trackingErrorAreaImprovement": area_improvement,
        "materialActiveOracleContexts": material_oracle,
        "capturedMaterialActiveContexts": captured_material,
        "negativeRecoveryActiveContexts": negative_active,
    }


def run_holdout(market_id: int, model: DecisionTreeClassifier) -> dict[str, Any]:
    lock_path = OUT / f"hft_r21_cooperation_response_head_v1_holdout{market_id}_locked_predictions.json"
    report_path = OUT / f"hft_r21_cooperation_response_head_v1_holdout{market_id}_report.json"
    if lock_path.exists() or report_path.exists():
        raise FileExistsError(f"Refusing to overwrite prior holdout artifacts: {lock_path.name} / {report_path.name}")

    native_rows = []
    locked_rows = []
    predictions: dict[tuple[int, str], str] = {}
    for fault in FAULTS:
        row = run_branch(market_id, fault, WAIT)
        native_rows.append(row)
        provisional = summarize([row])
        # A one-branch context is intentionally incomplete; build the prediction
        # input from the matched pre-divergence anchor only.
        context = {"marketId": market_id, "fault": fault, "anchor": row.get("anchor")}
        action, probability, vector = predict_context(model, context)
        predictions[(market_id, fault)] = action
        locked_rows.append(
            {
                "marketId": market_id,
                "fault": fault,
                "stateHash": str((row.get("anchor") or {}).get("stateHash") or ""),
                "features": dict(zip(FEATURE_NAMES, vector)),
                "predictedAction": action,
                "activeProbability": probability,
                "nativeTerminalOutcomeExcludedFromModelInput": True,
                "anchorPresent": bool(row.get("anchor")),
                "faultUsed": int(row.get("makerFaultsUsed") or 0) > 0,
            }
        )
        del provisional

    locked_payload = {
        "version": VERSION,
        "lockedBeforeAlternativeOutcomes": True,
        "marketId": market_id,
        "modelPath": str(MODEL_PATH.relative_to(ROOT)),
        "featureNames": list(FEATURE_NAMES),
        "predictions": locked_rows,
        "guard": "Only strict-past anchor/R2.1 fields are model inputs. Native continuation was needed to reach the fault anchor; its terminal outcome is not an input.",
    }
    lock_path.write_text(json.dumps(locked_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"lockedPredictions": str(lock_path), "predictions": locked_rows}, ensure_ascii=False), flush=True)

    alternative_rows = []
    for fault in FAULTS:
        for branch in ("FORMAL_WAIT", ACTIVE):
            alternative_rows.append(run_branch(market_id, fault, branch))
            print(json.dumps({"marketId": market_id, "fault": fault, "revealedBranch": branch}, ensure_ascii=False), flush=True)
    all_rows = native_rows + alternative_rows
    contexts = summarize(all_rows)
    aggregate = aggregate_metrics(contexts, predictions)
    usable = all(bool(context.get("usable")) for context in contexts) and len(contexts) == len(FAULTS)
    semantics_clean = all(
        row.get("cycleInvariantViolationCount") == 0
        and not (row.get("r21") or {}).get("strictPastViolations")
        for row in all_rows
    )
    no_harm = (
        aggregate["terminalResidualImprovement"] >= -EPS
        and aggregate["trackingErrorAreaImprovement"] >= -EPS
        and aggregate["negativeRecoveryActiveContexts"] == 0
    )
    material_ceiling = aggregate["materialActiveOracleContexts"] > 0
    captured = (
        aggregate["capturedMaterialActiveContexts"] > 0
        if material_ceiling
        else aggregate["activeCount"] == 0
    )
    if usable and semantics_clean and no_harm and captured:
        decision = "KEEP_SMALL_R21_RESPONSE_HEAD_FOR_ONE_MORE_CHRONOLOGICAL_REPLICATION"
    elif usable and semantics_clean and not no_harm:
        decision = "REJECT_R21_RESPONSE_HEAD_V1"
    else:
        decision = "NEED_MORE_DATA_R21_RESPONSE_HEAD_V1"
    payload = {
        "version": VERSION,
        "researchOnly": True,
        "graduationEligible": False,
        "executionSemantics": "HftBacktest + Predict Execution Tape V1; risk queue; actual-fill-only state; existing runner latency/partial-fill/cancel lifecycle",
        "developmentMarkets": [1573662, 1573848],
        "chronologicalHoldoutMarket": market_id,
        "lockedPredictionPath": str(lock_path.relative_to(ROOT)),
        "r21ExecutionAuthority": False,
        "rows": all_rows,
        "contexts": contexts,
        "aggregate": aggregate,
        "gates": {
            "allContextsUsable": usable,
            "strictPastAndSemanticsClean": semantics_clean,
            "selectedPolicyNoRecoveryHarm": no_harm,
            "materialActiveOracleExists": material_ceiling,
            "capturedMaterialActiveWhenAvailable": captured,
        },
        "decision": decision,
        "nextExperiment": "If KEEP, freeze without tuning and run one later opened-train replication with the same two Maker fault families. If REJECT, do not threshold-sweep this response head.",
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout-market", type=int, default=1574038)
    parser.add_argument("--train-only", action="store_true")
    parser.add_argument("--use-frozen-model", action="store_true")
    args = parser.parse_args()
    if args.use_frozen_model:
        artifact = joblib.load(MODEL_PATH)
        if not isinstance(artifact, dict) or artifact.get("version") != VERSION:
            raise RuntimeError("Frozen response-head artifact has the wrong contract/version")
        model = artifact["model"]
        print(json.dumps({"frozenModelLoaded": str(MODEL_PATH), "version": VERSION}, ensure_ascii=False), flush=True)
    else:
        report, model = build_training_artifacts()
        print(json.dumps({"train": report["summary"], "tree": report["tree"]}, ensure_ascii=False, indent=2), flush=True)
    if not args.train_only:
        result = run_holdout(args.holdout_market, model)
        print(json.dumps({"ok": True, "decision": result["decision"], "aggregate": result["aggregate"], "gates": result["gates"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
