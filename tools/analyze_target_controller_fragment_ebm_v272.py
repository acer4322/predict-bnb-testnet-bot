from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_fragment_ebm_v271 as controlmod
import audit_target_controller_prediction_8778_v272 as predmod

base = controlmod.base
bookmod = predmod.bookmod

REPORT_VERSION = "TARGET_CONTROLLER_FRAGMENT_EBM_V272_8778_AB"
DEFAULT_BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_controller_fragment_ebm_v272_report.json"
DEFAULT_ROWS = ROOT / "data" / "research" / "target_controller_fragment_ebm_v272_training_rows.csv"
DEFAULT_LOCAL = ROOT / "data" / "research" / "target_controller_fragment_ebm_v272_local_explanations.csv"
PREDICTION_FRESHNESS_MS = 2000


def _is_prediction_feature(name: str) -> bool:
    return name.startswith("micro_prediction_")


def feature_specs() -> dict[str, list[str]]:
    public_no_prediction = [f for f in base.PUBLIC_FEATURES if not _is_prediction_feature(f)]
    return {
        "CORE_STATE_PUBLIC_NO_PREDICTION": list(base.STATE_CORE) + public_no_prediction,
        "CORE_STATE_PUBLIC_8778_PREDICTION": list(base.STATE_CORE) + list(base.PUBLIC_FEATURES),
    }


def _load_8778_rows(
    states: Sequence[Mapping[str, Any]], book_db: Path
) -> tuple[dict[tuple[int, int], dict[str, Any]], dict[str, Any]]:
    market_ids = sorted({int(r["market_id"]) for r in states})
    with predmod._open_ro(book_db) as conn:
        bookmod.require_table(conn, bookmod.BOOK_TABLE)
        updates = bookmod.load_updates(conn, market_ids)
    source_states = [
        {"market_id": int(r["market_id"]), "sample_ms": int(r["sample_ms"])}
        for r in states
    ]
    audited = predmod.audit_rows(source_states, updates, predmod.PRIMARY_MODE)
    summary = predmod._summarize(audited)
    decision = predmod._decision(summary)
    by_key = {(int(r["market_id"]), int(r["sample_ms"])): dict(r) for r in audited}
    return by_key, {
        "summary": summary,
        "decision": decision,
        "retained8778UpdatesByTargetMarket": {
            str(mid): len(updates.get(mid, [])) for mid in market_ids
        },
    }


def _fresh_mid(pred: Mapping[str, Any] | None) -> float | None:
    if not pred or not bool(pred.get("fresh_2s")):
        return None
    return base._num(pred.get("prediction_mid"))


def _overlay_current_prediction(
    rows: list[dict[str, Any]],
    pred_by_key: Mapping[tuple[int, int], Mapping[str, Any]],
) -> None:
    for row in rows:
        pred = pred_by_key.get((int(row["market_id"]), int(row["sample_ms"])))
        age = base._num(pred.get("latest_age_ms")) if pred else None
        mid = _fresh_mid(pred)
        row["micro_prediction_event_age_ms"] = age
        row["micro_prediction_up_mid"] = mid
        row["micro_prediction_distance_05"] = abs(mid - 0.5) if mid is not None else None
        row["prediction_8778_reconstructable"] = int(bool(pred and pred.get("reconstructable")))
        row["prediction_8778_valid_mid"] = int(bool(pred and pred.get("valid_mid")))
        row["prediction_8778_fresh_2s"] = int(mid is not None)
        row["prediction_8778_latest_update_id"] = pred.get("latest_update_id") if pred else None
        row["prediction_8778_checkpoint_update_id"] = pred.get("checkpoint_update_id") if pred else None


def _prediction_history_features(rows: list[dict[str, Any]]) -> None:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["market_id"])].append(row)

    for market_rows in grouped.values():
        market_rows.sort(key=lambda r: int(r["sample_ms"]))
        for idx, row in enumerate(market_rows):
            sample_ms = int(row["sample_ms"])
            current_mid = base._num(row.get("micro_prediction_up_mid"))
            for history_ms in base.HISTORY_MS:
                suffix = f"{history_ms // 1000}s"
                delta_name = f"micro_prediction_delta_{suffix}"
                range_name = f"micro_prediction_range_{suffix}"
                aligned_name = f"micro_prediction_pressure_aligned_delta_{suffix}"
                row[delta_name] = None
                row[range_name] = None
                row[aligned_name] = None
                if current_mid is None:
                    continue

                lower = sample_ms - int(history_ms)
                points: list[tuple[int, float]] = []
                j = idx
                while j >= 0 and int(market_rows[j]["sample_ms"]) >= lower:
                    r = market_rows[j]
                    t = int(r["sample_ms"])
                    if t <= sample_ms:
                        mid = base._num(r.get("micro_prediction_up_mid"))
                        if mid is not None:
                            points.append((t, mid))
                    j -= 1
                points.sort()
                if len(points) < 2:
                    continue
                coverage = (points[-1][0] - points[0][0]) / float(history_ms)
                if coverage < 0.65:
                    continue

                pred_delta = points[-1][1] - points[0][1]
                pred_range = max(v for _, v in points) - min(v for _, v in points)
                pressure = base._num(row.get(f"micro_book_pressure_mean_{suffix}"))
                aligned = None
                if pressure is not None:
                    aligned = pred_delta * (1 if pressure > 0 else -1 if pressure < 0 else 0)
                row[delta_name] = pred_delta
                row[range_name] = pred_range
                row[aligned_name] = aligned


