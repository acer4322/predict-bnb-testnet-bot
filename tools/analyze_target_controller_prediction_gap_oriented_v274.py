from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import analyze_target_controller_fragment_ebm_v272 as v272
import analyze_target_controller_prediction_common_support_v273b as v273

base = v272.base
REPORT_VERSION = "TARGET_CONTROLLER_PREDICTION_GAP_ORIENTED_V274"
DEFAULT_REPORT = base.ROOT / "data" / "research" / "target_controller_prediction_gap_oriented_v274_report.json"
DEFAULT_ROWS = base.ROOT / "data" / "research" / "target_controller_prediction_gap_oriented_v274_training_rows.csv"

CONTROL = list(v273.CONTROL)
REQUIRED = [
    "micro_prediction_up_mid",
    "micro_prediction_distance_05",
    "micro_prediction_delta_3s",
    "micro_prediction_range_3s",
    "micro_prediction_pressure_aligned_delta_3s",
]
RAW_FEATURES = CONTROL + [
    "micro_prediction_up_mid",
    "micro_prediction_distance_05",
    "micro_prediction_delta_3s",
    "micro_prediction_range_3s",
    "micro_prediction_pressure_aligned_delta_3s",
]
ORIENTED_FEATURES = CONTROL + [
    "prediction_repair_side_mid",
    "micro_prediction_distance_05",
    "prediction_repair_side_delta_3s",
    "micro_prediction_range_3s",
    "micro_prediction_pressure_aligned_delta_3s",
]


def _sign(value: Any) -> int:
    x = base._num(value)
    if x is None:
        return 0
    return 1 if x > 1e-9 else -1 if x < -1e-9 else 0


