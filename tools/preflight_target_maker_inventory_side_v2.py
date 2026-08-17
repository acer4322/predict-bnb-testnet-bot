from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BEHAVIOR = ROOT / "data" / "research" / "target_maker_direct_behavior_v1.csv"
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_inventory_conditioned_side_v2_preflight.json"
BUCKET_MS = 300_000


def _iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _read_behavior(path: Path) -> list[dict[str, Any]]:
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        rows = []
        for raw in csv.DictReader(handle):
            rows.append({
                "market_id": int(float(raw["market_id"])),
                "placement_ms": int(float(raw["placement_first_ms"])),
            })
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast overlap preflight for Maker inventory-conditioned side V2")
    parser.add_argument("--behavior-dataset", type=Path, default=DEFAULT_BEHAVIOR)
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--asset", default="BTC")
    args = parser.parse_args()

    behavior = _read_behavior(args.behavior_dataset)
    db = lifecycle._connect_ro(args.target_db)
    try:
        events, event_meta = lifecycle._load_official_events(db, asset=str(args.asset).upper())
    finally:
        db.close()

    behavior_markets = {int(row["market_id"]) for row in behavior}
    event_markets = {int(row["market_id"]) for row in events}
    exact_overlap = sorted(behavior_markets & event_markets)

    behavior_times = [int(row["placement_ms"]) for row in behavior]
    event_times = [int(row["event_ms"]) for row in events]
    behavior_min = min(behavior_times) if behavior_times else None
    behavior_max = max(behavior_times) if behavior_times else None
    event_min = min(event_times) if event_times else None
    event_max = max(event_times) if event_times else None
    time_overlap = bool(
        behavior_min is not None and behavior_max is not None and event_min is not None and event_max is not None
        and max(behavior_min, event_min) <= min(behavior_max, event_max)
    )

    event_times_by_market: dict[int, list[int]] = defaultdict(list)
    for row in events:
        event_times_by_market[int(row["market_id"])].append(int(row["event_ms"]))
    exact_behavior_rows = [row for row in behavior if int(row["market_id"]) in set(exact_overlap)]
    exact_rows_with_prior_event = 0
    for row in exact_behavior_rows:
        times = event_times_by_market[int(row["market_id"])]
        if times and min(times) < int(row["placement_ms"]):
            exact_rows_with_prior_event += 1

    behavior_bucket_markets: dict[int, set[int]] = defaultdict(set)
    event_bucket_markets: dict[int, set[int]] = defaultdict(set)
    for row in behavior:
        behavior_bucket_markets[int(row["placement_ms"]) // BUCKET_MS].add(int(row["market_id"]))
    for row in events:
        event_bucket_markets[int(row["event_ms"]) // BUCKET_MS].add(int(row["market_id"]))
    common_buckets = sorted(set(behavior_bucket_markets) & set(event_bucket_markets))
    unique_bucket_pairs = []
    for bucket in common_buckets:
        left = behavior_bucket_markets[bucket]
        right = event_bucket_markets[bucket]
        if len(left) == 1 and len(right) == 1:
            unique_bucket_pairs.append({
                "bucketStartMs": bucket * BUCKET_MS,
                "behaviorMarketId": next(iter(left)),
                "officialMarketId": next(iter(right)),
            })

    payload = {
        "behaviorRows": len(behavior),
        "behaviorMarkets": len(behavior_markets),
        "officialFillLegs": len(events),
        "officialMarkets": len(event_markets),
        "exactMarketOverlap": len(exact_overlap),
        "exactMarketOverlapSample": exact_overlap[:20],
        "behaviorRowsOnExactOverlapMarkets": len(exact_behavior_rows),
        "behaviorRowsWithAnyStrictPriorOfficialEventOnExactMarket": exact_rows_with_prior_event,
        "behaviorPlacementRange": {"firstMs": behavior_min, "firstUtc": _iso(behavior_min), "lastMs": behavior_max, "lastUtc": _iso(behavior_max)},
        "officialEventRange": {"firstMs": event_min, "firstUtc": _iso(event_min), "lastMs": event_max, "lastUtc": _iso(event_max)},
        "timeRangesOverlap": time_overlap,
        "commonFiveMinuteBuckets": len(common_buckets),
        "uniqueFiveMinuteBucketPairs": len(unique_bucket_pairs),
        "uniqueFiveMinuteBucketPairSample": unique_bucket_pairs[:20],
        "officialEventMeta": event_meta,
        "interpretation": (
            "If exactMarketOverlap=0 but commonFiveMinuteBuckets/uniqueFiveMinuteBucketPairs are positive, the two datasets likely use different market-id namespaces and should be mapped by verified 5-minute market identity before inventory modeling. "
            "If timeRangesOverlap=false, the datasets do not cover the same period and must not be joined."
        ),
    }
    report = args.report.expanduser().resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("TARGET MAKER INVENTORY SIDE V2 PREFLIGHT", flush=True)
    print(f"behavior: rows={len(behavior)} markets={len(behavior_markets)}", flush=True)
    print(f"official: fill_legs={len(events)} markets={len(event_markets)}", flush=True)
    print(f"exact market overlap={len(exact_overlap)} | behavior rows on overlap={len(exact_behavior_rows)} | rows with strict-prior official event={exact_rows_with_prior_event}", flush=True)
    print(f"time ranges overlap={time_overlap} | behavior={_iso(behavior_min)}..{_iso(behavior_max)} | official={_iso(event_min)}..{_iso(event_max)}", flush=True)
    print(f"5m common buckets={len(common_buckets)} | unique candidate market pairs={len(unique_bucket_pairs)}", flush=True)
    for row in unique_bucket_pairs[:10]:
        print(f"  bucket={_iso(int(row['bucketStartMs']))} behavior_market={row['behaviorMarketId']} official_market={row['officialMarketId']}", flush=True)
    print(f"Report: {report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