def build_ab_rows(
    states: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    pred_by_key: Mapping[tuple[int, int], Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], Counter]:
    # V2.7.1 hardened builder + empty micro Prediction events => identical control
    # rows with every Prediction feature nulled before the 8778 treatment overlay.
    rows, audit = base.build_training_rows(states, snapshots, {})
    _overlay_current_prediction(rows, pred_by_key)
    _prediction_history_features(rows)
    return rows, audit


def _metric_delta(treatment: Mapping[str, Any], control: Mapping[str, Any]) -> dict[str, float | None]:
    auc_t = base._num((treatment.get("oof") or {}).get("auc"))
    auc_c = base._num((control.get("oof") or {}).get("auc"))
    ll_t = base._num((treatment.get("oof") or {}).get("logLoss"))
    ll_c = base._num((control.get("oof") or {}).get("logLoss"))
    return {
        "deltaAucTreatmentMinusControl": (
            auc_t - auc_c if auc_t is not None and auc_c is not None else None
        ),
        "deltaLogLossTreatmentMinusControl": (
            ll_t - ll_c if ll_t is not None and ll_c is not None else None
        ),
    }


def _fold_map(model: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    return {
        int(f["holdoutTargetMarketId"]): f
        for f in model.get("folds", [])
        if f.get("status") == "OK"
    }


def _ab_summary(
    models: Mapping[str, Mapping[str, Any]], horizons: Sequence[int]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for horizon in horizons:
        for task in ("ACTION", "ADD", "REPAIR"):
            ctl_key = f"{task}_{horizon}S__CORE_STATE_PUBLIC_NO_PREDICTION"
            trt_key = f"{task}_{horizon}S__CORE_STATE_PUBLIC_8778_PREDICTION"
            control = models[ctl_key]
            treatment = models[trt_key]
            fold_control = _fold_map(control)
            fold_treatment = _fold_map(treatment)
            folds = []
            for market_id in sorted(set(fold_control) & set(fold_treatment)):
                c = fold_control[market_id]
                t = fold_treatment[market_id]
                c_auc = base._num(c.get("auc"))
                t_auc = base._num(t.get("auc"))
                c_ll = base._num(c.get("logLoss"))
                t_ll = base._num(t.get("logLoss"))
                folds.append({
                    "holdoutTargetMarketId": market_id,
                    "controlAuc": c_auc,
                    "treatmentAuc": t_auc,
                    "deltaAucTreatmentMinusControl": (
                        t_auc - c_auc if t_auc is not None and c_auc is not None else None
                    ),
                    "controlLogLoss": c_ll,
                    "treatmentLogLoss": t_ll,
                    "deltaLogLossTreatmentMinusControl": (
                        t_ll - c_ll if t_ll is not None and c_ll is not None else None
                    ),
                })
            out[f"{task}_{horizon}S"] = {
                "control": control.get("oof"),
                "treatment": treatment.get("oof"),
                **_metric_delta(treatment, control),
                "foldDeltas": folds,
            }
    return out


def _prediction_feature_coverage(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    fields = [f for f in base.PUBLIC_FEATURES if _is_prediction_feature(f)]
    return base._coverage(list(rows), fields)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="V2.7.2 controlled EBM A/B: no Prediction vs strict-past 8778 Prediction."
    )
    parser.add_argument("--states", type=Path, default=base.DEFAULT_STATES)
    parser.add_argument("--micro-db", type=Path, default=base.DEFAULT_DB)
    parser.add_argument("--book-db", type=Path, default=DEFAULT_BOOK_DB)
    parser.add_argument("--start", default=base.DEFAULT_START)
    parser.add_argument("--end", default=base.DEFAULT_END)
    parser.add_argument("--horizons", default="3")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    parser.add_argument("--local", type=Path, default=DEFAULT_LOCAL)
    args = parser.parse_args()

    start_ms, end_ms = base._iso_ms(args.start), base._iso_ms(args.end)
    horizons = base._parse_horizons(args.horizons)
    states = base._load_states(args.states, start_ms, end_ms)
    if not states:
        raise RuntimeError("no V2.1 stress states in requested V2.7.2 window")
    if not args.book_db.expanduser().resolve().exists():
        raise RuntimeError(f"8778 Predict book DB not found: {args.book_db}")

    snapshots, _ignored_prediction_events = base._load_micro(args.micro_db, start_ms, end_ms)
    pred_by_key, pred_audit = _load_8778_rows(states, args.book_db)
    decision = pred_audit["decision"]
    if decision.get("status") != "READY_FOR_V272_EBM_AB":
        raise RuntimeError(
            "V2.7.2 Prediction coverage gate failed: "
            + json.dumps(decision, ensure_ascii=False)
        )

    rows, join_audit = build_ab_rows(states, snapshots, pred_by_key)
    if len(rows) < 50:
        raise RuntimeError(f"too few fresh fragment rows after strict-past micro join: {len(rows)}")

    specs = feature_specs()
    models: dict[str, Any] = {}
    local_rows: list[dict[str, Any]] = []
    for horizon in horizons:
        for task in ("ACTION", "ADD", "REPAIR"):
            for spec_name, features in specs.items():
                key = f"{task}_{horizon}S__{spec_name}"
                result, local = base.fit_one(
                    rows,
                    features,
                    task,
                    horizon,
                    spec_name,
                    collect_local=(horizon == 3),
                )
                for item in local:
                    item["feature_spec"] = spec_name
                models[key] = result
                local_rows.extend(local)
                print(
                    key,
                    "n=", result["rows"],
                    "pos=", result["positives"],
                    "OOF AUC=", result["oof"].get("auc"),
                    "logloss=", result["oof"].get("logLoss"),
                )

    market_counts = Counter(int(r["market_id"]) for r in rows)
    report = {
        "version": REPORT_VERSION,
        "policy": {
            "window": {"start": args.start, "end": args.end},
            "validation": "leave-one-Target-market-out; identical folds for control and treatment",
            "modelParameters": "identical V2.7 EBM factory/random_state for both arms",
            "labels": "identical V2.1 fixed-grid ACTION/ADD/REPAIR future labels",
            "primaryHorizonS": 3,
            "requestedHorizonsS": horizons,
            "control": "same state + non-Prediction public microstructure; all Prediction features excluded",
            "treatment": "control + receivedStrict 8778 Prediction current/history features, current mid gated <=2s",
            "predictionClock": "received_at_ms",
            "predictionStrictPast": "received_at_ms < fixed-grid sample_ms; equality excluded",
            "predictionFreshnessGateMs": PREDICTION_FRESHNESS_MS,
            "guardrails": [
                "No Target/Predict market_id to 699xxxx micro market_id equality requirement.",
                "8778 current Prediction mid is used only when strict-past valid and <=2s old.",
                "Prediction 1s/3s/5s deltas/ranges use only fresh strict-past mids from the same Target market.",
                "Control and treatment use the exact same post-micro-join rows, labels, folds, and EBM parameters.",
                "Full-fit importance is descriptive; LOMO OOF deltas are primary evidence.",
                "No Echtgeld/live strategy changes.",
            ],
        },
        "source": {
            "states": str(args.states.expanduser().resolve()),
            "microDb": str(args.micro_db.expanduser().resolve()),
            "bookDb": str(args.book_db.expanduser().resolve()),
            "bookTable": bookmod.BOOK_TABLE,
            "fixedGridRowsInWindow": len(states),
            "microSnapshotsLoaded": len(snapshots),
            "trainingRows": len(rows),
            "microJoinAuditControlBuilder": dict(join_audit),
            "targetMarketRows": {str(k): v for k, v in sorted(market_counts.items())},
            "prediction8778CoverageGate": pred_audit,
        },
        "featureSpecs": specs,
        "featureCoverage": {
            name: base._coverage(rows, fields) for name, fields in specs.items()
        },
        "predictionFeatureCoverage": _prediction_feature_coverage(rows),
        "models": models,
        "ab": _ab_summary(models, horizons),
        "interpretation": {
            "primaryQuestion": "Does fresh 8778 Prediction state add cross-market out-of-fold signal beyond the same risk state and non-Prediction public microstructure?",
            "supportPattern": "Positive DeltaAUC and negative DeltaLogLoss across multiple held-out markets, especially ADD, supports Prediction/repricing as a missing public controller input.",
            "failurePattern": "Lift confined to one holdout market, or better AUC with materially worse logloss, is not enough to promote the hypothesis.",
            "warning": "This fragment is four Target markets from a special regime; even a positive A/B is evidence for feature relevance, not proof of Target's proprietary policy.",
        },
    }

    args.report.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.report.expanduser().resolve().write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    base._write_csv(args.rows, rows)
    base._write_csv(
        args.local,
        sorted(local_rows, key=lambda r: (r["sample_ms"], r["task"], r["feature_spec"])),
    )
    print("report:", args.report)
    print("rows:", args.rows)
    print("local OOF explanations:", args.local, "rows=", len(local_rows))
    print("A/B summary:")
    print(json.dumps(report["ab"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
