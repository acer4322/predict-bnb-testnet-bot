from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_tail_insurance_report.json"
DEFAULT_CSV = ROOT / "artifacts" / "wallet_profit_strategy" / "target_taker_tail_insurance_episodes.csv"
CHEAP_PRICE_MAX = 0.12
TAIL_SECONDS_MAX = 60.0


def _iso_ms(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1_000)
    except (TypeError, ValueError):
        return None


def _market_end_ms(raw_json: str) -> int | None:
    try:
        raw = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    market = raw.get("market") if isinstance(raw.get("market"), dict) else {}
    return _iso_ms(market.get("boostEndsAt"))


def _side(up: float, down: float) -> str | None:
    return "UP" if up > down else "DOWN" if down > up else None


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _time_bucket(seconds_left: float) -> str:
    if seconds_left <= 5:
        return "0_5S"
    if seconds_left <= 10:
        return "5_10S"
    if seconds_left <= 15:
        return "10_15S"
    if seconds_left <= 30:
        return "15_30S"
    return "30_60S"


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.floor((len(ordered) - 1) * q)))]


def _stats(values: list[float]) -> dict[str, float | int | None]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "mean": statistics.mean(finite) if finite else None,
        "median": statistics.median(finite) if finite else None,
        "p10": _quantile(finite, 0.10),
        "p90": _quantile(finite, 0.90),
        "minimum": min(finite) if finite else None,
        "maximum": max(finite) if finite else None,
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(float(x), float(y)) for x, y in zip(xs, ys) if math.isfinite(float(x)) and math.isfinite(float(y))]
    if len(pairs) < 3:
        return None
    left = [item[0] for item in pairs]
    right = [item[1] for item in pairs]
    if statistics.pstdev(left) <= 1e-12 or statistics.pstdev(right) <= 1e-12:
        return None
    return statistics.correlation(left, right)


def _median_ratio_model(rows: list[dict[str, Any]], numerator: str, denominator: str) -> dict[str, Any]:
    pairs = [
        (float(row[numerator]), float(row[denominator]))
        for row in rows
        if row.get(numerator) is not None and row.get(denominator) is not None and float(row[denominator]) > 0
    ]
    ratios = [left / right for left, right in pairs]
    median_ratio = statistics.median(ratios) if ratios else None
    errors = [abs(left - float(median_ratio) * right) / left for left, right in pairs if left > 0] if median_ratio is not None else []
    return {
        "samples": len(pairs),
        "numerator": numerator,
        "denominator": denominator,
        "pearson": _pearson([right for _, right in pairs], [left for left, _ in pairs]),
        "ratio": _stats(ratios),
        "medianRatioAbsolutePercentageError": statistics.median(errors) if errors else None,
    }


