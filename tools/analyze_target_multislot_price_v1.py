from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path


MARKET_IDS = (1916869, 1917324, 1912961)
EPS = 1e-9


def load_rows(connection: sqlite3.Connection, market_id: int) -> list[dict]:
    rows = connection.execute(
        """
        SELECT event_ms, role, side, order_hash, price, shares
        FROM eth_events
        WHERE market_id = ? AND price IS NOT NULL
        ORDER BY event_ms, id
        """,
        (market_id,),
    ).fetchall()
    return [
        {
            "eventMs": int(row[0]),
            "role": str(row[1]),
            "side": str(row[2]),
            "orderHash": str(row[3]),
            "price": float(row[4]),
            "shares": float(row[5]),
        }
        for row in rows
    ]


def parent_price_rows(group: list[dict]) -> list[dict]:
    by_parent: dict[str, list[dict]] = defaultdict(list)
    for row in group:
        by_parent[row["orderHash"]].append(row)
    return [
        {
            "orderHash": order_hash,
            "meanPrice": statistics.fmean(row["price"] for row in rows),
            "roles": sorted({row["role"] for row in rows}),
            "shares": sum(row["shares"] for row in rows),
        }
        for order_hash, rows in sorted(by_parent.items())
    ]


def analyze_market(market_id: int, rows: list[dict]) -> dict:
    by_clock_side: dict[tuple[int, str], list[dict]] = defaultdict(list)
    by_clock: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    first_ms = min((row["eventMs"] for row in rows), default=0)
    for row in rows:
        by_clock_side[(row["eventMs"], row["side"])].append(row)
        by_clock[row["eventMs"]][row["side"]].append(row)

    same_side_atomic = []
    for (event_ms, side), group in sorted(by_clock_side.items()):
        parents = parent_price_rows(group)
        if len(parents) < 2:
            continue
        prices = sorted({round(parent["meanPrice"], 9) for parent in parents})
        same_side_atomic.append(
            {
                "eventMs": event_ms,
                "side": side,
                "physicalParents": len(parents),
                "prices": prices,
                "priceRange": max(prices) - min(prices),
                "parents": parents,
            }
        )

    by_bucket_side: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for row in rows:
        bucket = (row["eventMs"] - first_ms) // 5000
        by_bucket_side[(bucket, row["side"])].append(row)
    same_side_five_second = []
    for (bucket, side), group in sorted(by_bucket_side.items()):
        parents = parent_price_rows(group)
        if len(parents) < 2:
            continue
        prices = sorted({round(parent["meanPrice"], 9) for parent in parents})
        same_side_five_second.append(
            {
                "bucket": bucket,
                "side": side,
                "physicalParents": len(parents),
                "prices": prices,
                "priceRange": max(prices) - min(prices),
            }
        )

    opposite_side_exact = []
    for event_ms, sides in sorted(by_clock.items()):
        if "UP" not in sides or "DOWN" not in sides:
            continue
        up_mean = statistics.fmean(row["price"] for row in sides["UP"])
        down_mean = statistics.fmean(row["price"] for row in sides["DOWN"])
        opposite_side_exact.append(
            {
                "eventMs": event_ms,
                "upMean": up_mean,
                "downMean": down_mean,
                "sum": up_mean + down_mean,
                "upParents": len({row["orderHash"] for row in sides["UP"]}),
                "downParents": len({row["orderHash"] for row in sides["DOWN"]}),
            }
        )

    atomic_ranges = [group["priceRange"] for group in same_side_atomic]
    five_ranges = [group["priceRange"] for group in same_side_five_second]
    return {
        "marketId": market_id,
        "pricedRows": len(rows),
        "sameSideAtomicClock": {
            "groups": len(same_side_atomic),
            "multiPriceGroups": sum(len(group["prices"]) >= 2 for group in same_side_atomic),
            "rangeLe002": sum(value <= 0.02 + EPS for value in atomic_ranges),
            "rangeLe005": sum(value <= 0.05 + EPS for value in atomic_ranges),
            "rangeLe010": sum(value <= 0.10 + EPS for value in atomic_ranges),
            "medianRange": statistics.median(atomic_ranges) if atomic_ranges else None,
            "groupsDetail": same_side_atomic,
        },
        "sameSideFiveSecondBuckets": {
            "groups": len(same_side_five_second),
            "zeroDispersionGroups": sum(value <= EPS for value in five_ranges),
            "medianRange": statistics.median(five_ranges) if five_ranges else None,
            "groupsDetail": same_side_five_second,
        },
        "oppositeSideExactClocks": {
            "groups": len(opposite_side_exact),
            "withinFiveCents": sum(abs(row["sum"] - 1.0) <= 0.05 for row in opposite_side_exact),
            "medianSum": statistics.median(row["sum"] for row in opposite_side_exact)
            if opposite_side_exact
            else None,
            "meanSum": statistics.fmean(row["sum"] for row in opposite_side_exact)
            if opposite_side_exact
            else None,
            "groupsDetail": opposite_side_exact,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/research/r4_v0/p0_provenance_v1/target_eth_fill_legs_v3_snapshot_20260904.db"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/research/r4_v0/behavior_alignment_v1/"
            "TARGET_MULTISLOT_PRICE_RULE_INFERENCE_V1_RESULT_20260905.json"
        ),
    )
    args = parser.parse_args()

    connection = sqlite3.connect(args.db)
    try:
        markets = [analyze_market(market_id, load_rows(connection, market_id)) for market_id in MARKET_IDS]
    finally:
        connection.close()

    atomic = [market["sameSideAtomicClock"] for market in markets]
    five = [market["sameSideFiveSecondBuckets"] for market in markets]
    opposite = [market["oppositeSideExactClocks"] for market in markets]
    atomic_groups = sum(item["groups"] for item in atomic)
    atomic_multi = sum(item["multiPriceGroups"] for item in atomic)
    range_le002 = sum(item["rangeLe002"] for item in atomic)
    range_le005 = sum(item["rangeLe005"] for item in atomic)
    range_le010 = sum(item["rangeLe010"] for item in atomic)
    five_groups = sum(item["groups"] for item in five)
    five_zero = sum(item["zeroDispersionGroups"] for item in five)
    five_ranges = [group["priceRange"] for item in five for group in item["groupsDetail"]]
    opposite_rows = [row for item in opposite for row in item["groupsDetail"]]

    output = {
        "version": "TARGET_MULTISLOT_PRICE_RULE_INFERENCE_V1",
        "date": "2026-09-05",
        "researchOnly": True,
        "evidenceClass": "POST_MARKET_PHYSICAL_FILL_PARENT_PRICE_SHAPE",
        "marketIds": list(MARKET_IDS),
        "verdict": {
            "sameSideNearbyPriceLadder": (
                "PARTIALLY_SUPPORTED_AS_LOCAL_PHYSICAL_FILL_SHAPE"
                if atomic_multi and range_le005 / atomic_groups >= 0.5
                else "NOT_ESTABLISHED"
            ),
            "sameSideSingleCommonPriceFanout": "NOT_PRIMARY_PHYSICAL_PARENT_SHAPE",
            "oppositeSideComplementaryPair": (
                "SUPPORTED_AS_SMALL_EXACT_CLOCK_ASSOCIATION"
                if opposite_rows and all(abs(row["sum"] - 1.0) <= 0.05 for row in opposite_rows)
                else "NOT_ESTABLISHED"
            ),
        },
        "pooled": {
            "sameSideAtomicClockGroups": atomic_groups,
            "sameSideAtomicClockMultiPriceGroups": atomic_multi,
            "sameSideAtomicClockRangeLe002": range_le002,
            "sameSideAtomicClockRangeLe005": range_le005,
            "sameSideAtomicClockRangeLe010": range_le010,
            "sameSideAtomicClockMedianRange": statistics.median(
                [group["priceRange"] for item in atomic for group in item["groupsDetail"]]
            )
            if atomic_groups
            else None,
            "sameSideFiveSecondGroups": five_groups,
            "sameSideFiveSecondZeroDispersionGroups": five_zero,
            "sameSideFiveSecondZeroDispersionFraction": five_zero / five_groups if five_groups else None,
            "sameSideFiveSecondMedianRange": statistics.median(five_ranges) if five_ranges else None,
            "oppositeSideExactClockGroups": len(opposite_rows),
            "oppositeSideExactClockWithinFiveCents": sum(
                abs(row["sum"] - 1.0) <= 0.05 for row in opposite_rows
            ),
            "oppositeSideExactClockMedianSum": statistics.median(row["sum"] for row in opposite_rows)
            if opposite_rows
            else None,
            "oppositeSideExactClockMeanSum": statistics.fmean(row["sum"] for row in opposite_rows)
            if opposite_rows
            else None,
        },
        "markets": markets,
        "interpretationBoundary": [
            "raw order_hash is used to distinguish physical parents from FIFO responsibility allocations",
            "confirmed fills reveal realized parent prices, not every resting placement or cancel",
            "a same-clock price cluster may be a resting ladder, a taker sweep, or multiple generations",
            "Target evidence is offline architecture evidence and is excluded from OUR runtime",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "verdict": output["verdict"], "pooled": output["pooled"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
