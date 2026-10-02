from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TIERS = (1.0, 2.0, 5.0, 10.0, 50.0)
FIVE_MIN_TITLE = re.compile(
    r"^(Ethereum|BNB) Up or Down - .+,\s*(\d{1,2})(?::(\d{2}))?(AM|PM)-(\d{1,2})(?::(\d{2}))?(AM|PM) ET$"
)


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def minute_of_day(hour: int, minute: int, ampm: str) -> int:
    hour %= 12
    if ampm.upper() == "PM":
        hour += 12
    return hour * 60 + minute


def is_eth_bnb_5m(title: Any) -> bool:
    text = str(title or "").strip()
    match = FIVE_MIN_TITLE.match(text)
    if not match:
        return False
    sh, sm, sap, eh, em, eap = (
        int(match.group(2)),
        int(match.group(3) or 0),
        match.group(4),
        int(match.group(5)),
        int(match.group(6) or 0),
        match.group(7),
    )
    start = minute_of_day(sh, sm, sap)
    end = minute_of_day(eh, em, eap)
    if end < start:
        end += 24 * 60
    return end - start == 5


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    rows = sorted(values)
    if len(rows) == 1:
        return rows[0]
    x = max(0.0, min(1.0, q)) * (len(rows) - 1)
    lo = int(x)
    hi = min(len(rows) - 1, lo + 1)
    w = x - lo
    return rows[lo] * (1.0 - w) + rows[hi] * w


