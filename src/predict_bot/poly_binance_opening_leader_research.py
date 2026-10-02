from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from . import live_loss_attribution_report as loss_v1


DEFAULT_DB = loss_v1.DEFAULT_CROSS_DB
DEFAULT_OUTPUT_PREFIX = loss_v1.ROOT / "data" / "poly_binance_opening_leader_research"
DEFAULT_WINDOWS = (60, 120)
DEFAULT_THRESHOLDS = (0.60, 0.70, 0.80)
FULL_MARKET_MIN_COVERAGE_MS = 240_000
FULL_MARKET_MIN_SAMPLES = 240
OPENING_START_GRACE_MS = 15_000
MIN_OPENING_SAMPLES = 40
MIN_LATER_SAMPLES = 60
CONFIDENCE_LOOKBACK = 10
CONFIDENCE_REQUIRED = 8
DECISIVE = {"POLY", "BINANCE"}


def _market_start_ms(rows: list[dict[str, Any]]) -> tuple[int | None, str]:
    if not rows:
        return None, "UNAVAILABLE"
    slug = str(rows[0].get("poly_market_slug") or "")
    matches = re.findall(r"(?<!\d)(\d{10}|\d{13})(?!\d)", slug)
    if matches:
        raw = matches[-1]
        value = int(raw)
        start_ms = value if len(raw) == 13 else value * 1000
        observed = int(rows[0].get("observed_at_ms") or 0)
        if observed <= 0 or abs(observed - start_ms) <= 10 * 60_000:
            return start_ms, "POLY_SLUG_TIMESTAMP"
    observed = int(rows[0].get("observed_at_ms") or 0)
    if observed <= 0:
        return None, "UNAVAILABLE"
    return (observed // 300_000) * 300_000, "FIVE_MINUTE_BOUNDARY_INFERRED"


def _lead_context(
    rows: list[dict[str, Any]], thresholds: tuple[float, ...]
) -> dict[str, Any]:
    poly_series = loss_v1._dedupe_series(
        rows, time_key="poly_received_at_ms", value_key="poly_up_mid"
    )
    binance_series = loss_v1._dedupe_series(
        rows, time_key="binance_observed_at_ms", value_key="binance_up_mid"
    )
    events: list[dict[str, Any]] = []
    for side in ("UP", "DOWN"):
        for threshold in thresholds:
            pairs = loss_v1._pair_crossings(
                loss_v1._crossings(poly_series, side=side, threshold=threshold),
                loss_v1._crossings(binance_series, side=side, threshold=threshold),
            )
            for event in pairs:
                events.append({"side": side, "milestone": threshold, **event})
    signed = [int(event["signedPolyLeadMs"]) for event in events]
    if not signed:
        leader = "INSUFFICIENT"
        median = None
    else:
        median = statistics.median(signed)
        if median > loss_v1.LEAD_TIE_MS:
            leader = "POLY"
        elif median < -loss_v1.LEAD_TIE_MS:
            leader = "BINANCE"
        else:
            leader = "TIE_MIXED"
    return {
        "samples": len(rows),
        "events": len(events),
        "leader": leader,
        "polyFirstEvents": sum(event["leader"] == "POLY" for event in events),
        "binanceFirstEvents": sum(event["leader"] == "BINANCE" for event in events),
        "tieEvents": sum(event["leader"] == "TIE" for event in events),
        "medianSignedPolyLeadMs": median,
        "meanSignedPolyLeadMs": statistics.fmean(signed) if signed else None,
    }


def _load_all_markets(db_path: Path) -> dict[int, list[dict[str, Any]]]:
    db = loss_v1._connect_readonly(db_path)
    try:
        if not loss_v1._table_exists(db, "poly_binance_lead_samples"):
            return {}
        rows = db.execute(
            """SELECT * FROM poly_binance_lead_samples
                 ORDER BY binance_market_id, observed_at_ms"""
        ).fetchall()
    finally:
        db.close()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["binance_market_id"])].append(dict(row))
    return dict(grouped)


