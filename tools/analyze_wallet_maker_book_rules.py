from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import statistics
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_STRUCTURE = ROOT / "artifacts" / "wallet_profit_strategy" / "target_maker_strategy_forward_report.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "wallet_profit_strategy" / "target_maker_book_rules_report.json"


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[round((len(ordered) - 1) * fraction)])


def fraction(numerator: int | float, denominator: int | float) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def load_structure(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def analyze(db_path: Path, structure_path: Path) -> dict[str, Any]:
    con = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        events = [dict(row) for row in con.execute(
            "SELECT * FROM maker_book_inference_target_events ORDER BY market_id,target_event_ms"
        )]
        matched = [row for row in events if row["status"] == "MATCHED"]
        markets = {
            int(row["market_id"]): dict(row)
            for row in con.execute("SELECT * FROM maker_book_inference_markets")
        }
        updates: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for row in con.execute(
            "SELECT market_id,source_timestamp_ms,changes_z FROM maker_book_inference_updates ORDER BY market_id,source_timestamp_ms"
        ):
            changes = json.loads(zlib.decompress(row["changes_z"]).decode("utf-8"))
            updates[int(row["market_id"])].append((int(row["source_timestamp_ms"]), changes))
    finally:
        con.close()

    market_side: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    batches: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    orders: dict[str, list[dict[str, Any]]] = defaultdict(list)
    same_price_streams: dict[tuple[int, str, float], list[dict[str, Any]]] = defaultdict(list)
    for event in matched:
        market_side[(int(event["market_id"]), str(event["side"]))].append(event)
        batches[(int(event["market_id"]), int(event["target_event_ms"]) // 1000)].append(event)
        if event.get("order_hash"):
            orders[str(event["order_hash"])].append(event)
        same_price_streams[(int(event["market_id"]), str(event["side"]), float(event["target_price"]))].append(event)

    unique_levels = [len({float(row["target_price"]) for row in stream}) for stream in market_side.values()]
    price_spans = [
        max(float(row["target_price"]) for row in stream) - min(float(row["target_price"]) for row in stream)
        for stream in market_side.values()
    ]
    target_shares = [float(row["target_shares"]) for row in events]
    event_delays = [float(row["event_delay_ms"]) for row in matched if row.get("event_delay_ms") is not None]
    multi_batches = [stream for stream in batches.values() if len(stream) > 1]

    windows = (500, 1_000, 2_000, 5_000, 10_000)
    public_readd = {window: 0 for window in windows}
    public_large_readd = {window: 0 for window in windows}
    first_readd_delay: list[float] = []
    for event in matched:
        stream = updates.get(int(event["market_id"]), [])
        times = [item[0] for item in stream]
        matched_ms = int(event["matched_source_ms"])
        start = bisect.bisect_right(times, matched_ms)
        found: dict[int, float] = {}
        first: tuple[int, float] | None = None
        wanted = "bids" if event["native_book_side"] == "BID" else "asks"
        for source_ms, changes in stream[start:]:
            delay = source_ms - matched_ms
            if delay > windows[-1]:
                break
            positive = sum(
                float(change.get("delta") or 0)
                for change in changes.get(wanted, [])
                if abs(float(change.get("price") or -1) - float(event["native_price"])) <= 1e-9
                and float(change.get("delta") or 0) > 0
            )
            if positive <= 0:
                continue
            if first is None:
                first = (delay, positive)
            for window in windows:
                if delay <= window and window not in found:
                    found[window] = positive
        if first is not None:
            first_readd_delay.append(float(first[0]))
        for window, size in found.items():
            public_readd[window] += 1
            if size >= 0.8 * float(event["target_shares"]):
                public_large_readd[window] += 1

    different_order_same_price_gaps: list[float] = []
    for stream in same_price_streams.values():
        ordered = sorted(stream, key=lambda row: int(row["target_event_ms"]))
        for before, after in zip(ordered, ordered[1:]):
            if before.get("order_hash") != after.get("order_hash"):
                different_order_same_price_gaps.append(float(after["target_event_ms"] - before["target_event_ms"]))

    seconds_left = [
        (int(markets[int(row["market_id"])]["window_end_ms"]) - int(row["target_event_ms"])) / 1000
        for row in events
        if int(row["market_id"]) in markets and markets[int(row["market_id"])].get("window_end_ms")
    ]
    structure = load_structure(structure_path)
    historical = structure.get("historicalStructure", {})
    lifetime = structure.get("marketLifetime", {})
    signals = structure.get("forwardSignalEvidence", {})

    return {
        "generatedAt": datetime.now(timezone.utc).astimezone().isoformat(),
        "scope": {
            "database": str(db_path),
            "markets": len({int(row["market_id"]) for row in events}),
            "targetMakerEvents": len(events),
            "matchedEvents": len(matched),
            "matchingRate": fraction(len(matched), len(events)),
            "boundary": "forward-only retained target Maker BID fills; deployment market excluded",
            "identityCaveat": "public level changes are anonymous; target ownership is certain only for API fill legs, not public-book additions",
        },
        "orderShape": {
            "integerCentPriceShare": fraction(sum(abs(price * 100 - round(price * 100)) <= 1e-8 for price in [float(row["target_price"]) for row in events]), len(events)),
            "targetSharesMedian": percentile(target_shares, 0.5),
            "targetSharesMode": Counter(target_shares).most_common(1)[0][0] if target_shares else None,
            "exact18ShareEventShare": fraction(sum(abs(value - 18) <= 1e-9 for value in target_shares), len(target_shares)),
            "historicalParentExact18Share": historical.get("observedParentSize", {}).get("exact18Share"),
            "uniqueFilledLevelsPerMarketSide": {
                "median": percentile([float(value) for value in unique_levels], 0.5),
                "p90": percentile([float(value) for value in unique_levels], 0.9),
            },
            "filledPriceSpanPerMarketSide": {
                "median": percentile(price_spans, 0.5),
                "p90": percentile(price_spans, 0.9),
            },
            "sameSecondMultiEventBatchShare": fraction(len(multi_batches), len(batches)),
            "largestSameSecondBatch": max((len(stream) for stream in multi_batches), default=0),
            "repeatedOrderHashShare": fraction(sum(len(stream) > 1 for stream in orders.values()), len(orders)),
        },
        "refillEvidence": {
            "matchedEventDelayMs": {
                "median": percentile(event_delays, 0.5),
                "p90": percentile(event_delays, 0.9),
            },
            "publicSamePricePositiveDeltaRate": {
                str(window): fraction(public_readd[window], len(matched)) for window in windows
            },
            "publicSamePriceAtLeast80PctTargetRate": {
                str(window): fraction(public_large_readd[window], len(matched)) for window in windows
            },
            "firstPublicReaddDelayMs": {
                "observations": len(first_readd_delay),
                "median": percentile(first_readd_delay, 0.5),
                "p90": percentile(first_readd_delay, 0.9),
            },
            "differentTargetOrderSamePriceTransitions": len(different_order_same_price_gaps),
            "differentTargetOrderSamePriceWithin": {
                str(window): fraction(sum(gap <= window for gap in different_order_same_price_gaps), len(different_order_same_price_gaps))
                for window in windows
            },
            "interpretation": "fast anonymous re-adds plus new target order hashes filling again at the same price support refill/replacement, but do not reveal placement time or queue position",
        },
        "timingAndControl": {
            "secondsLeft": {
                "p10": percentile(seconds_left, 0.1),
                "median": percentile(seconds_left, 0.5),
                "p90": percentile(seconds_left, 0.9),
            },
            "last30VsPrior30ParentChange": lifetime.get("last30VsPrior30Change"),
            "suggestedFreezeSecondsLeft": lifetime.get("frozenActiveCutoffSecondsLeft"),
            "individualParentReducesImbalanceShare": historical.get("softInventory", {}).get("individualParentReducesShareImbalance"),
            "finalPairedCoverageMedian": historical.get("softInventory", {}).get("finalPairedShareCoverageMedian"),
            "directionScoreSideMatchRate": signals.get("features", {}).get("direction_score", {}).get("sideMatchRate"),
            "fillMinusPreEventBidMedian": signals.get("makerFillPriceMinusPreEventBid", {}).get("median"),
        },
        "ruleInference": {
            "highConfidence": [
                "two-sided one-cent BID ladders",
                "18-share parent order cap with smaller totals usually partial fills",
                "wide ladder exposure or frequent recentering: filled levels per market-side greatly exceed a narrow seven-level static grid",
                "fast refill/replacement at previously filled prices",
                "pooled soft inventory balancing rather than strict event-by-event alternation",
                "strong reduction of new/replacement activity near 30 seconds remaining",
            ],
            "rejected": [
                "Maker side chosen by the tested public directional micro-signals",
                "single narrow static ladder with no refill",
                "strict UP then DOWN one-generation pairing",
            ],
            "mostLikelyPolicy": {
                "grid": "both outcomes, integer-cent BID levels",
                "unitShares": 18,
                "coverage": "broad rails plus a recentered active band; exact simultaneously-live width remains unidentified",
                "refill": "rapid same-price replacement, often within 1-5 seconds, while inventory remains inside a soft pooled corridor",
                "inventory": "rebalance aggregate paired coverage, not every individual fill",
                "freeze": "stop or sharply reduce new/replacement Maker quotes around 30 seconds left",
                "takerBoundary": "directional conviction and residual risk are handled by a separate Taker layer",
            },
            "notIdentifiableFromFills": [
                "exact initial 0.01-0.99 coverage versus a moving subset",
                "unfilled cancellations and order lifetime",
                "recenter signal source and threshold",
                "queue position, private order generation identity and maker rebates",
            ],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Infer target Maker posting rules from forward full-book matches")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--structure", type=Path, default=DEFAULT_STRUCTURE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = analyze(args.db, args.structure)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "scope": report["scope"], "ruleInference": report["ruleInference"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
