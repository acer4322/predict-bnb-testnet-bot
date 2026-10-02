from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class PublicSourceSnapshotArchiveV2:
    """Research-only raw public source snapshot archive.

    Stores every public-source poll keyed by (market_id, sampled_at_ms). It is intentionally
    independent of strategy decisions and Echtgeld execution so historical closed-loop replay
    can reproduce intermediate own-state transitions that occur between recorded decisions.
    """

    def __init__(self, path: Path, *, commit_every: int = 16) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.lock = threading.RLock()
        self.commit_every = max(1, int(commit_every))
        self.pending = 0
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS public_source_snapshots_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id INTEGER NOT NULL,
                sampled_at_ms INTEGER NOT NULL,
                timestamp_ns INTEGER,
                seconds_left REAL,
                snapshot_json TEXT NOT NULL,
                archived_at_ms INTEGER NOT NULL,
                UNIQUE(market_id, sampled_at_ms)
            );
            CREATE INDEX IF NOT EXISTS idx_public_source_snapshots_v2_market_time
                ON public_source_snapshots_v2(market_id, sampled_at_ms);
            CREATE TABLE IF NOT EXISTS public_source_snapshot_archive_meta_v2 (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );
            """
        )
        now = int(time.time() * 1000)
        self.db.execute(
            "INSERT OR REPLACE INTO public_source_snapshot_archive_meta_v2(key,value,updated_at_ms) VALUES(?,?,?)",
            ("version", "PUBLIC_SOURCE_SNAPSHOT_ARCHIVE_V2", now),
        )
        self.db.commit()

    def record(self, snapshot: dict[str, Any]) -> bool:
        if not isinstance(snapshot, dict):
            return False
        try:
            market_id = int(snapshot.get("marketId") or 0)
            sampled_at_ms = int(snapshot.get("sampledAtMs") or 0)
        except (TypeError, ValueError, OverflowError):
            return False
        if market_id <= 0 or sampled_at_ms <= 0:
            return False
        try:
            timestamp_ns = int(snapshot.get("timestampNs")) if snapshot.get("timestampNs") is not None else None
        except (TypeError, ValueError, OverflowError):
            timestamp_ns = None
        try:
            seconds_left = float(snapshot.get("secondsLeft")) if snapshot.get("secondsLeft") is not None else None
        except (TypeError, ValueError, OverflowError):
            seconds_left = None
        body = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str)
        now = int(time.time() * 1000)
        with self.lock:
            cur = self.db.execute(
                """INSERT OR IGNORE INTO public_source_snapshots_v2(
                       market_id,sampled_at_ms,timestamp_ns,seconds_left,snapshot_json,archived_at_ms
                   ) VALUES(?,?,?,?,?,?)""",
                (market_id, sampled_at_ms, timestamp_ns, seconds_left, body, now),
            )
            if cur.rowcount:
                self.pending += 1
            if self.pending >= self.commit_every:
                self.db.commit()
                self.pending = 0
            return bool(cur.rowcount)

    def flush(self) -> None:
        with self.lock:
            self.db.commit()
            self.pending = 0

    def close(self) -> None:
        with self.lock:
            try:
                self.db.commit()
            finally:
                self.db.close()
