from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from build_target_taker_direct_eligibility_special_regime_multisource_v1 import (
    DEFAULT_PUBLIC_ARCHIVE_DB,
    build_multisource,
)
from predict_bot import target_taker_direct_eligibility_special_regime_v1 as core
from predict_bot.target_maker_ebm_dataset import DEFAULT_SIGNAL_DB
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB, TAKER_COHORT

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OFFICIAL_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
ADAPTER_VERSION = "TARGET_TAKER_OFFICIAL_PARENT_LABEL_ADAPTER_V1"


def _epoch_ms(value: str | None) -> int | None:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        pass
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"time must include timezone offset or be epoch ms, got {value!r}")
    return int(dt.timestamp() * 1000)


def _iso_taipei(value: int | None) -> str | None:
    if value is None:
        return None
    tz = timezone(timedelta(hours=8))
    return datetime.fromtimestamp(int(value) / 1000.0, tz=tz).isoformat()


def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def _row_key(row: dict[str, Any]) -> tuple[int, str, str]:
    market_id = int(row["market_id"])
    side = str(row.get("side") or "").upper()
    order_hash = str(row.get("order_hash") or "").strip().lower()
    parent_id = str(row.get("parent_id") or "").strip()
    identity = f"order:{order_hash}" if order_hash else f"parent:{parent_id}"
    return market_id, identity, side


def _source_stats(path: Path, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    times = [int(row["target_event_ms"]) for row in rows]
    return {
        "source": name,
        "path": str(path.expanduser().resolve()),
        "parents": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "minEventMs": min(times) if times else None,
        "minEventTaipei": _iso_taipei(min(times)) if times else None,
        "maxEventMs": max(times) if times else None,
        "maxEventTaipei": _iso_taipei(max(times)) if times else None,
    }


def _read_legacy(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], int, int | None]:
    db = _connect_ro(path)
    try:
        meta = db.execute(
            """SELECT deployed_at_ms,excluded_market_id
                 FROM wallet_target_taker_mirror_meta
                WHERE cohort=? LIMIT 1""",
            (TAKER_COHORT,),
        ).fetchone()
        if meta is None:
            raise RuntimeError(f"missing legacy Taker mirror meta for {TAKER_COHORT}")
        deployed = int(meta["deployed_at_ms"])
        excluded = int(meta["excluded_market_id"]) if meta["excluded_market_id"] is not None else None

        table = "wallet_target_taker_mirror_parents"
        columns = _columns(db, table)
        order_expr = "order_hash" if "order_hash" in columns else "NULL AS order_hash"
        rows = [
            {
                "parent_id": str(row["parent_id"]),
                "market_id": int(row["market_id"]),
                "target_event_ms": int(row["target_event_ms"]),
                "side": str(row["side"] or "").upper(),
                "order_hash": str(row["order_hash"] or "") if row["order_hash"] is not None else "",
                "label_source": "LEGACY_TARGET_TAKER_MIRROR",
            }
            for row in db.execute(
                f"""SELECT parent_id,market_id,target_event_ms,side,{order_expr}
                       FROM {table}
                      WHERE cohort=? AND target_event_ms IS NOT NULL
                      ORDER BY target_event_ms,parent_id""",
                (TAKER_COHORT,),
            )
        ]
        return rows, _source_stats(path, "LEGACY_TARGET_TAKER_MIRROR", rows), deployed, excluded
    finally:
        db.close()


def _official_wallet(db: sqlite3.Connection) -> str | None:
    if not _has_table(db, "target_service_meta"):
        return None
    row = db.execute("SELECT value FROM target_service_meta WHERE key='service' LIMIT 1").fetchone()
    if row is None or row[0] is None:
        return None
    try:
        payload = json.loads(str(row[0]))
    except (TypeError, ValueError):
        return None
    wallet = payload.get("targetWallet") if isinstance(payload, dict) else None
    return str(wallet).lower() if wallet else None


