from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from predict_bot import target_maker_direct_placement_v1 as maker

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_ARCHIVE_DB = ROOT / "data" / "public_research_archive_v1.db"


def _iso_taipei(value: int | None) -> str | None:
    if value is None:
        return None
    tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(int(value) / 1000.0, tz=tz).isoformat()


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _compatible_latest_snapshot_per_second(
    db: sqlite3.Connection, market_id: int
) -> list[dict[str, Any]]:
    columns = _table_columns(db, "wallet_taker_signal_snapshots")
    order = "sampled_at_ms"
    if "timestamp_ns" in columns:
        order += ",timestamp_ns"
    elif "id" in columns:
        order += ",id"
    rows = [
        dict(row)
        for row in db.execute(
            f"""SELECT *
                  FROM wallet_taker_signal_snapshots
                 WHERE market_id=?
                 ORDER BY {order}""",
            (int(market_id),),
        )
    ]
    latest: dict[int, dict[str, Any]] = {}
    for row in rows:
        latest[int(row["sampled_at_ms"]) // 1000] = row
    return [latest[key] for key in sorted(latest)]


def _source_stats(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    try:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wallet_taker_signal_snapshots'"
        ).fetchone()
        if table is None:
            raise RuntimeError(f"wallet_taker_signal_snapshots table missing: {resolved}")
        row = db.execute(
            """SELECT MIN(sampled_at_ms),MAX(sampled_at_ms),COUNT(*),COUNT(DISTINCT market_id)
                 FROM wallet_taker_signal_snapshots"""
        ).fetchone()
        first = int(row[0]) if row and row[0] is not None else None
        last = int(row[1]) if row and row[1] is not None else None
        return {
            "path": str(resolved),
            "firstSampleMs": first,
            "lastSampleMs": last,
            "firstSampleTaipei": _iso_taipei(first),
            "lastSampleTaipei": _iso_taipei(last),
            "rows": int(row[2]) if row else 0,
            "markets": int(row[3]) if row else 0,
            "hasTimestampNs": "timestamp_ns"
            in {str(info[1]) for info in db.execute("PRAGMA table_info(wallet_taker_signal_snapshots)")},
        }
    finally:
        db.close()


def _unique_paths(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.expanduser().resolve()
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def _merge_csv(
    paths: list[Path],
    output_path: Path,
    *,
    key_columns: tuple[str, ...],
    sort_columns: tuple[str, ...],
) -> int:
    columns: list[str] | None = None
    merged: dict[tuple[str, ...], dict[str, str]] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if columns is None:
                columns = list(reader.fieldnames or [])
            for row in reader:
                key = tuple(str(row.get(column, "")) for column in key_columns)
                merged[key] = dict(row)

    if columns is None:
        raise RuntimeError("no temporary Maker dataset was produced")

    def sort_key(row: dict[str, str]) -> tuple[Any, ...]:
        result: list[Any] = []
        for column in sort_columns:
            value = row.get(column, "")
            try:
                result.append(int(float(value)))
            except (TypeError, ValueError):
                result.append(str(value))
        return tuple(result)

    rows = sorted(merged.values(), key=sort_key)
    resolved = output_path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(resolved)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build Target Maker direct placement datasets from multiple compatible public "
            "signal archives and merge them by decision/parent key. This is intended for "
            "special-regime stress tests spanning the legacy 8777 archive and the newer "
            "target-blind PUBLIC_RESEARCH_ARCHIVE_V1."
        )
    )
    parser.add_argument("--maker-db", type=Path, default=maker.DEFAULT_MAKER_DB)
    parser.add_argument(
        "--signal-db",
        action="append",
        type=Path,
        default=None,
        help="Repeat to merge multiple signal DBs; later DBs win overlapping rows.",
    )
    parser.add_argument("--hazard-output", type=Path, default=maker.DEFAULT_HAZARD_OUTPUT)
    parser.add_argument("--behavior-output", type=Path, default=maker.DEFAULT_BEHAVIOR_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=maker.DEFAULT_META_OUTPUT)
    parser.add_argument("--max-signal-age-ms", type=int, default=maker.MAX_SIGNAL_AGE_MS)
    parser.add_argument("--min-parent-confidence", type=float, default=maker.MIN_PARENT_CONFIDENCE)
    parser.add_argument("--min-placement-coverage", type=float, default=maker.MIN_PLACEMENT_COVERAGE)
    parser.add_argument("--min-fill-coverage", type=float, default=maker.MIN_FILL_COVERAGE)
    args = parser.parse_args()

    signal_paths = list(args.signal_db or [])
    if not signal_paths:
        if maker.DEFAULT_SIGNAL_DB.exists():
            signal_paths.append(maker.DEFAULT_SIGNAL_DB)
        if DEFAULT_PUBLIC_ARCHIVE_DB.exists():
            signal_paths.append(DEFAULT_PUBLIC_ARCHIVE_DB)
    signal_paths = _unique_paths(signal_paths)
    if not signal_paths:
        raise SystemExit(
            "No signal DB found. Expected data/wallet_taker_signals.db and/or "
            "data/public_research_archive_v1.db."
        )

    stats = [_source_stats(path) for path in signal_paths]
    active = [
        (path, stat)
        for path, stat in zip(signal_paths, stats)
        if int(stat["rows"]) > 0
    ]
    if not active:
        raise SystemExit("All requested Maker signal DBs are empty.")

    # Existing Maker builder sorts legacy snapshots by timestamp_ns. The target-blind
    # public archive has sampled_at_ms + id instead. Patch only this offline process so
    # both schemas are accepted without altering the running collector DB.
    maker._latest_snapshot_per_second = _compatible_latest_snapshot_per_second

    source_reports: list[dict[str, Any]] = []
    hazard_parts: list[Path] = []
    behavior_parts: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="target-maker-special-regime-") as temp_dir:
        temp_root = Path(temp_dir)
        for index, (signal_path, stat) in enumerate(active):
            hazard_part = temp_root / f"hazard_{index}.csv"
            behavior_part = temp_root / f"behavior_{index}.csv"
            meta_part = temp_root / f"meta_{index}.json"
            report = maker.build_datasets(
                maker_db_path=args.maker_db,
                signal_db_path=signal_path,
                hazard_output_path=hazard_part,
                behavior_output_path=behavior_part,
                meta_output_path=meta_part,
                max_signal_age_ms=max(250, int(args.max_signal_age_ms)),
                min_parent_confidence=max(0.0, min(1.0, float(args.min_parent_confidence))),
                min_placement_coverage=max(0.0, min(1.0, float(args.min_placement_coverage))),
                min_fill_coverage=max(0.0, min(1.0, float(args.min_fill_coverage))),
            )
            source_reports.append({"signalSource": stat, "builderReport": report})
            hazard_parts.append(hazard_part)
            behavior_parts.append(behavior_part)

        hazard_rows = _merge_csv(
            hazard_parts,
            args.hazard_output,
            key_columns=("market_id", "decision_sampled_at_ms"),
            sort_columns=("decision_sampled_at_ms", "market_id"),
        )
        behavior_rows = _merge_csv(
            behavior_parts,
            args.behavior_output,
            key_columns=("parent_id",),
            sort_columns=("placement_first_ms", "market_id"),
        )

    hazard_start: int | None = None
    hazard_end: int | None = None
    with args.hazard_output.expanduser().resolve().open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            value = int(float(row["decision_sampled_at_ms"]))
            hazard_start = value if hazard_start is None else min(hazard_start, value)
            hazard_end = value if hazard_end is None else max(hazard_end, value)

    meta = {
        "datasetVersion": maker.DATASET_VERSION,
        "builderVersion": "TARGET_MAKER_DIRECT_PLACEMENT_V1_MULTI_SIGNAL_SOURCE_SPECIAL_REGIME",
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "signalSources": stats,
        "sourceReports": source_reports,
        "hazardRows": hazard_rows,
        "behaviorRows": behavior_rows,
        "hazardCoverageStartMs": hazard_start,
        "hazardCoverageStartTaipei": _iso_taipei(hazard_start),
        "hazardCoverageEndMs": hazard_end,
        "hazardCoverageEndTaipei": _iso_taipei(hazard_end),
        "mergePolicy": (
            "Hazard rows deduplicate on (market_id, decision_sampled_at_ms); behavior rows "
            "deduplicate on parent_id. Later signal sources win overlaps."
        ),
        "schemaCompatibility": (
            "Offline builder accepts either timestamp_ns or id as the secondary ordering "
            "column after sampled_at_ms."
        ),
    }
    resolved_meta = args.meta_output.expanduser().resolve()
    resolved_meta.parent.mkdir(parents=True, exist_ok=True)
    temp_meta = resolved_meta.with_suffix(resolved_meta.suffix + ".tmp")
    temp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_meta.replace(resolved_meta)
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
