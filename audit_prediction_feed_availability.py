from __future__ import annotations

import argparse
import bisect
import csv
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def epoch_ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def nearest_row(
    timestamps: list[int],
    rows: list[dict[str, Any]],
    target_ns: int,
) -> tuple[dict[str, Any] | None, float | None]:
    if not timestamps:
        return None, None
    index = bisect.bisect_left(timestamps, target_ns)
    candidates: list[int] = []
    if index < len(timestamps):
        candidates.append(index)
    if index > 0:
        candidates.append(index - 1)
    selected = min(
        candidates,
        key=lambda item: abs(timestamps[item] - target_ns),
    )
    gap_ms = abs(timestamps[selected] - target_ns) / 1_000_000
    return rows[selected], gap_ms


def group_intervals(
    rows: list[dict[str, Any]],
    *,
    maximum_gap_seconds: float,
) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for row in rows:
        timestamp = parse_time(str(row["observation_at"]))
        if timestamp is None:
            continue
        key = (
            row["verdict"],
            row["expected_market_id"],
            row["snapshot_market_id"],
        )

        if current is None:
            current = {
                "verdict": row["verdict"],
                "expected_market_id": row["expected_market_id"],
                "snapshot_market_id": row["snapshot_market_id"],
                "start": timestamp,
                "end": timestamp,
                "samples": 1,
                "max_snapshot_gap_ms": row["snapshot_gap_ms"],
            }
            continue

        last_end: datetime = current["end"]
        gap_seconds = (timestamp - last_end).total_seconds()
        current_key = (
            current["verdict"],
            current["expected_market_id"],
            current["snapshot_market_id"],
        )
        if key == current_key and 0 <= gap_seconds <= maximum_gap_seconds:
            current["end"] = timestamp
            current["samples"] += 1
            if row["snapshot_gap_ms"] is not None:
                existing = current["max_snapshot_gap_ms"]
                current["max_snapshot_gap_ms"] = max(
                    float(existing or 0.0),
                    float(row["snapshot_gap_ms"]),
                )
            continue

        intervals.append(current)
        current = {
            "verdict": row["verdict"],
            "expected_market_id": row["expected_market_id"],
            "snapshot_market_id": row["snapshot_market_id"],
            "start": timestamp,
            "end": timestamp,
            "samples": 1,
            "max_snapshot_gap_ms": row["snapshot_gap_ms"],
        }

    if current is not None:
        intervals.append(current)

    output: list[dict[str, Any]] = []
    for interval in intervals:
        duration = max(
            0.0,
            (interval["end"] - interval["start"]).total_seconds(),
        )
        output.append(
            {
                "verdict": interval["verdict"],
                "expected_market_id": interval["expected_market_id"],
                "snapshot_market_id": interval["snapshot_market_id"],
                "start": interval["start"].isoformat(),
                "end": interval["end"].isoformat(),
                "duration_seconds": duration,
                "samples": interval["samples"],
                "max_snapshot_gap_ms": interval["max_snapshot_gap_ms"],
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare simulation current-market observations with retained "
            "microstructure snapshots to locate Prediction feed outages."
        )
    )
    parser.add_argument(
        "--simulation-db",
        default="data/simulation.db",
    )
    parser.add_argument(
        "--microstructure-db",
        default="data/microstructure.db",
    )
    parser.add_argument(
        "--from-time",
        required=True,
        help="ISO-8601 timestamp, for example 2026-08-01T02:00:00+08:00",
    )
    parser.add_argument("--to-time", default=None)
    parser.add_argument(
        "--max-snapshot-gap-ms",
        type=float,
        default=3000.0,
    )
    parser.add_argument(
        "--output-prefix",
        default="prediction_feed_availability",
    )
    args = parser.parse_args()

    simulation_path = Path(args.simulation_db).resolve()
    micro_path = Path(args.microstructure_db).resolve()
    for path in (simulation_path, micro_path):
        if not path.exists():
            raise SystemExit(f"Database not found: {path}")

    start = parse_time(args.from_time)
    end = parse_time(args.to_time) if args.to_time else None
    if start is None:
        raise SystemExit("Invalid --from-time")
    if args.to_time and end is None:
        raise SystemExit("Invalid --to-time")

    sim = sqlite3.connect(simulation_path)
    sim.row_factory = sqlite3.Row
    micro = sqlite3.connect(micro_path)
    micro.row_factory = sqlite3.Row

    observation_columns = {
        row["name"]
        for row in sim.execute(
            "PRAGMA table_info(observations)"
        ).fetchall()
    }
    required_observation = {"timestamp", "market_id"}
    missing = required_observation - observation_columns
    if missing:
        raise SystemExit(
            f"observations table missing columns: {sorted(missing)}"
        )

    snapshot_columns = {
        row["name"]
        for row in micro.execute(
            "PRAGMA table_info(microstructure_snapshots)"
        ).fetchall()
    }
    required_snapshot = {
        "timestamp_ns",
        "market_id",
        "prediction_up_mid",
    }
    missing = required_snapshot - snapshot_columns
    if missing:
        raise SystemExit(
            "microstructure_snapshots missing columns: "
            f"{sorted(missing)}"
        )

    observations: list[dict[str, Any]] = []
    for row in sim.execute(
        """SELECT timestamp, market_id
             FROM observations
            ORDER BY id ASC"""
    ):
        timestamp = parse_time(str(row["timestamp"]))
        if timestamp is None or timestamp < start:
            continue
        if end is not None and timestamp > end:
            continue
        observations.append(
            {
                "timestamp": timestamp,
                "timestamp_ns": epoch_ns(timestamp),
                "market_id": int(row["market_id"]),
            }
        )

    start_ns = epoch_ns(start)
    end_ns = epoch_ns(end) if end is not None else 2**63 - 1
    snapshots = [
        {
            "timestamp_ns": int(row["timestamp_ns"]),
            "market_id": (
                int(row["market_id"])
                if row["market_id"] is not None
                else None
            ),
            "prediction_up_mid": row["prediction_up_mid"],
        }
        for row in micro.execute(
            """SELECT timestamp_ns, market_id, prediction_up_mid
                 FROM microstructure_snapshots
                WHERE timestamp_ns BETWEEN ? AND ?
                ORDER BY timestamp_ns ASC""",
            (start_ns, end_ns),
        ).fetchall()
    ]
    snapshot_times = [row["timestamp_ns"] for row in snapshots]

    compared: list[dict[str, Any]] = []
    verdict_counts: Counter[str] = Counter()

    for observation in observations:
        snapshot, gap_ms = nearest_row(
            snapshot_times,
            snapshots,
            observation["timestamp_ns"],
        )
        expected_market_id = observation["market_id"]

        if snapshot is None or gap_ms is None:
            verdict = "NO_MICRO_SNAPSHOT"
            snapshot_market_id = None
            prediction_up_mid = None
        elif gap_ms > args.max_snapshot_gap_ms:
            verdict = "NO_MICRO_SNAPSHOT"
            snapshot_market_id = snapshot["market_id"]
            prediction_up_mid = snapshot["prediction_up_mid"]
        else:
            snapshot_market_id = snapshot["market_id"]
            prediction_up_mid = snapshot["prediction_up_mid"]
            if snapshot_market_id is None:
                verdict = "NO_PREDICTION_MARKET_ID"
            elif snapshot_market_id != expected_market_id:
                verdict = "MARKET_ID_MISMATCH"
            elif prediction_up_mid is None:
                verdict = "NO_ACCEPTED_PREDICTION_BOOK"
            else:
                verdict = "ACTIVE_MATCH"

        record = {
            "observation_at": observation["timestamp"].isoformat(),
            "expected_market_id": expected_market_id,
            "snapshot_market_id": snapshot_market_id,
            "prediction_up_mid": prediction_up_mid,
            "snapshot_gap_ms": gap_ms,
            "verdict": verdict,
        }
        compared.append(record)
        verdict_counts[verdict] += 1

    intervals = group_intervals(
        compared,
        maximum_gap_seconds=5.0,
    )

    prefix = Path(args.output_prefix).resolve()
    rows_csv = prefix.with_name(prefix.name + "_rows.csv")
    intervals_csv = prefix.with_name(prefix.name + "_intervals.csv")
    summary_json = prefix.with_name(prefix.name + "_summary.json")

    row_fields = [
        "observation_at",
        "expected_market_id",
        "snapshot_market_id",
        "prediction_up_mid",
        "snapshot_gap_ms",
        "verdict",
    ]
    with rows_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=row_fields)
        writer.writeheader()
        writer.writerows(compared)

    interval_fields = [
        "verdict",
        "expected_market_id",
        "snapshot_market_id",
        "start",
        "end",
        "duration_seconds",
        "samples",
        "max_snapshot_gap_ms",
    ]
    with intervals_csv.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=interval_fields)
        writer.writeheader()
        writer.writerows(intervals)

    suspect = [
        item
        for item in intervals
        if item["verdict"] != "ACTIVE_MATCH"
    ]
    longest = sorted(
        suspect,
        key=lambda item: (
            float(item["duration_seconds"]),
            int(item["samples"]),
        ),
        reverse=True,
    )[:20]

    first_suspect = suspect[0] if suspect else None
    last_suspect = suspect[-1] if suspect else None

    summary = {
        "simulationDatabase": str(simulation_path),
        "microstructureDatabase": str(micro_path),
        "filterFrom": start.isoformat(),
        "filterTo": end.isoformat() if end is not None else None,
        "observationsCompared": len(compared),
        "microstructureSnapshots": len(snapshots),
        "verdictCounts": dict(verdict_counts),
        "firstSuspectInterval": first_suspect,
        "lastSuspectInterval": last_suspect,
        "longestSuspectIntervals": longest,
        "rowsCsv": str(rows_csv),
        "intervalsCsv": str(intervals_csv),
        "limitations": [
            (
                "ACTIVE_MATCH means a retained snapshot had the expected market "
                "ID and a non-null accepted Prediction mid near the observation."
            ),
            (
                "NO_ACCEPTED_PREDICTION_BOOK is the strongest retained-snapshot "
                "indicator of rollover rejection, unverified orientation, or "
                "Prediction feed outage."
            ),
            (
                "Snapshot retention is usually longer than raw-event retention, "
                "so this audit can cover periods whose raw frames were purged."
            ),
        ],
    }
    summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
