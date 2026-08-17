from __future__ import annotations

import bisect
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from .target_eth_taker_behavior_v1 import (
    DATASET_VERSION as BEHAVIOR_VERSION,
    DEFAULT_ETH_TARGET_DB,
    DEFAULT_PREDICT_DB,
    PREDICT_FEATURES,
    _connect_readonly,
    _has_table,
    _load_predict_market,
    _predict_features,
    _target_meta,
    _target_parents,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_eth_taker_hazard_v1.csv"
DEFAULT_META_OUTPUT = ROOT / "data" / "research" / "target_eth_taker_hazard_v1.meta.json"
DATASET_VERSION = "TARGET_ETH_TAKER_HAZARD_V1_UNIFORM_SECOND_CLOCK"
HORIZONS_SECONDS = (1, 2, 5)

METADATA_COLUMNS = [
    "dataset_version",
    "market_id",
    "decision_sampled_at_ms",
    "decision_second_bucket_ms",
    "same_second_taker_parents_ambiguous",
]
LABEL_COLUMNS: list[str] = []
for seconds in HORIZONS_SECONDS:
    LABEL_COLUMNS.extend(
        [
            f"label_next_taker_any_{seconds}s",
            f"next_taker_parent_count_{seconds}s",
            f"next_taker_total_shares_{seconds}s",
        ]
    )
OUTPUT_COLUMNS = METADATA_COLUMNS + PREDICT_FEATURES + LABEL_COLUMNS


def _eligible_markets(
    target: sqlite3.Connection, deployed_at_ms: int, excluded_market_id: int | None
) -> list[int]:
    if not _has_table(target, "maker_book_inference_markets"):
        raise RuntimeError("8779 maker_book_inference_markets is missing")
    query = """SELECT DISTINCT m.market_id
                 FROM maker_book_inference_markets m
                 JOIN maker_book_inference_updates u ON u.market_id=m.market_id
                WHERE m.first_seen_ms>=?"""
    rows = target.execute(query, (int(deployed_at_ms),)).fetchall()
    return [
        int(row[0])
        for row in rows
        if excluded_market_id is None or int(row[0]) != int(excluded_market_id)
    ]


def _events_by_market(parents: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for parent in parents:
        row = dict(parent)
        row["secondBucketMs"] = (int(row["target_event_ms"]) // 1000) * 1000
        grouped[int(row["market_id"])].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: (int(row["secondBucketMs"]), str(row["parent_id"])))
    return grouped


def _events_between(
    events: list[dict[str, Any]], low_bucket_ms: int, high_bucket_ms: int
) -> list[dict[str, Any]]:
    buckets = [int(row["secondBucketMs"]) for row in events]
    left = bisect.bisect_left(buckets, int(low_bucket_ms))
    right = bisect.bisect_right(buckets, int(high_bucket_ms))
    return events[left:right]


def build_target_eth_taker_hazard_dataset(
    *,
    target_db_path: Path = DEFAULT_ETH_TARGET_DB,
    predict_db_path: Path = DEFAULT_PREDICT_DB,
    output_path: Path = DEFAULT_OUTPUT,
    meta_output_path: Path = DEFAULT_META_OUTPUT,
) -> dict[str, Any]:
    target = _connect_readonly(target_db_path)
    predict = _connect_readonly(predict_db_path)
    try:
        if not _has_table(predict, "predict_fun_trajectory"):
            raise RuntimeError("8771 predict_fun_trajectory is missing")
        deployed_at_ms, excluded_market_id = _target_meta(target)
        parents = _target_parents(target, deployed_at_ms, excluded_market_id)
        events_by_market = _events_by_market(parents)
        markets = _eligible_markets(target, deployed_at_ms, excluded_market_id)

        output_rows: list[dict[str, Any]] = []
        for market_id in markets:
            raw_rows = [
                dict(row)
                for row in predict.execute(
                    """SELECT sampled_at_ms,seconds_left,up_mid,down_mid,up_bid,up_ask,down_bid,down_ask,
                              source_age_ms,receipt_age_ms
                         FROM predict_fun_trajectory
                        WHERE asset='ETH' AND market_id=?
                        ORDER BY sampled_at_ms""",
                    (int(market_id),),
                )
            ]
            if not raw_rows:
                continue
            # Keep one latest public state per wall-clock second. This gives a
            # uniform decision clock instead of conditioning negatives on Target activity.
            by_second: dict[int, dict[str, Any]] = {}
            for row in raw_rows:
                sampled = int(row["sampled_at_ms"])
                by_second[(sampled // 1000) * 1000] = row
            rows = list(by_second.values())
            rows.sort(key=lambda row: int(row["sampled_at_ms"]))
            times = [int(row["sampled_at_ms"]) for row in rows]
            events = events_by_market.get(int(market_id), [])

            for snapshot in rows:
                sampled = int(snapshot["sampled_at_ms"])
                decision_bucket = (sampled // 1000) * 1000
                seconds_left = snapshot.get("seconds_left")
                try:
                    seconds_left_value = float(seconds_left)
                except (TypeError, ValueError):
                    continue
                if seconds_left_value < 0 or seconds_left_value > 305:
                    continue
                same_second = _events_between(events, decision_bucket, decision_bucket)
                row: dict[str, Any] = {
                    "dataset_version": DATASET_VERSION,
                    "market_id": int(market_id),
                    "decision_sampled_at_ms": sampled,
                    "decision_second_bucket_ms": decision_bucket,
                    "same_second_taker_parents_ambiguous": len(same_second),
                }
                row.update(
                    _predict_features(
                        snapshot,
                        times=times,
                        rows=rows,
                        event_bucket_start_ms=sampled,
                    )
                )
                # This is a public-state decision row, not an as-of join to a
                # future event, so signal age is zero by construction.
                row["signal_age_ms"] = 0
                for horizon in HORIZONS_SECONDS:
                    future = _events_between(
                        events,
                        decision_bucket + 1000,
                        decision_bucket + horizon * 1000,
                    )
                    row[f"label_next_taker_any_{horizon}s"] = int(bool(future))
                    row[f"next_taker_parent_count_{horizon}s"] = len(future)
                    row[f"next_taker_total_shares_{horizon}s"] = sum(
                        float(item.get("target_latest_shares") or 0.0) for item in future
                    )
                output_rows.append(row)

        output_path = output_path.expanduser().resolve()
        meta_output_path = meta_output_path.expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp = output_path.with_suffix(output_path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in output_rows:
                writer.writerow({column: row.get(column) for column in OUTPUT_COLUMNS})
        temp.replace(output_path)

        rates = {}
        for horizon in HORIZONS_SECONDS:
            label = f"label_next_taker_any_{horizon}s"
            positives = sum(int(row[label]) for row in output_rows)
            rates[label] = {
                "positives": positives,
                "positiveRate": positives / len(output_rows) if output_rows else None,
            }
        ambiguous = sum(int(row["same_second_taker_parents_ambiguous"] > 0) for row in output_rows)
        report = {
            "datasetVersion": DATASET_VERSION,
            "behaviorDatasetVersion": BEHAVIOR_VERSION,
            "asset": "ETH",
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "rows": len(output_rows),
            "markets": len({int(row["market_id"]) for row in output_rows}),
            "eligibleCollectorMarkets": len(markets),
            "targetTakerParents": len(parents),
            "labelRates": rates,
            "rowsWithSameSecondAmbiguousTakerRate": ambiguous / len(output_rows) if output_rows else None,
            "decisionClock": "one latest 8771 ETH Predict.fun public snapshot per wall-clock second",
            "timestampBoundary": (
                "Target event seconds are never labels for the same decision second. Horizon labels begin at "
                "decisionBucket+1 second, so second-quantized Target timestamps cannot leak into features."
            ),
            "coverageBoundary": (
                "Only ETH markets recorded by the forward-only 8779 collector after deployment are used. "
                "No historical period before collector coverage is treated as negative."
            ),
            "sources": {
                "targetEthDb": str(target_db_path),
                "predictObserverDb": str(predict_db_path),
            },
            "output": str(output_path),
        }
        tmp_meta = meta_output_path.with_suffix(meta_output_path.suffix + ".tmp")
        tmp_meta.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_meta.replace(meta_output_path)
        return report
    finally:
        target.close()
        predict.close()
