from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from predict_bot import target_taker_direct_eligibility_special_regime_v1 as core
from predict_bot.target_maker_ebm_dataset import DEFAULT_SIGNAL_DB
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB, TAKER_COHORT

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_ARCHIVE_DB = ROOT / "data" / "public_research_archive_v1.db"


def _iso_taipei(value: int | None) -> str | None:
    if value is None:
        return None
    tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(int(value) / 1000.0, tz=tz).isoformat()


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _source_stats(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute(
        """SELECT MIN(sampled_at_ms),MAX(sampled_at_ms),COUNT(*),COUNT(DISTINCT market_id)
             FROM wallet_taker_signal_snapshots"""
    ).fetchone()
    first = int(row[0]) if row and row[0] is not None else None
    last = int(row[1]) if row and row[1] is not None else None
    return {
        "firstSampleMs": first,
        "lastSampleMs": last,
        "firstSampleTaipei": _iso_taipei(first),
        "lastSampleTaipei": _iso_taipei(last),
        "rows": int(row[2]) if row else 0,
        "markets": int(row[3]) if row else 0,
    }


def _latest_snapshot_per_second(
    db: sqlite3.Connection,
    market_id: int,
    *,
    coverage_start_ms: int,
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
                 WHERE market_id=? AND sampled_at_ms>=?
                 ORDER BY {order}""",
            (int(market_id), int(coverage_start_ms)),
        )
    ]
    latest: dict[int, dict[str, Any]] = {}
    for row in rows:
        latest[int(row["sampled_at_ms"]) // 1000] = row
    return [latest[key] for key in sorted(latest)]


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


def build_multisource(
    *,
    shadow_db_path: Path,
    signal_db_paths: list[Path],
    output_path: Path,
    meta_output_path: Path,
) -> dict[str, Any]:
    shadow = core._connect_readonly(shadow_db_path)
    sources: list[tuple[Path, sqlite3.Connection, dict[str, Any], int]] = []
    try:
        for table in ("wallet_target_taker_mirror_meta", "wallet_target_taker_mirror_parents"):
            if not core._has_table(shadow, table):
                raise RuntimeError(f"required target Taker mirror table missing: {table}")

        mirror_deployed, excluded_market_id = core._target_mirror_coverage(shadow)

        for path in _unique_paths(signal_db_paths):
            db = core._connect_readonly(path)
            if not core._has_table(db, "wallet_taker_signal_snapshots"):
                db.close()
                raise RuntimeError(f"wallet_taker_signal_snapshots table is missing: {path}")
            stats = _source_stats(db)
            deployed = core._signal_deployed_at_ms(db)
            actual_first = stats["firstSampleMs"]
            effective_start = max(
                int(mirror_deployed),
                int(deployed) if deployed > 0 else int(actual_first or mirror_deployed),
            )
            stats.update(
                {
                    "path": str(path),
                    "declaredDeployedAtMs": int(deployed),
                    "effectiveCoverageStartMs": int(effective_start),
                    "effectiveCoverageStartTaipei": _iso_taipei(effective_start),
                    "hasTimestampNs": "timestamp_ns"
                    in _table_columns(db, "wallet_taker_signal_snapshots"),
                }
            )
            sources.append((path, db, stats, effective_start))

        nonempty = [item for item in sources if int(item[2]["rows"]) > 0]
        if not nonempty:
            raise RuntimeError("no signal rows found in any requested signal DB")

        coverage_start_ms = min(int(item[3]) for item in nonempty)

        event_buckets_by_market: dict[int, list[int]] = {}
        parent_count = 0
        parent_min_ms: int | None = None
        parent_max_ms: int | None = None
        for row in shadow.execute(
            """SELECT market_id,target_event_ms
                 FROM wallet_target_taker_mirror_parents
                WHERE cohort=? AND target_event_ms>=?
                ORDER BY target_event_ms""",
            (TAKER_COHORT, int(coverage_start_ms)),
        ):
            market_id = int(row["market_id"])
            if excluded_market_id is not None and market_id == excluded_market_id:
                continue
            event_ms = int(row["target_event_ms"])
            parent_min_ms = event_ms if parent_min_ms is None else min(parent_min_ms, event_ms)
            parent_max_ms = event_ms if parent_max_ms is None else max(parent_max_ms, event_ms)
            bucket = (event_ms // 1000) * 1000
            event_buckets_by_market.setdefault(market_id, []).append(bucket)
            parent_count += 1
        for market_id, values in list(event_buckets_by_market.items()):
            event_buckets_by_market[market_id] = sorted(set(values))

        snapshots_by_market: dict[int, dict[int, dict[str, Any]]] = {}
        for _path, db, stats, source_start in sources:
            if int(stats["rows"]) <= 0:
                continue
            market_rows = db.execute(
                """SELECT DISTINCT market_id
                     FROM wallet_taker_signal_snapshots
                    WHERE sampled_at_ms>=? AND market_id IS NOT NULL
                    ORDER BY market_id""",
                (int(source_start),),
            )
            for raw in market_rows:
                market_id = int(raw[0])
                if excluded_market_id is not None and market_id == excluded_market_id:
                    continue
                bucket_map = snapshots_by_market.setdefault(market_id, {})
                for snapshot in _latest_snapshot_per_second(
                    db, market_id, coverage_start_ms=source_start
                ):
                    # Later signal sources win overlap. Runner passes the target-blind
                    # public archive after the legacy 8777 archive.
                    bucket_map[int(snapshot["sampled_at_ms"]) // 1000] = snapshot

        output_rows: list[dict[str, Any]] = []
        zero_taker_markets = 0
        for market_id in sorted(snapshots_by_market):
            events = event_buckets_by_market.get(market_id, [])
            if not events:
                zero_taker_markets += 1
            snapshots = [
                snapshots_by_market[market_id][key]
                for key in sorted(snapshots_by_market[market_id])
            ]
            previous_nonzero_sign = 0
            last_cross_ms: int | None = None
            for index, snapshot in enumerate(snapshots):
                sampled = int(snapshot["sampled_at_ms"])
                current_sign = core._sign(core._number(snapshot.get("spot_minus_strike_bps")))
                if current_sign:
                    if previous_nonzero_sign and current_sign != previous_nonzero_sign:
                        last_cross_ms = sampled
                    previous_nonzero_sign = current_sign

                decision_bucket = (sampled // 1000) * 1000
                event_index = bisect.bisect_right(events, decision_bucket)
                next_bucket = events[event_index] if event_index < len(events) else None
                delta = next_bucket - decision_bucket if next_bucket is not None else None

                row: dict[str, Any] = {
                    "dataset_version": core.DATASET_VERSION,
                    "market_id": market_id,
                    "decision_sampled_at_ms": sampled,
                    "decision_bucket_start_ms": decision_bucket,
                    "next_target_taker_event_bucket_ms": next_bucket,
                    "ms_to_next_target_taker_bucket": delta,
                }
                row.update(core._public_features(snapshot, event_bucket_start_ms=sampled))
                row["signal_age_ms"] = 0
                row.update(
                    core._special_features(
                        snapshots, index, last_cross_ms=last_cross_ms
                    )
                )
                for horizon in (1, 2, 5):
                    row[f"label_next_target_taker_any_{horizon}s"] = int(
                        delta is not None and 0 < delta <= horizon * 1000
                    )
                output_rows.append(row)

        core._write_csv(output_path, output_rows)
        decision_start = (
            min(int(row["decision_sampled_at_ms"]) for row in output_rows)
            if output_rows
            else None
        )
        decision_end = (
            max(int(row["decision_sampled_at_ms"]) for row in output_rows)
            if output_rows
            else None
        )

        meta = {
            "datasetVersion": core.DATASET_VERSION,
            "builderVersion": "TARGET_TAKER_DIRECT_ELIGIBILITY_SPECIAL_REGIME_V1_MULTI_SIGNAL_SOURCE",
            "paperResearchOnly": True,
            "automaticStrategyPromotion": False,
            "causalClaim": False,
            "coverageStartMs": coverage_start_ms,
            "coverageStartTaipei": _iso_taipei(coverage_start_ms),
            "coverageEndMs": decision_end,
            "coverageEndTaipei": _iso_taipei(decision_end),
            "decisionStartMs": decision_start,
            "decisionStartTaipei": _iso_taipei(decision_start),
            "decisionEndMs": decision_end,
            "decisionEndTaipei": _iso_taipei(decision_end),
            "mirrorDeployedAtMs": mirror_deployed,
            "mirrorDeployedTaipei": _iso_taipei(mirror_deployed),
            "excludedDeploymentMarketId": excluded_market_id,
            "signalSources": [item[2] for item in sources],
            "rows": len(output_rows),
            "markets": len(snapshots_by_market),
            "targetTakerParents": parent_count,
            "targetParentMinEventMs": parent_min_ms,
            "targetParentMinEventTaipei": _iso_taipei(parent_min_ms),
            "targetParentMaxEventMs": parent_max_ms,
            "targetParentMaxEventTaipei": _iso_taipei(parent_max_ms),
            "zeroTakerMarkets": zero_taker_markets,
            "positiveRates": {
                f"next{horizon}s": (
                    sum(
                        int(row[f"label_next_target_taker_any_{horizon}s"])
                        for row in output_rows
                    )
                    / len(output_rows)
                    if output_rows
                    else None
                )
                for horizon in (1, 2, 5)
            },
            "sampling": (
                "Latest snapshot per wall-clock second after merging all requested "
                "wallet_taker_signal_snapshots sources. Later sources win overlapping "
                "market-second buckets."
            ),
            "timestampBoundary": (
                "Target Taker event timestamps are second-quantized. Same-second target "
                "events are excluded from positive labels."
            ),
            "featureFamilies": {
                "frozen16": core.FROZEN16_FEATURES,
                "specialRegime": core.SPECIAL_REGIME_FEATURES,
            },
        }
        resolved_meta = meta_output_path.expanduser().resolve()
        resolved_meta.parent.mkdir(parents=True, exist_ok=True)
        temp_meta = resolved_meta.with_suffix(resolved_meta.suffix + ".tmp")
        temp_meta.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp_meta.replace(resolved_meta)
        return meta
    finally:
        shadow.close()
        for _path, db, _stats, _start in sources:
            db.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build TARGET_TAKER_DIRECT_ELIGIBILITY_SPECIAL_REGIME_V1 from multiple "
            "compatible signal archives. By default it merges the legacy 8777 archive "
            "with PUBLIC_RESEARCH_ARCHIVE_V1 when both exist."
        )
    )
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument(
        "--signal-db",
        action="append",
        type=Path,
        default=None,
        help="Repeat to merge multiple signal DBs; later DBs win overlapping seconds.",
    )
    parser.add_argument("--output", type=Path, default=core.DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=core.DEFAULT_META_OUTPUT)
    args = parser.parse_args()

    signal_paths = list(args.signal_db or [])
    if not signal_paths:
        if DEFAULT_SIGNAL_DB.exists():
            signal_paths.append(DEFAULT_SIGNAL_DB)
        if DEFAULT_PUBLIC_ARCHIVE_DB.exists():
            signal_paths.append(DEFAULT_PUBLIC_ARCHIVE_DB)
    if not signal_paths:
        raise SystemExit(
            "No signal DB found. Expected data/wallet_taker_signals.db and/or "
            "data/public_research_archive_v1.db."
        )

    report = build_multisource(
        shadow_db_path=args.shadow_db,
        signal_db_paths=signal_paths,
        output_path=args.output,
        meta_output_path=args.meta_output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