def add_gap_oriented_prediction(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    up_mid = base._num(row.get("micro_prediction_up_mid"))
    delta = base._num(row.get("micro_prediction_delta_3s"))
    gap_sign = _sign(row.get("payoff_gap"))

    if up_mid is None:
        repair_mid = None
    elif gap_sign > 0:
        # payoff_gap = settle_up - settle_down > 0 => DOWN is the weaker/repair side.
        repair_mid = 1.0 - up_mid
    elif gap_sign < 0:
        # payoff_gap < 0 => UP is the weaker/repair side.
        repair_mid = up_mid
    else:
        repair_mid = 0.5

    if delta is None:
        repair_delta = None
    elif gap_sign > 0:
        # For DOWN repair side, DOWN delta is the negative of UP delta.
        repair_delta = -delta
    elif gap_sign < 0:
        repair_delta = delta
    else:
        repair_delta = 0.0

    out["prediction_payoff_gap_sign"] = gap_sign
    out["prediction_repair_side"] = "DOWN" if gap_sign > 0 else "UP" if gap_sign < 0 else "BALANCED"
    out["prediction_repair_side_mid"] = repair_mid
    out["prediction_repair_side_delta_3s"] = repair_delta
    return out


def _three_way_summary(control: Mapping[str, Any], raw: Mapping[str, Any], oriented: Mapping[str, Any]) -> dict[str, Any]:
    raw_vs_control = v273.pair_summary(control, raw)
    oriented_vs_control = v273.pair_summary(control, oriented)
    oriented_vs_raw = v273.pair_summary(raw, oriented)
    return {
        "control": control.get("oof"),
        "raw": raw.get("oof"),
        "oriented": oriented.get("oof"),
        "rawVsControl": raw_vs_control,
        "orientedVsControl": oriented_vs_control,
        "orientedVsRaw": oriented_vs_raw,
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="V2.7.4: test portfolio-gap-oriented Prediction features for REPAIR hazard."
    )
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
        raise RuntimeError("V2.7.4 requires the V2.7.2 Prediction coverage gate to pass")

    rows, join_audit = v272.build_ab_rows(states, snapshots, pred_by_key)
    complete = v273.complete(rows, REQUIRED)
    complete = [add_gap_oriented_prediction(r) for r in complete]
    if len(complete) < 100:
        raise RuntimeError(f"too few complete-case rows: {len(complete)}")

    # One pre-specified task/horizon only. This is a mechanism test, not model tuning.
    control, _ = base.fit_one(complete, CONTROL, "REPAIR", 3, "V274__CONTROL", False)
    raw, _ = base.fit_one(complete, RAW_FEATURES, "REPAIR", 3, "V274__RAW", False)
    oriented, _ = base.fit_one(complete, ORIENTED_FEATURES, "REPAIR", 3, "V274__GAP_ORIENTED", False)

    counts = Counter(int(r["market_id"]) for r in complete)
    gap_sign_counts = Counter(str(r["prediction_repair_side"]) for r in complete)
    summary = _three_way_summary(control, raw, oriented)

    report = {
        "version": REPORT_VERSION,
        "policy": {
            "window": {"start": args.start, "end": args.end},
            "validation": "LOMO; identical complete-case rows/folds for control/raw/oriented",
            "task": "REPAIR",
            "horizonS": 3,
            "purpose": "Test an analytically defined orientation: Prediction price is expressed on the side that would reduce the current total payoff_gap.",
            "orientation": {
                "payoffGapDefinition": "settle_up - settle_down",
                "payoffGapPositive": "DOWN is the weaker/repair side; repair_side_mid = 1 - UP_mid; repair_side_delta_3s = -UP_delta_3s",
                "payoffGapNegative": "UP is the weaker/repair side; repair_side_mid = UP_mid; repair_side_delta_3s = UP_delta_3s",
                "payoffGapZero": "balanced; repair_side_mid=0.5 and repair_side_delta_3s=0",
            },
            "guardrails": [
                "Orientation is derived from portfolio payoff semantics, not selected from V2.7.3 feature importance.",
                "Same 8778 receivedStrict source and strict-past reconstruction as V2.7.2/V2.7.3.",
                "Same complete-case support as V2.7.3 CURRENT_PLUS_3S_COMPLETE_CASE.",
                "Raw and oriented arms have the same number of Prediction treatment columns.",
                "No hyperparameter tuning and no live trading changes.",
                "This four-market stress fragment is development evidence only; any surviving mechanism requires independent-window validation.",
            ],
        },
        "source": {
            "baseTrainingRows": len(rows),
            "completeCaseRows": len(complete),
            "targetMarketRows": {str(k): v for k, v in sorted(counts.items())},
            "repairSideRows": dict(gap_sign_counts),
            "microJoinAudit": dict(join_audit),
            "predictionCoverageDecision": pred_audit["decision"],
        },
        "features": {
            "required": REQUIRED,
            "rawPredictionFeatures": [f for f in RAW_FEATURES if f not in CONTROL],
            "orientedPredictionFeatures": [f for f in ORIENTED_FEATURES if f not in CONTROL],
        },
        "result": summary,
        "models": {
            "control": control,
            "raw": raw,
            "oriented": oriented,
        },
        "interpretation": {
            "supportsOrientation": "Oriented beats raw on OOF AUC and logloss and improves multiple LOMO folds without a single-market-only lift.",
            "rejectsOrientation": "Oriented is flat/worse than raw; raw UP-mid orientation was not the main reason for V2.7.3 instability.",
            "warning": "Even a positive result is not deployment evidence because the hypothesis was developed after inspecting the stress fragment.",
        },
    }

    out = args.report.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    base._write_csv(args.rows, complete)
    print(json.dumps({
        "version": REPORT_VERSION,
        "rows": len(complete),
        "targetMarketRows": report["source"]["targetMarketRows"],
        "control": summary["control"],
        "raw": summary["raw"],
        "oriented": summary["oriented"],
        "orientedVsRaw": {
            "deltaAuc": summary["orientedVsRaw"]["deltaAucTreatmentMinusControl"],
            "deltaLogLoss": summary["orientedVsRaw"]["deltaLogLossTreatmentMinusControl"],
        },
        "report": str(out),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
