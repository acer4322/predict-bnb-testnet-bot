from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_entry_value_dataset60_v1_preregistered.json"
DATASET = OUT_DIR / "hft_safe_floor_entry_value_dataset60_v1.json"
REPORT = OUT_DIR / "hft_safe_floor_entry_value_gate_v1_report.json"

PUBLIC = [
    "secondsLeft", "predictUpBid", "predictUpAsk", "predictUpMid", "predictDownBid", "predictDownAsk", "predictDownMid",
    "directionScore", "spotReturn1sBps", "spotReturn3sBps", "spotQueueImbalance", "spotTakerImbalance1s",
    "futuresReturn1sBps", "futuresReturn3sBps", "futuresQueueImbalance", "futuresTakerImbalance1s",
    "perpSpotBasisBps", "volatilityAlertHigh",
]
QUEUE = [
    "nativeBestBid", "nativeBestAsk", "nativeSpreadTicks", "nativeBestBidDepth", "nativeBestAskDepth",
    "upQuoteDepth", "downQuoteDepth", "upPrice", "downPrice", "pairQuoteSum", "pairLockedEdgeIfBoth",
    "nativeTradeBuyQty1s", "nativeTradeSellQty1s", "nativeTradeImbalance1s", "nativeTradeCount1s",
    "nativeTradeBuyQty3s", "nativeTradeSellQty3s", "nativeTradeImbalance3s", "nativeTradeCount3s",
    "nativeTradeBuyQty10s", "nativeTradeSellQty10s", "nativeTradeImbalance10s", "nativeTradeCount10s",
]
FEATURE_SETS = {"public": PUBLIC, "publicPlusQueue": PUBLIC + QUEUE}
EPS = 1e-9


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except Exception:
        return math.nan


def model() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=0.1, max_iter=5000, solver="lbfgs", random_state=20260823)),
        ]
    )