def _evaluate_market(
    market_id: int,
    rows: list[dict[str, Any]],
    *,
    opening_seconds: int,
    thresholds: tuple[float, ...],
) -> dict[str, Any]:
    start_ms, start_source = _market_start_ms(rows)
    sample_start = min((int(row.get("observed_at_ms") or 0) for row in rows), default=0)
    sample_end = max((int(row.get("observed_at_ms") or 0) for row in rows), default=0)
    coverage_ms = max(0, sample_end - sample_start)
    slug = str(rows[-1].get("poly_market_slug") or "") if rows else ""
    full_quality = bool(
        start_ms is not None
        and coverage_ms >= FULL_MARKET_MIN_COVERAGE_MS
        and len(rows) >= FULL_MARKET_MIN_SAMPLES
        and sample_start <= start_ms + OPENING_START_GRACE_MS
    )
    cutoff = (start_ms or sample_start) + opening_seconds * 1000
    opening_rows = [
        row for row in rows
        if int(row.get("observed_at_ms") or 0) <= cutoff
    ]
    later_rows = [
        row for row in rows
        if int(row.get("observed_at_ms") or 0) > cutoff
    ]
    opening = _lead_context(opening_rows, thresholds)
    later = _lead_context(later_rows, thresholds)
    full = _lead_context(rows, thresholds)
    opening_evaluable = bool(
        full_quality
        and len(opening_rows) >= MIN_OPENING_SAMPLES
        and opening["events"] > 0
        and opening["leader"] in DECISIVE
    )
    later_evaluable = bool(
        full_quality
        and len(later_rows) >= MIN_LATER_SAMPLES
        and later["events"] > 0
        and later["leader"] in DECISIVE
    )
    prediction_evaluable = opening_evaluable and later_evaluable
    exact_match = (
        opening["leader"] == later["leader"] if prediction_evaluable else None
    )
    return {
        "marketId": market_id,
        "polyMarketSlug": slug,
        "marketStartMs": start_ms,
        "marketStartSource": start_source,
        "sampleStartMs": sample_start or None,
        "sampleEndMs": sample_end or None,
        "coverageMs": coverage_ms,
        "samples": len(rows),
        "coverageQualified": full_quality,
        "openingSeconds": opening_seconds,
        "openingSamples": len(opening_rows),
        "laterSamples": len(later_rows),
        "openingLeader": opening["leader"],
        "openingEvents": opening["events"],
        "openingMedianSignedPolyLeadMs": opening["medianSignedPolyLeadMs"],
        "laterLeader": later["leader"],
        "laterEvents": later["events"],
        "laterMedianSignedPolyLeadMs": later["medianSignedPolyLeadMs"],
        "fullLeader": full["leader"],
        "fullMedianSignedPolyLeadMs": full["medianSignedPolyLeadMs"],
        "openingEvaluable": opening_evaluable,
        "laterEvaluable": later_evaluable,
        "predictionEvaluable": prediction_evaluable,
        "openingPredictsLater": exact_match,
    }


def _accuracy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = [row for row in rows if row.get("predictionEvaluable") is True]
    correct = [row for row in evaluable if row.get("openingPredictsLater") is True]
    opening_poly = [row for row in evaluable if row.get("openingLeader") == "POLY"]
    opening_binance = [row for row in evaluable if row.get("openingLeader") == "BINANCE"]
    poly_correct = [row for row in opening_poly if row.get("laterLeader") == "POLY"]
    binance_correct = [row for row in opening_binance if row.get("laterLeader") == "BINANCE"]
    confusion = Counter(
        f"{row.get('openingLeader')}->{row.get('laterLeader')}" for row in evaluable
    )
    return {
        "markets": len(rows),
        "coverageQualifiedMarkets": sum(bool(row.get("coverageQualified")) for row in rows),
        "predictionEvaluableMarkets": len(evaluable),
        "predictionCoverage": len(evaluable) / len(rows) if rows else None,
        "correct": len(correct),
        "accuracy": len(correct) / len(evaluable) if evaluable else None,
        "earlyPolyPredictions": len(opening_poly),
        "earlyPolyPrecisionForLaterPoly": (
            len(poly_correct) / len(opening_poly) if opening_poly else None
        ),
        "earlyBinancePredictions": len(opening_binance),
        "earlyBinancePrecisionForLaterBinance": (
            len(binance_correct) / len(opening_binance) if opening_binance else None
        ),
        "confusion": dict(confusion),
    }


