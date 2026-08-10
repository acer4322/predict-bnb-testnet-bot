from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from . import live_loss_attribution_report as loss_v1
from . import poly_binance_opening_leader_research as v1


DEFAULT_OUTPUT_PREFIX = loss_v1.ROOT / "data" / "poly_binance_opening_leader_research_v2"
STRICT_MIN_OPENING_EVALUABLE = 5
STRICT_MIN_OPENING_ACCURACY = 0.80


def _simulate_confidence_policy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate confidence mode using the true previous 10 chronological markets.

    V1 accidentally filtered history to laterEvaluable markets before filling the
    10-market deque. That changed the intended rule from "8 of the previous 10
    markets" into "8 of the previous 10 decisive/evaluable markets" and could
    skip intervening TIE/INSUFFICIENT/coverage-gap markets. V2 keeps every market
    in chronological history. Unknown or non-decisive markets therefore consume a
    slot and count against the 8-of-10 requirement instead of disappearing.
    """
    ordered = sorted(
        rows,
        key=lambda row: (int(row.get("marketStartMs") or 0), int(row.get("marketId") or 0)),
    )
    decisions: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []

    for row in ordered:
        prior = history[-v1.CONFIDENCE_LOOKBACK :]
        consensus_leader: str | None = None
        consensus_count = 0
        counts: Counter[str] = Counter()
        if len(prior) == v1.CONFIDENCE_LOOKBACK:
            for item in prior:
                if (
                    item.get("coverageQualified") is True
                    and item.get("laterEvaluable") is True
                    and str(item.get("laterLeader") or "") in v1.DECISIVE
                ):
                    counts[str(item.get("laterLeader"))] += 1
            if counts:
                candidate, candidate_count = counts.most_common(1)[0]
                consensus_count = int(candidate_count)
                if candidate_count >= v1.CONFIDENCE_REQUIRED:
                    consensus_leader = candidate

        reliable_prior = [
            item
            for item in prior
            if item.get("coverageQualified") is True
            and item.get("predictionEvaluable") is True
        ]
        opening_correct = sum(
            item.get("openingPredictsLater") is True for item in reliable_prior
        )
        opening_accuracy = (
            opening_correct / len(reliable_prior) if reliable_prior else None
        )
        opening_reliability = bool(
            len(reliable_prior) >= STRICT_MIN_OPENING_EVALUABLE
            and opening_accuracy is not None
            and opening_accuracy >= STRICT_MIN_OPENING_ACCURACY
        )
        consensus_confidence = consensus_leader in v1.DECISIVE
        strict_confidence = consensus_confidence and opening_reliability

        early = str(row.get("openingLeader") or "")
        actual = str(row.get("laterLeader") or "")
        actual_evaluable = bool(
            row.get("coverageQualified") is True
            and row.get("laterEvaluable") is True
            and actual in v1.DECISIVE
        )

        def policy_decision(use_strict: bool) -> tuple[str | None, str]:
            confidence = strict_confidence if use_strict else consensus_confidence
            if confidence and consensus_leader in v1.DECISIVE:
                return consensus_leader, "CONFIDENCE_SKIP_OPENING"
            if row.get("openingEvaluable") is True and early in v1.DECISIVE:
                return early, "OPENING_WINDOW_REQUIRED"
            return None, "NO_DECISIVE_OPENING_SIGNAL"

        consensus_prediction, consensus_mode = policy_decision(False)
        strict_prediction, strict_mode = policy_decision(True)
        decisions.append(
            {
                "marketId": row.get("marketId"),
                "marketStartMs": row.get("marketStartMs"),
                "actualLaterLeader": actual,
                "actualLaterLeaderEvaluable": actual_evaluable,
                "openingLeader": early,
                "prior10ChronologicalMarkets": len(prior),
                "prior10PolyCount": int(counts.get("POLY", 0)),
                "prior10BinanceCount": int(counts.get("BINANCE", 0)),
                "prior10OtherOrUnusableCount": (
                    len(prior) - int(counts.get("POLY", 0)) - int(counts.get("BINANCE", 0))
                ),
                "prior10LeaderConsensus": consensus_leader,
                "prior10LeaderConsensusCount": consensus_count,
                "prior10OpeningPredictionsEvaluable": len(reliable_prior),
                "prior10OpeningCorrect": opening_correct,
                "prior10OpeningAccuracy": opening_accuracy,
                "consensusConfidenceMode": consensus_confidence,
                "strictConfidenceMode": strict_confidence,
                "consensusPolicyMode": consensus_mode,
                "consensusPolicyPrediction": consensus_prediction,
                "consensusPolicyCorrect": (
                    consensus_prediction == actual
                    if consensus_prediction and actual_evaluable
                    else None
                ),
                "strictPolicyMode": strict_mode,
                "strictPolicyPrediction": strict_prediction,
                "strictPolicyCorrect": (
                    strict_prediction == actual
                    if strict_prediction and actual_evaluable
                    else None
                ),
            }
        )
        history.append(row)

    def summarize(prefix: str) -> dict[str, Any]:
        predicted = [
            item
            for item in decisions
            if item.get(f"{prefix}PolicyPrediction") in v1.DECISIVE
            and item.get("actualLaterLeaderEvaluable") is True
        ]
        correct = [item for item in predicted if item.get(f"{prefix}PolicyCorrect") is True]
        skip_all = [
            item
            for item in decisions
            if item.get(f"{prefix}PolicyMode") == "CONFIDENCE_SKIP_OPENING"
        ]
        skip_scored = [
            item for item in skip_all if item.get("actualLaterLeaderEvaluable") is True
        ]
        skip_correct = [
            item for item in skip_scored if item.get(f"{prefix}PolicyCorrect") is True
        ]
        poly_skip = [
            item
            for item in skip_scored
            if item.get(f"{prefix}PolicyPrediction") == "POLY"
        ]
        return {
            "predictedMarkets": len(predicted),
            "accuracy": len(correct) / len(predicted) if predicted else None,
            "confidenceSkipMarkets": len(skip_all),
            "confidenceSkipScoredMarkets": len(skip_scored),
            "confidenceSkipAccuracy": (
                len(skip_correct) / len(skip_scored) if skip_scored else None
            ),
            "polyConfidenceSkipMarkets": len(poly_skip),
            "polyConfidenceSkipAccuracy": (
                sum(item.get(f"{prefix}PolicyCorrect") is True for item in poly_skip)
                / len(poly_skip)
                if poly_skip
                else None
            ),
            "openingWindowRequiredMarkets": sum(
                item.get(f"{prefix}PolicyMode") == "OPENING_WINDOW_REQUIRED"
                for item in decisions
            ),
            "noSignalMarkets": sum(
                item.get(f"{prefix}PolicyMode") == "NO_DECISIVE_OPENING_SIGNAL"
                for item in decisions
            ),
        }

    return {
        "lookbackMarkets": v1.CONFIDENCE_LOOKBACK,
        "requiredAgreement": v1.CONFIDENCE_REQUIRED,
        "historyDefinition": "true previous 10 chronological markets; TIE/INSUFFICIENT/unusable markets count against agreement",
        "strictMinimumOpeningEvaluable": STRICT_MIN_OPENING_EVALUABLE,
        "strictMinimumOpeningAccuracy": STRICT_MIN_OPENING_ACCURACY,
        "consensusOnlyPolicy": summarize("consensus"),
        "strictReliabilityPlusConsensusPolicy": summarize("strict"),
        "decisions": decisions,
    }


def build_report(
    db_path: Path = v1.DEFAULT_DB,
    *,
    windows: tuple[int, ...] = v1.DEFAULT_WINDOWS,
    thresholds: tuple[float, ...] = v1.DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    report = v1.build_report(db_path, windows=windows, thresholds=thresholds)
    report["version"] = "poly_binance_opening_leader_research_v2"
    report["method"]["confidenceHistoryDefinition"] = (
        "true previous 10 chronological markets; non-decisive or unusable markets are not skipped"
    )
    report["method"]["strictMinimumOpeningEvaluable"] = STRICT_MIN_OPENING_EVALUABLE
    report["method"]["strictMinimumOpeningAccuracy"] = STRICT_MIN_OPENING_ACCURACY
    for payload in (report.get("windows") or {}).values():
        payload["confidencePolicy"] = _simulate_confidence_policy(
            list(payload.get("markets") or [])
        )
    report.setdefault("limitations", []).append(
        "V2 fixes V1 confidence-history selection: confidence is based on the literal previous 10 chronological markets, not the previous 10 decisive markets."
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Causal Poly/Binance opening-leader research with literal 8-of-10 chronological confidence mode."
    )
    parser.add_argument("--db", type=Path, default=v1.DEFAULT_DB)
    parser.add_argument("--windows", default="60,120")
    parser.add_argument("--thresholds", default="0.60,0.70,0.80")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    windows = v1._parse_ints(args.windows)
    thresholds = v1._parse_thresholds(args.thresholds)
    if not windows:
        parser.error("--windows must contain positive seconds")
    if not thresholds:
        parser.error("--thresholds must contain values between 0.51 and 0.95")
    report = build_report(args.db, windows=windows, thresholds=thresholds)
    paths = v1.write_report(report, args.output_prefix)
    headline = {
        key: {
            "accuracy": payload["accuracy"],
            "consensusPolicy": payload["confidencePolicy"]["consensusOnlyPolicy"],
            "strictPolicy": payload["confidencePolicy"]["strictReliabilityPlusConsensusPolicy"],
        }
        for key, payload in report["windows"].items()
    }
    print(json.dumps({"windows": headline, "outputs": paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