def _read_official(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db = _connect_ro(path)
    try:
        table = "target_parent_orders"
        if not _has_table(db, table):
            raise RuntimeError(f"official Target parent table missing: {table}")
        columns = _columns(db, table)
        required = {"parent_id", "market_id", "role", "side", "asset", "first_event_ms"}
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(f"official Target parent table missing columns: {', '.join(missing)}")

        wallet = _official_wallet(db)
        clauses = ["upper(role)='TAKER'", "upper(asset)='BTC'", "first_event_ms IS NOT NULL"]
        params: list[Any] = []
        if wallet and "wallet" in columns:
            clauses.append("lower(wallet)=?")
            params.append(wallet)
        order_expr = "order_hash" if "order_hash" in columns else "NULL AS order_hash"
        rows = [
            {
                "parent_id": str(row["parent_id"]),
                "market_id": int(row["market_id"]),
                # A parent is one entry opportunity. Use its first observed fill, not
                # last_event_ms or each leg, to avoid manufacturing fake re-entries.
                "target_event_ms": int(row["first_event_ms"]),
                "side": str(row["side"] or "").upper(),
                "order_hash": str(row["order_hash"] or "") if row["order_hash"] is not None else "",
                "label_source": "TARGET_WALLET_OFFICIAL_V1",
            }
            for row in db.execute(
                f"""SELECT parent_id,market_id,first_event_ms,side,{order_expr}
                       FROM {table}
                      WHERE {' AND '.join(clauses)}
                      ORDER BY first_event_ms,parent_id""",
                tuple(params),
            )
        ]
        stats = _source_stats(path, "TARGET_WALLET_OFFICIAL_V1", rows)
        stats.update(
            {
                "targetWallet": wallet,
                "assetFilter": "BTC",
                "roleFilter": "TAKER",
                "eventTimeField": "first_event_ms",
            }
        )
        return rows, stats
    finally:
        db.close()


def _merge_labels(
    legacy_path: Path,
    official_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int | None]:
    legacy, legacy_stats, deployed, excluded = _read_legacy(legacy_path)
    merged = {_row_key(row): row for row in legacy}
    stats = [legacy_stats]

    resolved_official = official_path.expanduser().resolve()
    if resolved_official.exists():
        official, official_stats = _read_official(resolved_official)
        for row in official:
            # Official V1 is the newer observed Target source and wins overlap.
            merged[_row_key(row)] = row
        stats.append(official_stats)
    else:
        stats.append(
            {
                "source": "TARGET_WALLET_OFFICIAL_V1",
                "path": str(resolved_official),
                "exists": False,
                "parents": 0,
                "markets": 0,
            }
        )

    rows = sorted(
        merged.values(),
        key=lambda row: (int(row["target_event_ms"]), int(row["market_id"]), str(row["parent_id"])),
    )
    return rows, stats, deployed, excluded


def _write_adapter_db(
    path: Path,
    *,
    rows: list[dict[str, Any]],
    deployed_at_ms: int,
    excluded_market_id: int | None,
) -> None:
    db = sqlite3.connect(path)
    try:
        db.executescript(
            """
            CREATE TABLE wallet_target_taker_mirror_meta (
                cohort TEXT PRIMARY KEY,
                deployed_at_ms INTEGER NOT NULL,
                excluded_market_id INTEGER
            );
            CREATE TABLE wallet_target_taker_mirror_parents (
                cohort TEXT NOT NULL,
                parent_id TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                target_event_ms INTEGER NOT NULL,
                side TEXT,
                order_hash TEXT,
                label_source TEXT
            );
            CREATE INDEX idx_adapter_parent_time
                ON wallet_target_taker_mirror_parents(cohort,target_event_ms,market_id);
            """
        )
        db.execute(
            "INSERT INTO wallet_target_taker_mirror_meta(cohort,deployed_at_ms,excluded_market_id) VALUES(?,?,?)",
            (TAKER_COHORT, int(deployed_at_ms), excluded_market_id),
        )
        db.executemany(
            """INSERT INTO wallet_target_taker_mirror_parents
                   (cohort,parent_id,market_id,target_event_ms,side,order_hash,label_source)
               VALUES(?,?,?,?,?,?,?)""",
            [
                (
                    TAKER_COHORT,
                    row["parent_id"],
                    int(row["market_id"]),
                    int(row["target_event_ms"]),
                    row.get("side"),
                    row.get("order_hash"),
                    row.get("label_source"),
                )
                for row in rows
            ],
        )
        db.commit()
    finally:
        db.close()


def _augment_meta(
    path: Path,
    *,
    source_stats: list[dict[str, Any]],
    merged_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    meta = json.loads(resolved.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    for row in merged_rows:
        source = str(row.get("label_source") or "UNKNOWN")
        counts[source] = counts.get(source, 0) + 1
    meta.update(
        {
            "targetLabelAdapterVersion": ADAPTER_VERSION,
            "targetLabelSources": source_stats,
            "targetLabelSourceCountsMerged": counts,
            "targetLabelMergePolicy": (
                "Parent-level Target Taker labels. TARGET_WALLET_OFFICIAL_V1 BTC/TAKER "
                "first_event_ms wins exact market/order/side overlap; legacy mirror fills "
                "historical gaps. Same-second future-label exclusion is unchanged."
            ),
        }
    )
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)
    return meta


def _preflight(
    *,
    dataset_path: Path,
    merged_rows: list[dict[str, Any]],
    start_ms: int,
    end_ms: int | None,
) -> dict[str, Any]:
    special_rows = 0
    special_markets: set[int] = set()
    positives = {1: 0, 2: 0, 5: 0}
    with dataset_path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            sampled = int(row["decision_sampled_at_ms"])
            if sampled < start_ms or (end_ms is not None and sampled >= end_ms):
                continue
            special_rows += 1
            special_markets.add(int(row["market_id"]))
            for horizon in positives:
                positives[horizon] += int(row[f"label_next_target_taker_any_{horizon}s"])

    target_window = [
        row
        for row in merged_rows
        if int(row["target_event_ms"]) >= start_ms
        and (end_ms is None or int(row["target_event_ms"]) < end_ms)
    ]
    target_markets = {int(row["market_id"]) for row in target_window}
    intersection = special_markets & target_markets
    source_counts: dict[str, int] = {}
    for row in target_window:
        source = str(row.get("label_source") or "UNKNOWN")
        source_counts[source] = source_counts.get(source, 0) + 1

    report = {
        "specialStartMs": start_ms,
        "specialStartTaipei": _iso_taipei(start_ms),
        "specialEndMs": end_ms,
        "specialEndTaipei": _iso_taipei(end_ms),
        "specialRows": special_rows,
        "specialMarkets": len(special_markets),
        "targetTakerParentsInWindow": len(target_window),
        "targetTakerMarketsInWindow": len(target_markets),
        "marketIdIntersectionCount": len(intersection),
        "targetParentsBySourceInWindow": source_counts,
        "positiveCounts": {f"next{h}s": positives[h] for h in (1, 2, 5)},
        "positiveRates": {
            f"next{h}s": positives[h] / special_rows if special_rows else None for h in (1, 2, 5)
        },
    }
    print("TARGET_TAKER_OFFICIAL_LABEL_PREFLIGHT")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    failures: list[str] = []
    if special_rows <= 0:
        failures.append("no public decision rows in the requested special window")
    if not target_window:
        failures.append("no merged observed Target TAKER parents in the requested special window")
    if not intersection:
        failures.append("zero market-id overlap between public rows and observed Target TAKER parents")
    if positives[5] <= 0:
        failures.append("zero label_next_target_taker_any_5s positives")
    if failures:
        raise RuntimeError(
            "TARGET_TAKER_OFFICIAL_LABEL_PREFLIGHT FAILED: " + "; ".join(failures) + ". Refusing to train."
        )
    if positives[2] <= 0:
        print("WARNING: 2s special positives are zero; 5s preflight passed but 2s holdout is not informative.")
    print("TARGET_TAKER_OFFICIAL_LABEL_PREFLIGHT OK")
    return report


def _signal_paths(explicit: list[Path] | None) -> list[Path]:
    if explicit:
        return list(explicit)
    result: list[Path] = []
    if DEFAULT_SIGNAL_DB.exists():
        result.append(DEFAULT_SIGNAL_DB)
    if DEFAULT_PUBLIC_ARCHIVE_DB.exists():
        result.append(DEFAULT_PUBLIC_ARCHIVE_DB)
    if not result:
        raise RuntimeError(
            "No signal DB found. Expected data/wallet_taker_signals.db and/or data/public_research_archive_v1.db."
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the special-regime Target Taker eligibility dataset using a parent-level "
            "label adapter that merges legacy mirror history with TARGET_WALLET_OFFICIAL_V1."
        )
    )
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--official-target-db", type=Path, default=DEFAULT_OFFICIAL_TARGET_DB)
    parser.add_argument("--signal-db", action="append", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=core.DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=core.DEFAULT_META_OUTPUT)
    parser.add_argument("--special-start", required=True)
    parser.add_argument("--special-end", default=None)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    start_ms = _epoch_ms(args.special_start)
    end_ms = _epoch_ms(args.special_end)
    if start_ms is None:
        raise SystemExit("--special-start is required")
    if end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--special-end must be after --special-start")

    merged_rows, source_stats, deployed, excluded = _merge_labels(
        args.shadow_db, args.official_target_db
    )
    print(ADAPTER_VERSION)
    print("Target label sources:")
    for source in source_stats:
        print(
            f"  {source.get('source')}: parents={source.get('parents')} "
            f"markets={source.get('markets')} max={source.get('maxEventTaipei')} "
            f"path={source.get('path')}"
        )
    print(f"Merged parent labels: {len(merged_rows)}")

    if args.preflight_only:
        _preflight(
            dataset_path=args.output,
            merged_rows=merged_rows,
            start_ms=int(start_ms),
            end_ms=end_ms,
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="target_taker_official_adapter_") as temp_dir:
        adapter_db = Path(temp_dir) / "target_taker_label_adapter.db"
        _write_adapter_db(
            adapter_db,
            rows=merged_rows,
            deployed_at_ms=deployed,
            excluded_market_id=excluded,
        )
        report = build_multisource(
            shadow_db_path=adapter_db,
            signal_db_paths=_signal_paths(args.signal_db),
            output_path=args.output,
            meta_output_path=args.meta_output,
        )

    meta = _augment_meta(
        args.meta_output,
        source_stats=source_stats,
        merged_rows=merged_rows,
    )
    print(
        json.dumps(
            {
                "datasetRows": report.get("rows"),
                "datasetMarkets": report.get("markets"),
                "targetTakerParents": report.get("targetTakerParents"),
                "targetParentMaxEventTaipei": report.get("targetParentMaxEventTaipei"),
                "targetLabelAdapterVersion": meta.get("targetLabelAdapterVersion"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    _preflight(
        dataset_path=args.output,
        merged_rows=merged_rows,
        start_ms=int(start_ms),
        end_ms=end_ms,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