def _simulate_confidence_policy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(
        [row for row in rows if row.get("laterEvaluable") is True],
        key=lambda row: (int(row.get("marketStartMs") or 0), int(row.get("marketId") or 0)),
    )
    history: deque[dict[str, Any]] = deque(maxlen=CONFIDENCE_LOOKBACK)
    decisions: list[dict[str, Any]] = []
    for row in ordered:
        prior = list(history)
        consensus_leader = None
        consensus_count = 0
        if len(prior) == CONFIDENCE_LOOKBACK:
            counts = Counter(
                str(item.get("laterLeader"))
                for item in prior
                if item.get("laterLeader") in DECISIVE
            )
            if counts:
                consensus_leader, consensus_count = counts.most_common(1)[0]
                if consensus_count < CONFIDENCE_REQUIRED:
                    consensus_leader = None
        reliable_prior = [
            item for item in prior if item.get("predictionEvaluable") is True
        ]
        opening_correct = sum(
            item.get("openingPredictsLater") is True for item in reliable_prior
        )
        opening_reliability = (
            len(reliable_prior) == CONFIDENCE_LOOKBACK
            and opening_correct >= CONFIDENCE_REQUIRED
        )
        consensus_confidence = consensus_leader in DECISIVE
        strict_confidence = consensus_confidence and opening_reliability

        early = str(row.get("openingLeader") or "")
        actual = str(row.get("laterLeader") or "")

        def policy_decision(use_strict: bool) -> tuple[str | None, str]:
            confidence = strict_confidence if use_strict else consensus_confidence
            if confidence and consensus_leader in DECISIVE:
                return consensus_leader, "CONFIDENCE_SKIP_OPENING"
            if row.get("openingEvaluable") is True and early in DECISIVE:
                return early, "OPENING_WINDOW_REQUIRED"
            return None, "NO_DECISIVE_OPENING_SIGNAL"

        consensus_prediction, consensus_mode = policy_decision(False)
        strict_prediction, strict_mode = policy_decision(True)
        decisions.append(
            {
                "marketId": row["marketId"],
                "marketStartMs": row.get("marketStartMs"),
                "actualLaterLeader": actual,
                "openingLeader": early,
                "prior10LeaderConsensus": consensus_leader,
                "prior10LeaderConsensusCount": consensus_count,
                "prior10OpeningPredictionsEvaluable": len(reliable_prior),
                "prior10OpeningCorrect": opening_correct,
                "prior10OpeningAccuracy": (
                    opening_correct / len(reliable_prior) if reliable_prior else None
                ),
                "consensusConfidenceMode": consensus_confidence,
                "strictConfidenceMode": strict_confidence,
                "consensusPolicyMode": consensus_mode,
                "consensusPolicyPrediction": consensus_prediction,
                "consensusPolicyCorrect": (
                    consensus_prediction == actual if consensus_prediction else None
                ),
                "strictPolicyMode": strict_mode,
                "strictPolicyPrediction": strict_prediction,
                "strictPolicyCorrect": (
                    strict_prediction == actual if strict_prediction else None
                ),
            }
        )
        history.append(row)

    def summarize(prefix: str) -> dict[str, Any]:
        predicted = [
            item for item in decisions if item.get(f"{prefix}PolicyPrediction") in DECISIVE
        ]
        correct = [item for item in predicted if item.get(f"{prefix}PolicyCorrect") is True]
        skip = [
            item for item in decisions
            if item.get(f"{prefix}PolicyMode") == "CONFIDENCE_SKIP_OPENING"
        ]
        skip_correct = [item for item in skip if item.get(f"{prefix}PolicyCorrect") is True]
        poly_skip = [
            item for item in skip if item.get(f"{prefix}PolicyPrediction") == "POLY"
        ]
        return {
            "predictedMarkets": len(predicted),
            "accuracy": len(correct) / len(predicted) if predicted else None,
            "confidenceSkipMarkets": len(skip),
            "confidenceSkipAccuracy": len(skip_correct) / len(skip) if skip else None,
            "polyConfidenceSkipMarkets": len(poly_skip),
            "polyConfidenceSkipAccuracy": (
                sum(item.get(f"{prefix}PolicyCorrect") is True for item in poly_skip) / len(poly_skip)
                if poly_skip else None
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
        "lookbackMarkets": CONFIDENCE_LOOKBACK,
        "requiredAgreement": CONFIDENCE_REQUIRED,
        "consensusOnlyPolicy": summarize("consensus"),
        "strictReliabilityPlusConsensusPolicy": summarize("strict"),
        "decisions": decisions,
    }


def build_report(
    db_path: Path = DEFAULT_DB,
    *,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
    thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict[str, Any]:
    if not db_path.exists():
        raise FileNotFoundError(f"cross-oracle DB not found: {db_path}")
    grouped = _load_all_markets(db_path)
    window_reports: dict[str, Any] = {}
    for seconds in windows:
        evaluated = [
            _evaluate_market(
                market_id,
                rows,
                opening_seconds=seconds,
                thresholds=thresholds,
            )
            for market_id, rows in grouped.items()
        ]
        evaluated.sort(
            key=lambda row: (int(row.get("marketStartMs") or 0), int(row.get("marketId") or 0))
        )
        window_reports[f"{seconds}s"] = {
            "openingSeconds": seconds,
            "accuracy": _accuracy(evaluated),
            "confidencePolicy": _simulate_confidence_policy(evaluated),
            "markets": evaluated,
        }
    return {
        "version": "poly_binance_opening_leader_research_v1",
        "sourceDb": str(db_path),
        "marketsLoaded": len(grouped),
        "milestones": list(thresholds),
        "method": {
            "target": "leader during the remainder of the same 5-minute market after the opening window",
            "openingWindowsSeconds": list(windows),
            "marketStart": "Poly slug timestamp when available, otherwise inferred 5-minute boundary",
            "coverageGateMs": FULL_MARKET_MIN_COVERAGE_MS,
            "minimumFullSamples": FULL_MARKET_MIN_SAMPLES,
            "openingStartGraceMs": OPENING_START_GRACE_MS,
            "confidenceLookbackMarkets": CONFIDENCE_LOOKBACK,
            "confidenceRequiredAgreement": CONFIDENCE_REQUIRED,
            "noLookahead": True,
        },
        "windows": window_reports,
        "limitations": [
            "Leader is based on same-probability milestone crossing order, not causal proof that one venue drives the other.",
            "Opening prediction is scored only against samples after the opening window to avoid using the opening period in the target.",
            "Confidence-mode simulation uses only prior completed markets; the current market is never used to arm its own confidence mode.",
            "A confidence skip that predicts BINANCE should be interpreted as an early block for a Poly-leading entry strategy, not permission to invert the trade.",
        ],
    }


def write_report(report: dict[str, Any], output_prefix: Path) -> dict[str, str]:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    paths = {"json": str(json_path)}
    for key, payload in (report.get("windows") or {}).items():
        seconds = int(str(key).rstrip("s"))
        markets_path = output_prefix.parent / f"{output_prefix.name}_{seconds}s_markets.csv"
        decisions_path = output_prefix.parent / f"{output_prefix.name}_{seconds}s_confidence_decisions.csv"
        loss_v1._write_csv(markets_path, list(payload.get("markets") or []))
        decisions = ((payload.get("confidencePolicy") or {}).get("decisions") or [])
        loss_v1._write_csv(decisions_path, list(decisions))
        paths[f"{seconds}sMarketsCsv"] = str(markets_path)
        paths[f"{seconds}sConfidenceCsv"] = str(decisions_path)
    return paths


def _parse_ints(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    for token in str(raw).split(","):
        try:
            value = int(token.strip())
        except ValueError:
            continue
        if value > 0:
            values.append(value)
    return tuple(sorted(set(values)))


def _parse_thresholds(raw: str) -> tuple[float, ...]:
    values: list[float] = []
    for token in str(raw).split(","):
        try:
            value = float(token.strip())
        except ValueError:
            continue
        if math.isfinite(value) and 0.51 <= value <= 0.95:
            values.append(value)
    return tuple(sorted(set(values)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test whether the first 60/120 seconds predict the later Poly-vs-Binance leader and simulate 8-of-10 confidence mode."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--windows", default="60,120")
    parser.add_argument("--thresholds", default="0.60,0.70,0.80")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    windows = _parse_ints(args.windows)
    thresholds = _parse_thresholds(args.thresholds)
    if not windows:
        parser.error("--windows must contain positive seconds")
    if not thresholds:
        parser.error("--thresholds must contain values between 0.51 and 0.95")
    report = build_report(args.db, windows=windows, thresholds=thresholds)
    paths = write_report(report, args.output_prefix)
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