def nearest_tier(value: float) -> tuple[float, float]:
    tier = min(TIERS, key=lambda t: abs(value - t))
    return tier, abs(value - tier) / tier


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze target makerHash sequences for genuine ETH/BNB 5m parent-order replenishment. Read-only."
    )
    parser.add_argument("--input", default="data/target_maker_hash_profile.json")
    parser.add_argument("--output", default="data/target_replenishment_profile.json")
    args = parser.parse_args()

    source = Path(args.input)
    report = json.loads(source.read_text(encoding="utf-8"))
    rows = report.get("parentOrdersByMakerHash") if isinstance(report, dict) else None
    if not isinstance(rows, list):
        raise SystemExit("input does not contain parentOrdersByMakerHash")

    filtered = [row for row in rows if isinstance(row, dict) and is_eth_bnb_5m(row.get("marketTitle"))]
    filtered.sort(key=lambda row: (str(row.get("marketId")), str(row.get("outcome")), str(row.get("firstExecutedAt"))))

    by_stream: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_price: dict[tuple[int, str, float], list[dict[str, Any]]] = defaultdict(list)
    tier_counts: Counter[str] = Counter()
    exact_tier_counts: Counter[str] = Counter()
    cost_tier_counts: Counter[str] = Counter()

    for row in filtered:
        mid = int(row.get("marketId") or 0)
        side = str(row.get("outcome") or "").upper()
        price = float(row.get("price") or 0.0)
        by_stream[(mid, side)].append(row)
        by_market[mid].append(row)
        by_price[(mid, side, round(price, 6))].append(row)

        pp = float(row.get("totalPotentialProfitUsdtApprox") or 0.0)
        ct = float(row.get("totalCostUsdtApprox") or 0.0)
        tier, rel = nearest_tier(pp)
        cost_tier, cost_rel = nearest_tier(ct)
        if rel <= 0.05:
            tier_counts[f"{tier:g}"] += 1
        if rel <= 0.005:
            exact_tier_counts[f"{tier:g}"] += 1
        if cost_rel <= 0.05:
            cost_tier_counts[f"{cost_tier:g}"] += 1

    transitions: list[dict[str, Any]] = []
    for (mid, side), stream in by_stream.items():
        stream.sort(key=lambda row: str(row.get("firstExecutedAt") or ""))
        for prev, cur in zip(stream, stream[1:]):
            p0 = float(prev.get("price") or 0.0)
            p1 = float(cur.get("price") or 0.0)
            prev_last = parse_iso(prev.get("lastExecutedAt"))
            cur_first = parse_iso(cur.get("firstExecutedAt"))
            gap = None
            if prev_last is not None and cur_first is not None:
                gap = (cur_first - prev_last).total_seconds()
            pp0 = float(prev.get("totalPotentialProfitUsdtApprox") or 0.0)
            pp1 = float(cur.get("totalPotentialProfitUsdtApprox") or 0.0)
            t0, e0 = nearest_tier(pp0)
            t1, e1 = nearest_tier(pp1)
            transitions.append(
                {
                    "marketId": mid,
                    "outcome": side,
                    "previousHash": prev.get("makerHash"),
                    "nextHash": cur.get("makerHash"),
                    "previousPrice": p0,
                    "nextPrice": p1,
                    "priceDelta": p1 - p0,
                    "absoluteTicks": round(abs(p1 - p0) / 0.01, 6),
                    "gapSecondsFromPreviousLastFill": gap,
                    "samePrice": abs(p1 - p0) < 0.005,
                    "previousPotentialProfit": pp0,
                    "nextPotentialProfit": pp1,
                    "sameMatchedPotentialTier": e0 <= 0.05 and e1 <= 0.05 and t0 == t1,
                    "matchedTier": t1 if e0 <= 0.05 and e1 <= 0.05 and t0 == t1 else None,
                }
            )

    same_price = [row for row in transitions if row["samePrice"]]
    same_price_5s = [row for row in same_price if row["gapSecondsFromPreviousLastFill"] is not None and 0 <= row["gapSecondsFromPreviousLastFill"] <= 5]
    same_price_15s = [row for row in same_price if row["gapSecondsFromPreviousLastFill"] is not None and 0 <= row["gapSecondsFromPreviousLastFill"] <= 15]
    overlap = [row for row in transitions if row["gapSecondsFromPreviousLastFill"] is not None and row["gapSecondsFromPreviousLastFill"] < 0]
    within_one_tick = [row for row in transitions if row["absoluteTicks"] <= 1.000001]
    within_two_ticks = [row for row in transitions if row["absoluteTicks"] <= 2.000001]

    repeated_price_clusters = []
    for (mid, side, price), cluster in by_price.items():
        if len(cluster) < 2:
            continue
        cluster = sorted(cluster, key=lambda row: str(row.get("firstExecutedAt") or ""))
        repeated_price_clusters.append(
            {
                "marketId": mid,
                "marketTitle": cluster[0].get("marketTitle"),
                "outcome": side,
                "price": price,
                "distinctMakerHashes": len(cluster),
                "firstExecutedAt": cluster[0].get("firstExecutedAt"),
                "lastExecutedAt": cluster[-1].get("lastExecutedAt"),
                "potentialProfits": [float(row.get("totalPotentialProfitUsdtApprox") or 0.0) for row in cluster],
                "makerHashes": [row.get("makerHash") for row in cluster],
            }
        )
    repeated_price_clusters.sort(key=lambda row: row["distinctMakerHashes"], reverse=True)

    pp_match = 0
    cost_match = 0
    sub_one_cost = 0
    for row in filtered:
        pp = float(row.get("totalPotentialProfitUsdtApprox") or 0.0)
        cost = float(row.get("totalCostUsdtApprox") or 0.0)
        _, pp_rel = nearest_tier(pp)
        _, cost_rel = nearest_tier(cost)
        if pp_rel <= 0.05:
            pp_match += 1
            if cost < 1.0:
                sub_one_cost += 1
        if cost_rel <= 0.05:
            cost_match += 1

    stream_counts = [len(stream) for stream in by_stream.values()]
    market_counts = [len(stream) for stream in by_market.values()]
    gaps = [float(row["gapSecondsFromPreviousLastFill"]) for row in transitions if row["gapSecondsFromPreviousLastFill"] is not None]

    summary = {
        "source": str(source),
        "filter": "ETH/BNB exact 5-minute market titles only",
        "parentOrdersApprox": len(filtered),
        "markets": len(by_market),
        "marketOutcomeStreams": len(by_stream),
        "parentOrdersPerMarket": {
            "median": statistics.median(market_counts) if market_counts else None,
            "p90": percentile([float(x) for x in market_counts], 0.90),
            "max": max(market_counts, default=0),
        },
        "parentOrdersPerMarketOutcome": {
            "median": statistics.median(stream_counts) if stream_counts else None,
            "p90": percentile([float(x) for x in stream_counts], 0.90),
            "max": max(stream_counts, default=0),
        },
        "potentialProfitTierWithin5Pct": {
            "count": pp_match,
            "share": pp_match / len(filtered) if filtered else None,
            "byTier": dict(sorted(tier_counts.items(), key=lambda item: float(item[0]))),
        },
        "potentialProfitTierWithin0_5Pct": {
            "count": sum(exact_tier_counts.values()),
            "share": sum(exact_tier_counts.values()) / len(filtered) if filtered else None,
            "byTier": dict(sorted(exact_tier_counts.items(), key=lambda item: float(item[0]))),
        },
        "costTierWithin5Pct": {
            "count": cost_match,
            "share": cost_match / len(filtered) if filtered else None,
            "byTier": dict(sorted(cost_tier_counts.items(), key=lambda item: float(item[0]))),
        },
        "matchedPotentialTierButFilledCostBelowBinance1Usdt": {
            "count": sub_one_cost,
            "shareOfPotentialTierMatches": sub_one_cost / pp_match if pp_match else None,
        },
        "consecutiveParentTransitions": len(transitions),
        "samePriceDistinctHashTransitions": {
            "count": len(same_price),
            "share": len(same_price) / len(transitions) if transitions else None,
            "within5Seconds": len(same_price_5s),
            "within15Seconds": len(same_price_15s),
        },
        "priceMoveTransitions": {
            "withinOneTickCount": len(within_one_tick),
            "withinOneTickShare": len(within_one_tick) / len(transitions) if transitions else None,
            "withinTwoTicksCount": len(within_two_ticks),
            "withinTwoTicksShare": len(within_two_ticks) / len(transitions) if transitions else None,
        },
        "overlappingExecutionWindows": {
            "count": len(overlap),
            "share": len(overlap) / len(transitions) if transitions else None,
            "note": "negative gap means next makerHash began filling before the prior hash's last observed fill; this is evidence compatible with concurrent laddering, but placement/cancel events are not public",
        },
        "transitionGapSeconds": {
            "median": statistics.median(gaps) if gaps else None,
            "p10": percentile(gaps, 0.10),
            "p90": percentile(gaps, 0.90),
        },
        "repeatedSamePriceClusters": len(repeated_price_clusters),
    }

    output = {
        "summary": summary,
        "topRepeatedSamePriceClusters": repeated_price_clusters[:100],
        "samePriceRapidRefillExamples": sorted(
            same_price_15s,
            key=lambda row: (float(row["gapSecondsFromPreviousLastFill"] or 0.0), row["marketId"]),
        )[:100],
        "overlappingExecutionExamples": overlap[:100],
        "caveats": [
            "makerHash is strong evidence of distinct signed parent orders, but the API exposes matches rather than placement/cancel events.",
            "totalShares/totalCost/totalPotentialProfit are observed filled amounts. A partially filled or later-cancelled parent order can have a larger original requested size, so tier-match rates are conservative rather than exact full-order-size rates.",
            "same-price sequential hashes strongly support replenishment; negative execution-window gaps support possible simultaneous laddering, but neither alone proves exact placement timing.",
        ],
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"wrote {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