def _parent_orders(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[int, int]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    market_ends: dict[int, int] = {}
    for event in events:
        market_id = int(event["market_id"])
        if market_id not in market_ends:
            end_ms = _market_end_ms(str(event.get("raw_json") or ""))
            if end_ms is not None:
                market_ends[market_id] = end_ms
        grouped[(market_id, event["role"], event.get("order_hash") or event["leg_id"], event["side"], event["quote_type"])].append(event)

    result = []
    for (market_id, role, order_hash, side, quote_type), legs in grouped.items():
        shares = sum(float(leg.get("shares") or 0.0) for leg in legs)
        notional = sum(float(leg.get("shares") or 0.0) * float(leg.get("price") or 0.0) for leg in legs)
        result.append({
            "marketId": market_id,
            "role": str(role),
            "orderHash": str(order_hash),
            "side": str(side),
            "quoteType": str(quote_type),
            "eventMs": min(int(leg["event_ms"]) for leg in legs),
            "shares": shares,
            "notionalUsdt": notional,
            "averagePrice": notional / shares if shares else None,
            "fillLegs": len(legs),
        })
    result.sort(key=lambda row: (int(row["marketId"]), int(row["eventMs"]), str(row["orderHash"])))
    return result, market_ends


def _candidate_rows(orders: list[dict[str, Any]], market_ends: dict[int, int]) -> list[dict[str, Any]]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for order in orders:
        by_market[int(order["marketId"])].append(order)

    candidates: list[dict[str, Any]] = []
    for market_id, rows in by_market.items():
        end_ms = market_ends.get(market_id)
        if end_ms is None:
            continue
        up_shares = down_shares = up_capital = down_capital = 0.0
        by_second: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_second[int(row["eventMs"])].append(row)
        for event_ms in sorted(by_second):
            second_rows = by_second[event_ms]
            before = {
                "upShares": up_shares,
                "downShares": down_shares,
                "upCapitalUsdt": up_capital,
                "downCapitalUsdt": down_capital,
            }
            capital_side = _side(up_capital, down_capital)
            seconds_left = (end_ms - event_ms) / 1_000.0
            for row in second_rows:
                price = float(row.get("averagePrice") or 0.0)
                if (
                    row["role"] == "TAKER"
                    and row["quoteType"] == "BID"
                    and 0 <= seconds_left <= TAIL_SECONDS_MAX
                    and 0 < price <= CHEAP_PRICE_MAX
                    and capital_side is not None
                    and row["side"] != capital_side
                ):
                    main_shares = up_shares if capital_side == "UP" else down_shares
                    main_capital = up_capital if capital_side == "UP" else down_capital
                    total_capital = up_capital + down_capital
                    main_profit_if_wins = main_shares - total_capital
                    candidates.append({
                        **row,
                        "secondsLeft": seconds_left,
                        "mainSideBefore": capital_side,
                        "hedgeSide": row["side"],
                        "mainSharesBefore": main_shares,
                        "mainCapitalBeforeUsdt": main_capital,
                        "totalCapitalBeforeUsdt": total_capital,
                        "netShareExposureBefore": abs(up_shares - down_shares),
                        "expectedMainPayoutUsdt": main_shares,
                        "expectedMainGrossProfitIfWinsUsdt": main_profit_if_wins,
                        "insurancePayoutIfWinsUsdt": float(row["shares"]),
                        "insuranceSharesToMainShares": _ratio(float(row["shares"]), main_shares),
                        "insuranceCostToMainCapital": _ratio(float(row["notionalUsdt"]), main_capital),
                        "insuranceCostToTotalCapital": _ratio(float(row["notionalUsdt"]), total_capital),
                        "strictlyPriorState": before,
                    })
            for row in second_rows:
                sign = 1.0 if row["quoteType"] == "BID" else -1.0
                if row["side"] == "UP":
                    up_shares += sign * float(row["shares"])
                    up_capital += sign * float(row["notionalUsdt"])
                elif row["side"] == "DOWN":
                    down_shares += sign * float(row["shares"])
                    down_capital += sign * float(row["notionalUsdt"])
    return candidates


def _episodes(candidates: list[dict[str, Any]], settled: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[(int(row["marketId"]), str(row["mainSideBefore"]), str(row["hedgeSide"]))].append(row)

    episodes = []
    for (market_id, main_side, hedge_side), rows in grouped.items():
        rows.sort(key=lambda row: (int(row["eventMs"]), str(row["orderHash"])))
        first = rows[0]
        shares = sum(float(row["shares"]) for row in rows)
        cost = sum(float(row["notionalUsdt"]) for row in rows)
        result = settled.get(market_id, {})
        winner = result.get("winner")
        gross_payout = shares if winner == hedge_side else 0.0
        main_shares = float(first["mainSharesBefore"])
        main_capital = float(first["mainCapitalBeforeUsdt"])
        total_capital = float(first["totalCapitalBeforeUsdt"])
        expected_profit = float(first["expectedMainGrossProfitIfWinsUsdt"])
        episodes.append({
            "marketId": market_id,
            "mainSideBefore": main_side,
            "hedgeSide": hedge_side,
            "firstEventMs": int(first["eventMs"]),
            "lastEventMs": max(int(row["eventMs"]) for row in rows),
            "firstSecondsLeft": float(first["secondsLeft"]),
            "lastSecondsLeft": min(float(row["secondsLeft"]) for row in rows),
            "parents": len(rows),
            "observedInsuranceShares": shares,
            "observedInsuranceCostUsdt": cost,
            "averageInsurancePrice": cost / shares if shares else None,
            "observedCostPerParentUsdt": cost / len(rows),
            "oneUsdtPerParentShares": len(rows) / (cost / shares) if cost > 0 and shares > 0 else None,
            "mainSharesBefore": main_shares,
            "mainCapitalBeforeUsdt": main_capital,
            "totalCapitalBeforeUsdt": total_capital,
            "netShareExposureBefore": float(first["netShareExposureBefore"]),
            "expectedMainPayoutUsdt": main_shares,
            "expectedMainGrossProfitIfWinsUsdt": expected_profit,
            "insurancePayoutIfWinsUsdt": shares,
            "insuranceSharesToMainShares": _ratio(shares, main_shares),
            "insuranceCostToMainCapital": _ratio(cost, main_capital),
            "insuranceCostToTotalCapital": _ratio(cost, total_capital),
            "insurancePayoutToExpectedMainProfit": _ratio(shares, expected_profit),
            "winner": winner,
            "insuranceWon": winner == hedge_side if winner in {"UP", "DOWN"} else None,
            "observedInsuranceGrossPnlUsdt": gross_payout - cost if winner in {"UP", "DOWN"} else None,
            "parentOrderHashes": [row["orderHash"] for row in rows],
        })
    return sorted(episodes, key=lambda row: (int(row["firstEventMs"]), int(row["marketId"])))


def build_report(db_path: Path) -> dict[str, Any]:
    db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    events = [dict(row) for row in db.execute(
        "SELECT * FROM wallet_shadow_target_events ORDER BY market_id,event_ms,leg_id"
    )]
    settled = {int(row["market_id"]): dict(row) for row in db.execute(
        "SELECT * FROM wallet_shadow_target_market_results"
    )}
    db.close()

    orders, market_ends = _parent_orders(events)
    candidates = _candidate_rows(orders, market_ends)
    episodes = _episodes(candidates, settled)
    settled_episodes = [row for row in episodes if row["insuranceWon"] is not None]
    parent_costs = [float(row["notionalUsdt"]) for row in candidates]
    parent_shares = [float(row["shares"]) for row in candidates]
    parent_prices = [float(row["averagePrice"]) for row in candidates]
    episode_costs = [float(row["observedInsuranceCostUsdt"]) for row in episodes]
    episode_shares = [float(row["observedInsuranceShares"]) for row in episodes]
    by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        by_time[_time_bucket(float(row["secondsLeft"]))].append(row)

    models = [
        _median_ratio_model(episodes, "observedInsuranceShares", "mainSharesBefore"),
        _median_ratio_model(episodes, "observedInsuranceShares", "netShareExposureBefore"),
        _median_ratio_model(episodes, "observedInsuranceShares", "expectedMainGrossProfitIfWinsUsdt"),
        _median_ratio_model(episodes, "observedInsuranceCostUsdt", "mainCapitalBeforeUsdt"),
        _median_ratio_model(episodes, "observedInsuranceCostUsdt", "totalCapitalBeforeUsdt"),
        _median_ratio_model(episodes, "parents", "mainSharesBefore"),
        _median_ratio_model(episodes, "parents", "mainCapitalBeforeUsdt"),
        _median_ratio_model(episodes, "observedInsuranceCostUsdt", "parents"),
        _median_ratio_model(episodes, "observedInsuranceShares", "oneUsdtPerParentShares"),
    ]
    return {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "scope": {
            "source": "retained target Taker BID parent fills and strictly prior target ledger state",
            "tailSecondsMaximum": TAIL_SECONDS_MAX,
            "cheapAveragePriceMaximum": CHEAP_PRICE_MAX,
            "episodeDefinition": "market plus pre-event capital-main side plus opposite hedge side",
            "sameSecondRule": "all parent fills sharing one second see only ledger state from earlier seconds",
            "minimumOrderCaveat": "observed parent fill notional may be below the venue minimum because a valid >=1 USDT order can partially fill",
            "causality": "descriptive sizing evidence; it cannot reveal the original requested size, unfilled remainder, cancellations or private budget state",
        },
        "summary": {
            "candidateParents": len(candidates),
            "episodes": len(episodes),
            "markets": len({int(row["marketId"]) for row in episodes}),
            "observedInsuranceShares": sum(episode_shares),
            "observedInsuranceCostUsdt": sum(episode_costs),
            "parentObservedNotionalUsdt": _stats(parent_costs),
            "parentObservedShares": _stats(parent_shares),
            "parentObservedAveragePrice": _stats(parent_prices),
            "parentObservedNotionalBuckets": {
                "below0_50": sum(value < 0.50 for value in parent_costs),
                "from0_50To0_90": sum(0.50 <= value < 0.90 for value in parent_costs),
                "from0_90To1_10": sum(0.90 <= value <= 1.10 for value in parent_costs),
                "from1_10To2_10": sum(1.10 < value <= 2.10 for value in parent_costs),
                "above2_10": sum(value > 2.10 for value in parent_costs),
            },
            "episodeParents": _stats([float(row["parents"]) for row in episodes]),
            "episodeObservedCostPerParentUsdt": _stats([
                float(row["observedCostPerParentUsdt"]) for row in episodes
            ]),
            "episodeObservedShares": _stats(episode_shares),
            "episodeObservedCostUsdt": _stats(episode_costs),
            "settledEpisodes": len(settled_episodes),
            "insuranceWinRate": (
                sum(bool(row["insuranceWon"]) for row in settled_episodes) / len(settled_episodes)
                if settled_episodes else None
            ),
            "observedInsuranceGrossPnlUsdt": sum(float(row["observedInsuranceGrossPnlUsdt"]) for row in settled_episodes),
        },
        "bySecondsLeft": {
            bucket: {
                "parents": len(rows),
                "markets": len({int(row["marketId"]) for row in rows}),
                "observedShares": sum(float(row["shares"]) for row in rows),
                "observedCostUsdt": sum(float(row["notionalUsdt"]) for row in rows),
                "shares": _stats([float(row["shares"]) for row in rows]),
                "notionalUsdt": _stats([float(row["notionalUsdt"]) for row in rows]),
                "averagePrice": _stats([float(row["averagePrice"]) for row in rows]),
            }
            for bucket, rows in sorted(by_time.items())
        },
        "allocationModels": models,
        "interpretationGuide": {
            "mostStableRatio": "prefer lower medianRatioAbsolutePercentageError; correlation alone does not establish a sizing rule",
            "sharesVsCapital": "cheap shares can be a large payout exposure while remaining a small capital allocation",
            "forwardUse": "freeze any candidate budget rule before evaluating new forward markets",
        },
        "episodes": list(reversed(episodes)),
        "parents": list(reversed(candidates)),
    }


def _write_csv(path: Path, episodes: list[dict[str, Any]]) -> None:
    fields = [
        "marketId", "mainSideBefore", "hedgeSide", "firstSecondsLeft", "lastSecondsLeft", "parents",
        "observedInsuranceShares", "observedInsuranceCostUsdt", "averageInsurancePrice",
        "observedCostPerParentUsdt", "oneUsdtPerParentShares",
        "mainSharesBefore", "mainCapitalBeforeUsdt", "totalCapitalBeforeUsdt", "netShareExposureBefore",
        "expectedMainPayoutUsdt", "expectedMainGrossProfitIfWinsUsdt", "insurancePayoutIfWinsUsdt",
        "insuranceSharesToMainShares", "insuranceCostToMainCapital", "insuranceCostToTotalCapital",
        "insurancePayoutToExpectedMainProfit", "winner", "insuranceWon", "observedInsuranceGrossPnlUsdt",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(episodes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse target late cheap opposite Taker insurance allocation")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    args = parser.parse_args()
    report = build_report(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(args.csv, report["episodes"])
    print(json.dumps({
        "output": str(args.output),
        "csv": str(args.csv),
        "summary": report["summary"],
        "allocationModels": report["allocationModels"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
