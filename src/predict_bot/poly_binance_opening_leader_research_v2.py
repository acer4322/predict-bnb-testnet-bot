from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import live_loss_attribution_report as loss_v1
from . import poly_binance_opening_leader_research as v1


DEFAULT_OUTPUT_PREFIX = loss_v1.ROOT / "data" / "poly_binance_opening_leader_research_v2"
STRICT_MIN_OPENING_EVALUABLE = 5
STRICT_MIN_OPENING_ACCURACY = 0.80
PAPER_STRATEGY = "R_POLY_GAP_SCALP"
DAY_MS = 86_400_000
RAW_DOWNSAMPLE_MS = 1_000
RAW_FEED_GAP_EXCLUDE_MS = 3_000


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values: list[float], q: float) -> float | None:
    cleaned = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not cleaned:
        return None
    if len(cleaned) == 1:
        return cleaned[0]
    pos = (len(cleaned) - 1) * min(1.0, max(0.0, q))
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return cleaned[lo]
    w = pos - lo
    return cleaned[lo] * (1.0 - w) + cleaned[hi] * w


def _simulate_confidence_policy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate confidence mode using the true previous 10 chronological markets."""
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


def _load_paper_trades(db_path: Path) -> list[dict[str, Any]]:
    db = loss_v1._connect_readonly(db_path)
    try:
        if not loss_v1._table_exists(db, "cross_oracle_strategy_trades"):
            return []
        rows = db.execute(
            """SELECT *
                 FROM cross_oracle_strategy_trades
                WHERE strategy=?
                ORDER BY opened_at_ms, id""",
            (PAPER_STRATEGY,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        db.close()


def _latest_available_ms(
    base_report: dict[str, Any], paper_trades: list[dict[str, Any]]
) -> int | None:
    values: list[int] = []
    for payload in (base_report.get("windows") or {}).values():
        for row in payload.get("markets") or []:
            for key in ("sampleEndMs", "marketStartMs"):
                try:
                    value = int(row.get(key) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    values.append(value)
    for row in paper_trades:
        for key in ("closed_at_ms", "opened_at_ms"):
            try:
                value = int(row.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                values.append(value)
                break
    return max(values) if values else None


def _paper_market_rows(
    paper_trades: list[dict[str, Any]], cutoff_ms: int | None
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in paper_trades:
        opened = int(row.get("opened_at_ms") or 0)
        closed = int(row.get("closed_at_ms") or 0)
        effective = closed or opened
        if cutoff_ms is not None and effective < cutoff_ms:
            continue
        market_id = int(row.get("binance_market_id") or 0)
        if market_id > 0:
            grouped[market_id].append(row)

    result: list[dict[str, Any]] = []
    for market_id, rows in grouped.items():
        rows.sort(key=lambda row: (int(row.get("opened_at_ms") or 0), int(row.get("id") or 0)))
        terminal = [
            row
            for row in rows
            if _finite(row.get("gross_pnl_usdt")) is not None
            and str(row.get("status") or "") != "OPEN"
        ]
        pnl = sum(_finite(row.get("gross_pnl_usdt")) or 0.0 for row in terminal)
        reversal_exits = sum(
            str(row.get("exit_reason") or "") == "POLY_DIRECTION_FLIP" for row in terminal
        )
        sides = [str(row.get("side") or "") for row in rows if str(row.get("side") or "") in {"UP", "DOWN"}]
        side_switches = sum(a != b for a, b in zip(sides, sides[1:]))
        wins = sum((_finite(row.get("gross_pnl_usdt")) or 0.0) > 0 for row in terminal)
        losses = sum((_finite(row.get("gross_pnl_usdt")) or 0.0) < 0 for row in terminal)
        result.append(
            {
                "marketId": market_id,
                "trades": len(rows),
                "closedTrades": len(terminal),
                "wins": wins,
                "losses": losses,
                "marketPnlUsdt": pnl,
                "profitableMarket": pnl > 1e-9,
                "losingMarket": pnl < -1e-9,
                "reversalExits": reversal_exits,
                "sideSwitches": side_switches,
                "officialSettlementExits": sum(
                    str(row.get("exit_reason") or "") == "OFFICIAL_SETTLEMENT"
                    for row in terminal
                ),
                "firstOpenedAtMs": min((int(row.get("opened_at_ms") or 0) for row in rows), default=None),
                "lastClosedAtMs": max((int(row.get("closed_at_ms") or 0) for row in terminal), default=None),
                "polyMarketSlug": str(rows[-1].get("poly_market_slug") or "") if rows else "",
            }
        )
    result.sort(key=lambda row: (int(row.get("firstOpenedAtMs") or 0), int(row["marketId"])))
    return result


def _market_cohort_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "markets": 0,
            "profitableMarkets": 0,
            "losingMarkets": 0,
            "profitableMarketRate": None,
            "totalPnlUsdt": 0.0,
            "averageMarketPnlUsdt": None,
        }
    profitable = sum(bool(row.get("profitableMarket")) for row in rows)
    losing = sum(bool(row.get("losingMarket")) for row in rows)
    pnls = [float(row.get("marketPnlUsdt") or 0.0) for row in rows]
    return {
        "markets": len(rows),
        "profitableMarkets": profitable,
        "losingMarkets": losing,
        "profitableMarketRate": profitable / len(rows),
        "totalPnlUsdt": sum(pnls),
        "averageMarketPnlUsdt": statistics.fmean(pnls),
    }


def _bucket_paper_markets(
    rows: list[dict[str, Any]], key: str, cuts: tuple[int, ...]
) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = int(row.get(key) or 0)
        label = None
        previous = 0
        for cut in cuts:
            if value <= cut:
                label = str(value) if cut == previous else f"{previous}-{cut}"
                break
            previous = cut + 1
        if label is None:
            label = f"{cuts[-1] + 1}+"
        buckets[label].append(row)
    return {label: _market_cohort_summary(items) for label, items in sorted(buckets.items())}


def _numeric_group(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> dict[str, Any]:
    payload: dict[str, Any] = {"markets": len(rows)}
    for key in keys:
        values = [
            float(value)
            for row in rows
            if (value := _finite(row.get(key))) is not None
        ]
        payload[key] = {
            "mean": statistics.fmean(values) if values else None,
            "median": statistics.median(values) if values else None,
            "p90": _percentile(values, 0.90),
        }
    return payload


def _paper_profit_proxy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    profitable = [row for row in rows if row.get("profitableMarket") is True]
    losing = [row for row in rows if row.get("losingMarket") is True]
    keys = ("trades", "reversalExits", "sideSwitches")
    pos = _numeric_group(profitable, keys)
    neg = _numeric_group(losing, keys)

    comparisons: dict[str, bool | None] = {}
    for key in keys:
        pos_med = _finite((pos.get(key) or {}).get("median"))
        neg_med = _finite((neg.get(key) or {}).get("median"))
        comparisons[f"profitableMedianLower_{key}"] = (
            pos_med < neg_med if pos_med is not None and neg_med is not None else None
        )
    known = [value for value in comparisons.values() if value is not None]
    if len(profitable) < 3 or len(losing) < 3:
        verdict = "INSUFFICIENT"
    elif sum(value is True for value in known) >= 2:
        verdict = "SUPPORTS_FEWER_REVERSALS_AND_REENTRIES"
    elif sum(value is True for value in known) == 1:
        verdict = "MIXED"
    else:
        verdict = "DOES_NOT_SUPPORT"

    return {
        "strategy": PAPER_STRATEGY,
        "interpretation": "Paper proxy: reversal exits/reentries describe strategy-observed chop, not full market volatility.",
        "allMarkets": _market_cohort_summary(rows),
        "profitableMarkets": pos,
        "losingMarkets": neg,
        "medianComparisons": comparisons,
        "hypothesisVerdict": verdict,
        "byReversalExits": _bucket_paper_markets(rows, "reversalExits", (0, 1, 2)),
        "byTradesPerMarket": _bucket_paper_markets(rows, "trades", (1, 2, 3)),
        "bySideSwitches": _bucket_paper_markets(rows, "sideSwitches", (0, 1, 2)),
        "markets": rows,
    }


def _downsample_series(
    rows: list[dict[str, Any]], *, time_key: str, value_key: str
) -> list[tuple[int, float]]:
    buckets: dict[int, tuple[int, float]] = {}
    for row in rows:
        try:
            at = int(row.get(time_key) or 0)
        except (TypeError, ValueError):
            continue
        value = _finite(row.get(value_key))
        if at <= 0 or value is None or not 0.0 <= value <= 1.0:
            continue
        bucket = at // RAW_DOWNSAMPLE_MS
        current = buckets.get(bucket)
        if current is None or at > current[0]:
            buckets[bucket] = (at, value)
    return [buckets[key] for key in sorted(buckets)]


def _direction_flips(series: list[tuple[int, float]]) -> int:
    last: str | None = None
    flips = 0
    for _, value in series:
        current = "UP" if value >= 0.55 else "DOWN" if value <= 0.45 else None
        if current is None:
            continue
        if last is not None and current != last:
            flips += 1
        last = current
    return flips


def _path_stats(series: list[tuple[int, float]]) -> dict[str, Any]:
    values = [value for _, value in series]
    if len(values) < 2:
        return {
            "points1s": len(values),
            "totalVariation": None,
            "range": None,
            "efficiencyRatio": None,
            "directionFlips": None,
        }
    total_variation = sum(abs(b - a) for a, b in zip(values, values[1:]))
    net = abs(values[-1] - values[0])
    return {
        "points1s": len(values),
        "totalVariation": total_variation,
        "range": max(values) - min(values),
        "efficiencyRatio": net / total_variation if total_variation > 1e-12 else 0.0,
        "directionFlips": _direction_flips(series),
    }


def _poly_receipt_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stamps = sorted(
        {
            int(row.get("poly_received_at_ms") or 0)
            for row in rows
            if int(row.get("poly_received_at_ms") or 0) > 0
        }
    )
    gaps = [
        b - a
        for a, b in zip(stamps, stamps[1:])
        if 0 < b - a < 30_000
    ]
    return {
        "uniqueReceipts": len(stamps),
        "medianGapMs": statistics.median(gaps) if gaps else None,
        "p95GapMs": _percentile([float(x) for x in gaps], 0.95),
        "maxGapMs": max(gaps) if gaps else None,
        "gapOver2s": sum(gap >= 2_000 for gap in gaps),
        "gapOver3s": sum(gap >= 3_000 for gap in gaps),
    }


def _raw_profit_comparison(
    db_path: Path,
    paper_rows: list[dict[str, Any]],
    cutoff_ms: int | None,
) -> dict[str, Any]:
    grouped = v1._load_all_markets(db_path)
    paper_by_market = {int(row["marketId"]): row for row in paper_rows}
    joined: list[dict[str, Any]] = []

    for market_id, paper in paper_by_market.items():
        rows = grouped.get(market_id)
        if not rows:
            continue
        if cutoff_ms is not None:
            latest = max((int(row.get("observed_at_ms") or 0) for row in rows), default=0)
            if latest < cutoff_ms:
                continue
        poly = _path_stats(
            _downsample_series(rows, time_key="poly_received_at_ms", value_key="poly_up_mid")
        )
        binance = _path_stats(
            _downsample_series(rows, time_key="binance_observed_at_ms", value_key="binance_up_mid")
        )
        quality = _poly_receipt_quality(rows)
        max_gap = _finite(quality.get("maxGapMs"))
        raw_usable = bool(
            poly.get("points1s", 0) >= 40
            and binance.get("points1s", 0) >= 40
            and (max_gap is None or max_gap < RAW_FEED_GAP_EXCLUDE_MS)
        )
        joined.append(
            {
                **paper,
                "rawUsable": raw_usable,
                "polyPoints1s": poly.get("points1s"),
                "polyTotalVariation": poly.get("totalVariation"),
                "polyRange": poly.get("range"),
                "polyEfficiencyRatio": poly.get("efficiencyRatio"),
                "polyDirectionFlips": poly.get("directionFlips"),
                "binancePoints1s": binance.get("points1s"),
                "binanceTotalVariation": binance.get("totalVariation"),
                "binanceRange": binance.get("range"),
                "binanceEfficiencyRatio": binance.get("efficiencyRatio"),
                "binanceDirectionFlips": binance.get("directionFlips"),
                "polyReceiptMedianGapMs": quality.get("medianGapMs"),
                "polyReceiptP95GapMs": quality.get("p95GapMs"),
                "polyReceiptMaxGapMs": quality.get("maxGapMs"),
                "polyReceiptGapOver3s": quality.get("gapOver3s"),
            }
        )

    clean = [row for row in joined if row.get("rawUsable") is True]
    profitable = [row for row in clean if row.get("profitableMarket") is True]
    losing = [row for row in clean if row.get("losingMarket") is True]
    keys = (
        "polyDirectionFlips",
        "polyTotalVariation",
        "polyRange",
        "polyEfficiencyRatio",
        "binanceDirectionFlips",
        "binanceTotalVariation",
    )
    pos = _numeric_group(profitable, keys)
    neg = _numeric_group(losing, keys)

    poly_flip_lower = None
    poly_tv_lower = None
    poly_er_higher = None
    if profitable and losing:
        pos_flip = _finite((pos.get("polyDirectionFlips") or {}).get("median"))
        neg_flip = _finite((neg.get("polyDirectionFlips") or {}).get("median"))
        pos_tv = _finite((pos.get("polyTotalVariation") or {}).get("median"))
        neg_tv = _finite((neg.get("polyTotalVariation") or {}).get("median"))
        pos_er = _finite((pos.get("polyEfficiencyRatio") or {}).get("median"))
        neg_er = _finite((neg.get("polyEfficiencyRatio") or {}).get("median"))
        poly_flip_lower = pos_flip < neg_flip if pos_flip is not None and neg_flip is not None else None
        poly_tv_lower = pos_tv < neg_tv if pos_tv is not None and neg_tv is not None else None
        poly_er_higher = pos_er > neg_er if pos_er is not None and neg_er is not None else None

    if len(profitable) < 3 or len(losing) < 3:
        verdict = "INSUFFICIENT"
    else:
        support = sum(value is True for value in (poly_flip_lower, poly_tv_lower, poly_er_higher))
        verdict = "SUPPORTS_SMOOTHER_MARKETS" if support >= 2 else "MIXED_OR_UNSUPPORTED"

    return {
        "interpretation": "Raw market validation uses 1-second downsampled Poly/Binance probability paths. Markets with a >=3s Poly receipt hole are excluded from the clean comparison.",
        "joinedMarkets": len(joined),
        "cleanRawMarkets": len(clean),
        "profitableCleanMarkets": len(profitable),
        "losingCleanMarkets": len(losing),
        "profitableMarkets": pos,
        "losingMarkets": neg,
        "comparisons": {
            "profitableMedianLower_polyDirectionFlips": poly_flip_lower,
            "profitableMedianLower_polyTotalVariation": poly_tv_lower,
            "profitableMedianHigher_polyEfficiencyRatio": poly_er_higher,
        },
        "hypothesisVerdict": verdict,
        "markets": joined,
    }


def _apply_days_and_recompute(
    report: dict[str, Any], cutoff_ms: int | None
) -> None:
    unique_markets: set[int] = set()
    for payload in (report.get("windows") or {}).values():
        rows = list(payload.get("markets") or [])
        if cutoff_ms is not None:
            rows = [
                row
                for row in rows
                if int(row.get("sampleEndMs") or row.get("marketStartMs") or 0) >= cutoff_ms
            ]
        payload["markets"] = rows
        payload["accuracy"] = v1._accuracy(rows)
        payload["confidencePolicy"] = _simulate_confidence_policy(rows)
        unique_markets.update(int(row.get("marketId") or 0) for row in rows if int(row.get("marketId") or 0) > 0)
    report["marketsLoaded"] = len(unique_markets)


def _attach_paper_context(
    report: dict[str, Any], paper_rows: list[dict[str, Any]]
) -> None:
    by_market = {int(row["marketId"]): row for row in paper_rows}
    for payload in (report.get("windows") or {}).values():
        for row in payload.get("markets") or []:
            paper = by_market.get(int(row.get("marketId") or 0))
            if not paper:
                continue
            row["paperMarketPnlUsdt"] = paper.get("marketPnlUsdt")
            row["paperTrades"] = paper.get("trades")
            row["paperReversalExits"] = paper.get("reversalExits")
            row["paperSideSwitches"] = paper.get("sideSwitches")


def build_report(
    db_path: Path = v1.DEFAULT_DB,
    *,
    windows: tuple[int, ...] = v1.DEFAULT_WINDOWS,
    thresholds: tuple[float, ...] = v1.DEFAULT_THRESHOLDS,
    days: float | None = None,
) -> dict[str, Any]:
    report = v1.build_report(db_path, windows=windows, thresholds=thresholds)
    paper_trades = _load_paper_trades(db_path)
    latest_ms = _latest_available_ms(report, paper_trades)
    cutoff_ms = (
        int(latest_ms - float(days) * DAY_MS)
        if days is not None and days > 0 and latest_ms is not None
        else None
    )
    _apply_days_and_recompute(report, cutoff_ms)
    paper_rows = _paper_market_rows(paper_trades, cutoff_ms)
    _attach_paper_context(report, paper_rows)

    report["version"] = "poly_binance_opening_leader_research_v2"
    report["analysisWindowDays"] = days
    report["analysisLatestAvailableMs"] = latest_ms
    report["analysisCutoffMs"] = cutoff_ms
    report["method"]["confidenceHistoryDefinition"] = (
        "true previous 10 chronological markets; non-decisive or unusable markets are not skipped"
    )
    report["method"]["strictMinimumOpeningEvaluable"] = STRICT_MIN_OPENING_EVALUABLE
    report["method"]["strictMinimumOpeningAccuracy"] = STRICT_MIN_OPENING_ACCURACY
    report["method"]["paperProfitProxyStrategy"] = PAPER_STRATEGY
    report["method"]["rawPathDownsampleMs"] = RAW_DOWNSAMPLE_MS
    report["paperRegimeProfitProxy"] = _paper_profit_proxy(paper_rows)
    report["rawPathProfitComparison"] = _raw_profit_comparison(
        db_path, paper_rows, cutoff_ms
    )
    report.setdefault("limitations", []).extend(
        [
            "V2 confidence is based on the literal previous 10 chronological markets, not the previous 10 decisive markets.",
            "Paper reversal/reentry metrics are a proxy for chop seen by R_POLY_GAP_SCALP; they are not a full-market volatility measurement.",
            "Raw three-day analysis is forward-only: samples pruned before the retention increase cannot be reconstructed from the research script.",
            "Raw profit comparison excludes markets whose Poly receipt stream contains a >=3 second gap because such holes can distort direction flips and total variation.",
        ]
    )
    return report


def _write_extra_csvs(
    report: dict[str, Any], output_prefix: Path, paths: dict[str, str]
) -> None:
    paper_path = output_prefix.parent / f"{output_prefix.name}_paper_markets.csv"
    raw_path = output_prefix.parent / f"{output_prefix.name}_raw_profit_markets.csv"
    loss_v1._write_csv(
        paper_path,
        list((report.get("paperRegimeProfitProxy") or {}).get("markets") or []),
    )
    loss_v1._write_csv(
        raw_path,
        list((report.get("rawPathProfitComparison") or {}).get("markets") or []),
    )
    paths["paperMarketsCsv"] = str(paper_path)
    paths["rawProfitMarketsCsv"] = str(raw_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Causal Poly/Binance opening-leader research plus Paper/raw chop-vs-profit diagnostics."
    )
    parser.add_argument("--db", type=Path, default=v1.DEFAULT_DB)
    parser.add_argument("--windows", default="60,120")
    parser.add_argument("--thresholds", default="0.60,0.70,0.80")
    parser.add_argument(
        "--days",
        type=float,
        default=None,
        help="Only analyze the latest N days available in the DB, e.g. --days 3.",
    )
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    windows = v1._parse_ints(args.windows)
    thresholds = v1._parse_thresholds(args.thresholds)
    if not windows:
        parser.error("--windows must contain positive seconds")
    if not thresholds:
        parser.error("--thresholds must contain values between 0.51 and 0.95")
    if args.days is not None and (not math.isfinite(args.days) or args.days <= 0):
        parser.error("--days must be a positive number")

    report = build_report(
        args.db,
        windows=windows,
        thresholds=thresholds,
        days=args.days,
    )
    paths = v1.write_report(report, args.output_prefix)
    _write_extra_csvs(report, args.output_prefix, paths)
    headline = {
        key: {
            "accuracy": payload["accuracy"],
            "consensusPolicy": payload["confidencePolicy"]["consensusOnlyPolicy"],
            "strictPolicy": payload["confidencePolicy"]["strictReliabilityPlusConsensusPolicy"],
        }
        for key, payload in report["windows"].items()
    }
    print(
        json.dumps(
            {
                "days": args.days,
                "windows": headline,
                "paperHypothesis": (report.get("paperRegimeProfitProxy") or {}).get("hypothesisVerdict"),
                "rawHypothesis": (report.get("rawPathProfitComparison") or {}).get("hypothesisVerdict"),
                "outputs": paths,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