def tail_metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, Any]:
    both = len(set(map(int, y))) == 2
    return {
        "n": len(y),
        "tails": int(y.sum()),
        "tailRate": float(y.mean()),
        "rocAuc": float(roc_auc_score(y, probability)) if both else None,
        "averagePrecision": float(average_precision_score(y, probability)) if y.sum() else None,
        "brier": float(brier_score_loss(y, probability)),
        "meanPredictedTailProbability": float(probability.mean()),
    }


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    records = []
    for row in data["rows"]:
        record = {key: row[key] for key in ("marketId", "split", "tail", "terminalWorstCaseFloor", "settledRealizedPnlAudit", "cycleInvariantViolationCount")}
        record.update({name: finite(row["strictPastEntryState"].get(name)) for name in PUBLIC + QUEUE})
        records.append(record)
    frame = pd.DataFrame(records)
    train = frame[frame.split == "train"].copy()
    train_tail = train[train["tail"] == 1].terminalWorstCaseFloor.astype(float)
    train_nonnegative = train[train["tail"] == 0].terminalWorstCaseFloor.astype(float)
    if train_tail.empty or train_nonnegative.empty:
        raise RuntimeError("train split must contain both tail and nonnegative outcomes")
    mean_tail_value = float(train_tail.mean())
    mean_nonnegative_value = float(train_nonnegative.mean())

    models: dict[str, Pipeline] = {}
    predictions: dict[str, np.ndarray] = {}
    for name, features in FEATURE_SETS.items():
        fitted = model()
        fitted.fit(train[features], train["tail"].astype(int))
        models[name] = fitted
        predictions[name] = fitted.predict_proba(frame[features])[:, list(fitted.classes_).index(1)]
        frame[f"pTail__{name}"] = predictions[name]
        frame[f"predictedValue__{name}"] = predictions[name] * mean_tail_value + (1.0 - predictions[name]) * mean_nonnegative_value
        frame[f"act__{name}"] = frame[f"predictedValue__{name}"] > 0.0

    split_reports: dict[str, Any] = {}
    for split in ("train", "validation", "unseenHoldout"):
        selected = frame[frame.split == split].copy()
        values = selected.terminalWorstCaseFloor.astype(float).to_numpy()
        split_report: dict[str, Any] = {
            "markets": len(selected),
            "alwaysAct": {
                "actRate": 1.0,
                "realizedValue": float(values.sum()),
                "positiveMarkets": int((values > EPS).sum()),
                "negativeMarkets": int((values < -EPS).sum()),
            },
            "oracleWaitAct": {
                "actRate": float((values > EPS).mean()),
                "realizedValue": float(np.maximum(values, 0.0).sum()),
                "actMarkets": int((values > EPS).sum()),
                "waitMarkets": int((values <= EPS).sum()),
            },
            "models": {},
        }
        for name in FEATURE_SETS:
            probability = selected[f"pTail__{name}"].to_numpy(dtype=float)
            act = selected[f"act__{name}"].to_numpy(dtype=bool)
            split_report["models"][name] = {
                "tailMetrics": tail_metrics(selected["tail"].astype(int).to_numpy(), probability),
                "actRate": float(act.mean()),
                "actMarkets": int(act.sum()),
                "waitMarkets": int((~act).sum()),
                "realizedValue": float(values[act].sum()),
                "positiveActMarkets": int((values[act] > EPS).sum()),
                "negativeActMarkets": int((values[act] < -EPS).sum()),
                "meanPredictedValue": float(selected[f"predictedValue__{name}"].mean()),
            }
        split_reports[split] = split_report

    primary = "publicPlusQueue"
    validation = split_reports["validation"]["models"][primary]
    holdout = split_reports["unseenHoldout"]["models"][primary]
    violations = int(frame.cycleInvariantViolationCount.sum())
    keep = bool(
        int(validation["actMarkets"]) > 0
        and int(holdout["actMarkets"]) > 0
        and float(validation["realizedValue"]) > EPS
        and float(holdout["realizedValue"]) > EPS
        and violations == 0
    )
    threshold_probability = mean_nonnegative_value / (mean_nonnegative_value - mean_tail_value)
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_ENTRY_VALUE_GATE_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "cohort": {"markets": len(frame), "split": prereg["chronology"], "officialHftForwardUsed": False, "sealed20260816Used": False},
        "executionSemantics": data["executionSemantics"],
        "target": "full-cycle HftBacktest terminal worst-case floor for frozen contingent offset1 option; WAIT=0",
        "trainOnlyEconomics": {
            "meanTailValue": mean_tail_value,
            "meanNonnegativeValue": mean_nonnegative_value,
            "impliedMaximumTailProbabilityForAct": threshold_probability,
            "thresholdSwept": False,
        },
        "featureSets": FEATURE_SETS,
        "splitResults": split_reports,
        "rows": frame[["marketId", "split", "tail", "terminalWorstCaseFloor", "pTail__public", "predictedValue__public", "act__public", "pTail__publicPlusQueue", "predictedValue__publicPlusQueue", "act__publicPlusQueue"]].to_dict(orient="records"),
        "waitActRate": {split: split_reports[split]["models"][primary]["actRate"] for split in split_reports},
        "oracleValueCeiling": {split: split_reports[split]["oracleWaitAct"]["realizedValue"] for split in split_reports},
        "learnedPolicyRealizedValue": {split: split_reports[split]["models"][primary]["realizedValue"] for split in split_reports},
        "chronologicalUnseenOos": split_reports["unseenHoldout"],
        "cycleInvariantViolations": violations,
        "decision": "KEEP_ENTRY_VALUE_GATE_PILOT" if keep else "REJECT_ENTRY_VALUE_GATE_V1",
        "decisionReason": (
            "The preregistered public-plus-queue economic gate ACTed and retained positive realized execution value on both chronological validation and unseen holdout."
            if keep
            else "The preregistered public-plus-queue gate failed to ACT or failed positive realized execution value on validation/holdout; no threshold retuning is allowed."
        ),
        "next": (
            "Freeze this representation/gate and test one later untouched pre-official block; do not use official HFT Forward for tuning."
            if keep
            else "Reject this entry-state representation; use the Queue-Reactive generator only if it adds demonstrably new pre-fill reachability state before another real-tape holdout."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("trainOnlyEconomics", "splitResults", "waitActRate", "oracleValueCeiling", "learnedPolicyRealizedValue", "decision", "decisionReason", "next")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
