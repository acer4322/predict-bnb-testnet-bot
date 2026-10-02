from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any

import analyze_target_taker_trigger_conditioned_selective_replay_v1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OOF = ROOT / "data" / "research" / "target_taker_trigger_conditioned_action_v1_oof.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_trigger_conditioned_selective_stability_v1_report.json"
REPORT_VERSION = "TARGET_TAKER_TRIGGER_CONDITIONED_SELECTIVE_STABILITY_V1"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _selected_direction_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        side: base._metrics([
            row for row in rows
            if bool(row.get("selected")) and str(row.get("predicted_side") or "") == side
        ])
        for side in ("UP", "DOWN")
    }


def _fold_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    folds = sorted({int(row["fold"]) for row in rows})
    return {
        str(fold): {
            "metrics": base._metrics([row for row in rows if int(row["fold"]) == fold]),
            "byPredictedSide": _selected_direction_metrics([
                row for row in rows if int(row["fold"]) == fold
            ]),
        }
        for fold in folds
    }


def _stability_summary(by_fold: dict[str, Any]) -> dict[str, Any]:
    e2e: list[float] = []
    side_acc: list[float] = []
    selected: list[int] = []
    up_e2e: list[float] = []
    down_e2e: list[float] = []
    up_better = 0
    comparable = 0

    for payload in by_fold.values():
        metrics = payload["metrics"]
        if metrics.get("selectedActions", 0):
            selected.append(int(metrics["selectedActions"]))
        if metrics.get("endToEndCorrectSidePerSelectedAction") is not None:
            e2e.append(float(metrics["endToEndCorrectSidePerSelectedAction"]))
        if metrics.get("sideAccuracyGivenCleanSubsequent") is not None:
            side_acc.append(float(metrics["sideAccuracyGivenCleanSubsequent"]))

        up = payload["byPredictedSide"]["UP"]
        down = payload["byPredictedSide"]["DOWN"]
        u = up.get("endToEndCorrectSidePerSelectedAction")
        d = down.get("endToEndCorrectSidePerSelectedAction")
        if u is not None:
            up_e2e.append(float(u))
        if d is not None:
            down_e2e.append(float(d))
        if u is not None and d is not None:
            comparable += 1
            if float(u) > float(d):
                up_better += 1

    def stats(values: list[float]) -> dict[str, Any]:
        if not values:
            return {"folds": 0}
        return {
            "folds": len(values),
            "min": min(values),
            "median": median(values),
            "max": max(values),
            "mean": sum(values) / len(values),
        }

    return {
        "endToEndCorrectSidePerSelectedAction": stats(e2e),
        "sideAccuracyGivenCleanSubsequent": stats(side_acc),
        "selectedActionsPerFold": {
            "folds": len(selected),
            "min": min(selected) if selected else None,
            "median": median(selected) if selected else None,
            "max": max(selected) if selected else None,
            "total": sum(selected),
        },
        "predictedUpEndToEnd": stats(up_e2e),
        "predictedDownEndToEnd": stats(down_e2e),
        "upBeatsDown": {
            "comparableFolds": comparable,
            "upBetterFolds": up_better,
            "rate": up_better / comparable if comparable else None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit fold stability and UP/DOWN asymmetry for the causal selective trigger replay. "
            "No models are fit and Target labels never participate in selection."
        )
    )
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--window-rows", type=int, default=300)
    parser.add_argument("--min-history-rows", type=int, default=40)
    args = parser.parse_args()

    rows = base._prepare(args.oof)
    if not rows:
        raise SystemExit(
            f"no trigger-conditioned OOF rows: {args.oof.expanduser().resolve()}\n"
            "Run .\\run-target-taker-trigger-conditioned-action-v1.ps1 first."
        )

    window_rows = max(80, int(args.window_rows))
    min_history_rows = max(20, min(int(args.min_history_rows), window_rows))

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "source": str(args.oof.expanduser().resolve()),
        "purpose": (
            "Check whether aggregate selective-policy gains are stable across chronological OOF folds and whether "
            "the observed predicted-UP versus predicted-DOWN imitation gap is persistent rather than regime-local."
        ),
        "selectionBoundary": (
            "Exactly the same causal fold/phase rolling raw-rank thresholds as selective replay V1; current and future "
            "Target labels never affect selection."
        ),
        "config": {
            "windowRowsPerFoldPhase": window_rows,
            "minHistoryRowsPerFoldPhase": min_history_rows,
            "candidates": list(base.CANDIDATES),
            "sideTailFractions": list(base.SIDE_FRACTIONS),
            "mixedVetoes": [name for name, _ in base.MIXED_VETOES],
        },
        "candidates": {},
    }

    for candidate in base.CANDIDATES:
        source_rows = [row for row in rows if row["candidate"] == candidate]
        candidate_payload: dict[str, Any] = {"sourceTriggers": len(source_rows), "policies": {}}
        report["candidates"][candidate] = candidate_payload
        print(f"\n{candidate}: sourceTriggers={len(source_rows):,}", flush=True)

        for side_fraction in base.SIDE_FRACTIONS:
            for veto_name, veto_fraction in base.MIXED_VETOES:
                marked, coverage = base._causal_marks(
                    source_rows,
                    side_fraction=float(side_fraction),
                    mixed_veto_fraction=veto_fraction,
                    window_rows=window_rows,
                    min_history_rows=min_history_rows,
                )
                policy_name = f"SIDE_TAIL_{int(side_fraction * 100):02d}__{veto_name}"
                by_fold = _fold_payload(marked)
                overall = base._metrics(marked)
                candidate_payload["policies"][policy_name] = {
                    "coverage": coverage,
                    "overall": overall,
                    "byPredictedSide": _selected_direction_metrics(marked),
                    "byFold": by_fold,
                    "stability": _stability_summary(by_fold),
                }
                stability = candidate_payload["policies"][policy_name]["stability"]
                print(
                    f"  {policy_name} overallE2E={overall.get('endToEndCorrectSidePerSelectedAction')} "
                    f"foldMedian={stability['endToEndCorrectSidePerSelectedAction'].get('median')} "
                    f"UP>DOWN={stability['upBeatsDown']['upBetterFolds']}/{stability['upBeatsDown']['comparableFolds']}",
                    flush=True,
                )

    _write_json(args.report, report)
    print(f"\nReport: {args.report.expanduser().resolve()}", flush=True)
    print("No strategy was promoted.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
