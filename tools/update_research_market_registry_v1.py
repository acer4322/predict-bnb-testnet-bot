from __future__ import annotations

"""Incremental BTC5M research market registry.

Purpose:
- avoid repeated whole-table COUNT(DISTINCT market_id) scans on multi-GB stores;
- centralize source-availability / execution-quality cohort selection metadata;
- keep outcome/winner fields out of the registry so it is not a model feature source.

The registry is a research index only. Canonical truth remains the source SQLite
stores and Execution Tape archive.
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
VERSION = "BTC5M_RESEARCH_MARKET_REGISTRY_V1"
DEFAULT_MAKER_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_PUBLIC_DB = ROOT / "data" / "public_source_snapshot_archive_v2.db"
DEFAULT_TAPE_DIR = ROOT / "data" / "execution_tape_v1" / "markets"
DEFAULT_REGISTRY = ROOT / "data" / "research" / "market_capsule_v1" / "research_market_registry_v1.db"
DEFAULT_PARQUET = ROOT / "data" / "research" / "market_capsule_v1" / "research_market_registry_v1.parquet"


def ro(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=15.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    c.execute("PRAGMA busy_timeout=15000")
    return c


def chunks(xs: list[int], n: int = 400) -> Iterable[list[int]]:
    for i in range(0, len(xs), n):
        yield xs[i:i+n]


def stats_by_market(con: sqlite3.Connection, sql_prefix: str, ids: list[int], extra_args: list[Any] | None = None) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    extra_args = list(extra_args or [])
    for part in chunks(ids):
        ph = ",".join("?" for _ in part)
        for r in con.execute(sql_prefix.format(ph=ph), [*extra_args, *part]):
            out[int(r["market_id"])] = dict(r)
    return out


def ensure_schema(con: sqlite3.Connection) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS market_registry_v1(
            market_id INTEGER PRIMARY KEY,
            asset TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            title TEXT,
            window_start_ms INTEGER,
            window_end_ms INTEGER,
            market_status TEXT,
            quality_status TEXT,
            eligible_execution_training INTEGER NOT NULL DEFAULT 0,
            eligible_legacy_replay INTEGER NOT NULL DEFAULT 0,
            first_source_ms INTEGER,
            last_source_ms INTEGER,
            l2_rows INTEGER NOT NULL DEFAULT 0,
            match_rows INTEGER NOT NULL DEFAULT 0,
            execution_archive_bytes INTEGER NOT NULL DEFAULT 0,
            source_quality_assessed_at_ms INTEGER,
            has_execution_tape INTEGER NOT NULL DEFAULT 0,
            execution_tape_bytes INTEGER NOT NULL DEFAULT 0,
            execution_tape_mtime_ns INTEGER,
            has_target_actions INTEGER NOT NULL DEFAULT 0,
            target_action_count INTEGER NOT NULL DEFAULT 0,
            target_first_action_ms INTEGER,
            target_last_action_ms INTEGER,
            has_target_parents INTEGER NOT NULL DEFAULT 0,
            target_parent_count INTEGER NOT NULL DEFAULT 0,
            target_first_parent_ms INTEGER,
            target_last_parent_ms INTEGER,
            has_public_snapshots INTEGER NOT NULL DEFAULT 0,
            public_snapshot_count INTEGER NOT NULL DEFAULT 0,
            public_first_sample_ms INTEGER,
            public_last_sample_ms INTEGER,
            ready_market_capsule INTEGER NOT NULL DEFAULT 0,
            refreshed_at_ms INTEGER NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_registry_ready_end ON market_registry_v1(ready_market_capsule,window_end_ms)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_registry_quality_end ON market_registry_v1(quality_status,window_end_ms)")
    con.execute("CREATE TABLE IF NOT EXISTS registry_meta_v1(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    con.commit()


def export_parquet(reg: sqlite3.Connection, parquet: Path) -> None:
    rows = [dict(r) for r in reg.execute("SELECT * FROM market_registry_v1 ORDER BY market_id")]
    tmp = parquet.with_suffix(".jsonl.tmp")
    parquet.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":"), ensure_ascii=False))
            f.write("\n")
    import duckdb
    c = duckdb.connect(database=":memory:")
    try:
        src = tmp.resolve().as_posix().replace("'", "''")
        dst = parquet.resolve().as_posix().replace("'", "''")
        c.execute(f"COPY (SELECT * FROM read_json_auto('{src}', format='newline_delimited')) TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        c.close()
        tmp.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    ap.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    ap.add_argument("--public-db", type=Path, default=DEFAULT_PUBLIC_DB)
    ap.add_argument("--tape-dir", type=Path, default=DEFAULT_TAPE_DIR)
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    ap.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--timeframe", default="5M")
    ap.add_argument("--full", action="store_true", help="refresh every execution-quality market")
    ap.add_argument("--recent-hours", type=float, default=12.0, help="always refresh recently ended markets")
    ap.add_argument("--market-ids", help="comma-separated explicit IDs; forces refresh of only these IDs")
    ap.add_argument("--limit", type=int, help="smoke-test cap after candidate resolution")
    ns = ap.parse_args()

    maker_path = ns.maker_db if ns.maker_db.is_absolute() else ROOT / ns.maker_db
    target_path = ns.target_db if ns.target_db.is_absolute() else ROOT / ns.target_db
    public_path = ns.public_db if ns.public_db.is_absolute() else ROOT / ns.public_db
    tape_dir = ns.tape_dir if ns.tape_dir.is_absolute() else ROOT / ns.tape_dir
    registry = ns.registry if ns.registry.is_absolute() else ROOT / ns.registry
    parquet = ns.parquet if ns.parquet.is_absolute() else ROOT / ns.parquet
    registry.parent.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    now_ms = int(time.time() * 1000)
    reg = sqlite3.connect(registry)
    reg.row_factory = sqlite3.Row
    ensure_schema(reg)
    maker = ro(maker_path)
    target = ro(target_path)
    public = ro(public_path)
    try:
        quality_rows = [dict(r) for r in maker.execute("""
            SELECT market_id,quality_status,eligible_execution_training,eligible_legacy_replay,
                   window_start_ms,window_end_ms,first_source_ms,last_source_ms,l2_rows,match_rows,
                   archive_bytes,assessed_at_ms
              FROM maker_execution_market_quality_v1 ORDER BY market_id
        """)]
        quality = {int(r["market_id"]): r for r in quality_rows}
        if ns.market_ids:
            ids = [int(x.strip()) for x in ns.market_ids.split(",") if x.strip()]
            missing = [x for x in ids if x not in quality]
            if missing:
                raise RuntimeError(f"market IDs missing execution quality rows: {missing}")
        elif ns.full:
            ids = sorted(quality)
        else:
            existing = {int(r["market_id"]): int(r["source_quality_assessed_at_ms"] or 0)
                        for r in reg.execute("SELECT market_id,source_quality_assessed_at_ms FROM market_registry_v1")}
            recent_cut = now_ms - int(max(0.0, ns.recent_hours) * 3600_000)
            ids = []
            for mid, q in quality.items():
                assessed = int(q.get("assessed_at_ms") or 0)
                end_ms = int(q.get("window_end_ms") or 0)
                if mid not in existing or assessed > existing[mid] or end_ms >= recent_cut:
                    ids.append(mid)
            ids.sort()
        if ns.limit is not None:
            ids = ids[-max(1, int(ns.limit)):]

        meta = stats_by_market(
            target,
            "SELECT market_id,asset,title,window_end_ms,status FROM target_markets WHERE market_id IN ({ph})",
            ids,
        ) if ids else {}
        actions = stats_by_market(
            target,
            "SELECT market_id,COUNT(*) target_action_count,MIN(event_ms) target_first_action_ms,MAX(event_ms) target_last_action_ms FROM wallet_shadow_target_events WHERE asset=? AND market_id IN ({ph}) GROUP BY market_id",
            ids,
            [str(ns.asset).upper()],
        ) if ids else {}
        parents = stats_by_market(
            target,
            "SELECT market_id,COUNT(*) target_parent_count,MIN(first_event_ms) target_first_parent_ms,MAX(last_event_ms) target_last_parent_ms FROM target_parent_orders WHERE asset=? AND market_id IN ({ph}) GROUP BY market_id",
            ids,
            [str(ns.asset).upper()],
        ) if ids else {}
        pubs = stats_by_market(
            public,
            "SELECT market_id,COUNT(*) public_snapshot_count,MIN(sampled_at_ms) public_first_sample_ms,MAX(sampled_at_ms) public_last_sample_ms FROM public_source_snapshots_v2 WHERE market_id IN ({ph}) GROUP BY market_id",
            ids,
        ) if ids else {}

        upsert = """
        INSERT INTO market_registry_v1(
            market_id,asset,timeframe,title,window_start_ms,window_end_ms,market_status,
            quality_status,eligible_execution_training,eligible_legacy_replay,first_source_ms,last_source_ms,
            l2_rows,match_rows,execution_archive_bytes,source_quality_assessed_at_ms,
            has_execution_tape,execution_tape_bytes,execution_tape_mtime_ns,
            has_target_actions,target_action_count,target_first_action_ms,target_last_action_ms,
            has_target_parents,target_parent_count,target_first_parent_ms,target_last_parent_ms,
            has_public_snapshots,public_snapshot_count,public_first_sample_ms,public_last_sample_ms,
            ready_market_capsule,refreshed_at_ms
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(market_id) DO UPDATE SET
            asset=excluded.asset,timeframe=excluded.timeframe,title=excluded.title,
            window_start_ms=excluded.window_start_ms,window_end_ms=excluded.window_end_ms,market_status=excluded.market_status,
            quality_status=excluded.quality_status,eligible_execution_training=excluded.eligible_execution_training,
            eligible_legacy_replay=excluded.eligible_legacy_replay,first_source_ms=excluded.first_source_ms,
            last_source_ms=excluded.last_source_ms,l2_rows=excluded.l2_rows,match_rows=excluded.match_rows,
            execution_archive_bytes=excluded.execution_archive_bytes,source_quality_assessed_at_ms=excluded.source_quality_assessed_at_ms,
            has_execution_tape=excluded.has_execution_tape,execution_tape_bytes=excluded.execution_tape_bytes,
            execution_tape_mtime_ns=excluded.execution_tape_mtime_ns,has_target_actions=excluded.has_target_actions,
            target_action_count=excluded.target_action_count,target_first_action_ms=excluded.target_first_action_ms,
            target_last_action_ms=excluded.target_last_action_ms,has_target_parents=excluded.has_target_parents,
            target_parent_count=excluded.target_parent_count,target_first_parent_ms=excluded.target_first_parent_ms,
            target_last_parent_ms=excluded.target_last_parent_ms,has_public_snapshots=excluded.has_public_snapshots,
            public_snapshot_count=excluded.public_snapshot_count,public_first_sample_ms=excluded.public_first_sample_ms,
            public_last_sample_ms=excluded.public_last_sample_ms,ready_market_capsule=excluded.ready_market_capsule,
            refreshed_at_ms=excluded.refreshed_at_ms
        """
        written = 0
        for mid in ids:
            q = quality[mid]
            m = meta.get(mid, {})
            a = actions.get(mid, {})
            p = parents.get(mid, {})
            u = pubs.get(mid, {})
            tape = tape_dir / f"{mid}.json.xz"
            has_tape = tape.exists()
            ts = tape.stat() if has_tape else None
            has_actions = int(a.get("target_action_count") or 0) > 0
            has_parents = int(p.get("target_parent_count") or 0) > 0
            has_public = int(u.get("public_snapshot_count") or 0) > 0
            ready = bool(int(q.get("eligible_execution_training") or 0) and has_tape and has_actions and has_public)
            reg.execute(upsert, (
                mid, str(m.get("asset") or ns.asset).upper(), str(ns.timeframe).upper(), m.get("title"),
                q.get("window_start_ms"), q.get("window_end_ms") if q.get("window_end_ms") is not None else m.get("window_end_ms"),
                m.get("status"), q.get("quality_status"), int(q.get("eligible_execution_training") or 0),
                int(q.get("eligible_legacy_replay") or 0), q.get("first_source_ms"), q.get("last_source_ms"),
                int(q.get("l2_rows") or 0), int(q.get("match_rows") or 0), int(q.get("archive_bytes") or 0),
                q.get("assessed_at_ms"), int(has_tape), int(ts.st_size if ts else 0), int(ts.st_mtime_ns if ts else 0) if ts else None,
                int(has_actions), int(a.get("target_action_count") or 0), a.get("target_first_action_ms"), a.get("target_last_action_ms"),
                int(has_parents), int(p.get("target_parent_count") or 0), p.get("target_first_parent_ms"), p.get("target_last_parent_ms"),
                int(has_public), int(u.get("public_snapshot_count") or 0), u.get("public_first_sample_ms"), u.get("public_last_sample_ms"),
                int(ready), now_ms,
            ))
            written += 1
        reg.execute("INSERT OR REPLACE INTO registry_meta_v1(key,value) VALUES('version',?)", (VERSION,))
        reg.execute("INSERT OR REPLACE INTO registry_meta_v1(key,value) VALUES('updated_at_ms',?)", (str(now_ms),))
        reg.execute("INSERT OR REPLACE INTO registry_meta_v1(key,value) VALUES('asset',?)", (str(ns.asset).upper(),))
        reg.execute("INSERT OR REPLACE INTO registry_meta_v1(key,value) VALUES('timeframe',?)", (str(ns.timeframe).upper(),))
        reg.commit()
        export_parquet(reg, parquet)

        total = int(reg.execute("SELECT COUNT(*) FROM market_registry_v1").fetchone()[0])
        ready_n = int(reg.execute("SELECT COUNT(*) FROM market_registry_v1 WHERE ready_market_capsule=1").fetchone()[0])
        report = {
            "version": VERSION,
            "researchOnly": True,
            "winnerIncluded": False,
            "mode": "explicit" if ns.market_ids else ("full" if ns.full else "incremental"),
            "qualityCandidates": len(quality_rows),
            "refreshedMarkets": written,
            "registryMarkets": total,
            "readyMarketCapsule": ready_n,
            "registry": str(registry.resolve()),
            "parquet": str(parquet.resolve()),
            "parquetBytes": parquet.stat().st_size if parquet.exists() else 0,
            "elapsedSeconds": round(time.perf_counter() - t0, 3),
            "selectionContract": "quality-row candidates + indexed bounded source checks; no whole-table COUNT(DISTINCT market_id)",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        maker.close(); target.close(); public.close(); reg.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
