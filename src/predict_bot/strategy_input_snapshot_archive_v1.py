from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

VERSION = "STRATEGY_INPUT_TRACE_V2_BOOK_FRONTIER"


class StrategyInputSnapshotArchiveV1:
    """Research-only archive of snapshots actually consumed by a strategy controller.

    This recorder has no trading side effects. Failures are surfaced through last_error
    but callers should keep strategy execution fail-open with respect to this archive.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.last_error: str | None = None
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategy_input_snapshots_v1 (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              controller_version TEXT NOT NULL,
              market_id INTEGER NOT NULL,
              sampled_at_ms INTEGER NOT NULL,
              timestamp_ns INTEGER,
              consumed_at_ms INTEGER NOT NULL,
              snapshot_json TEXT NOT NULL,
              UNIQUE(controller_version, market_id, sampled_at_ms)
            );
            CREATE INDEX IF NOT EXISTS idx_strategy_input_snapshots_v1_market
              ON strategy_input_snapshots_v1(controller_version, market_id, sampled_at_ms);
            CREATE TABLE IF NOT EXISTS strategy_input_archive_meta_v1 (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            """
        )
        # Forward-compatible migration: V2 adds the exact 8778 book frontier that
        # was visible immediately after each consumed Strategy snapshot.
        cols = {str(r[1]) for r in self.db.execute("PRAGMA table_info(strategy_input_snapshots_v1)")}
        for name, typ in [
            ("book_source_ms", "INTEGER"), ("book_context_at_ms", "INTEGER"),
            ("book_bid_count", "INTEGER"), ("book_ask_count", "INTEGER"),
            ("book_best_bid", "REAL"), ("book_best_ask", "REAL"),
            ("book_state_hash", "TEXT")
        ]:
            if name not in cols:
                self.db.execute(f"ALTER TABLE strategy_input_snapshots_v1 ADD COLUMN {name} {typ}")
        self.db.execute(
            "INSERT OR REPLACE INTO strategy_input_archive_meta_v1(key,value) VALUES('version',?)",
            (VERSION,),
        )
        self.db.commit()

    def record(self, controller_version: str, snapshot: dict[str, Any], *, consumed_at_ms: int | None = None) -> None:
        try:
            market_id = int(snapshot.get("marketId") or 0)
            sampled_at_ms = int(snapshot.get("sampledAtMs") or 0)
            if market_id <= 0 or sampled_at_ms <= 0:
                return
            timestamp_ns = snapshot.get("timestampNs")
            if timestamp_ns is not None:
                timestamp_ns = int(timestamp_ns)
            consumed = int(consumed_at_ms if consumed_at_ms is not None else time.time() * 1000)
            payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), default=str)
            with self.lock:
                self.db.execute(
                    """INSERT OR IGNORE INTO strategy_input_snapshots_v1(
                         controller_version,market_id,sampled_at_ms,timestamp_ns,consumed_at_ms,snapshot_json
                       ) VALUES(?,?,?,?,?,?)""",
                    (str(controller_version), market_id, sampled_at_ms, timestamp_ns, consumed, payload),
                )
                self.db.commit()
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"


    def record_book_context(
        self, controller_version: str, *, market_id: int, sampled_at_ms: int,
        book_source_ms: int | None, book: dict[str, dict[float, float]],
        context_at_ms: int | None = None,
    ) -> None:
        """Attach the exact public-book frontier visible to the live Strategy step."""
        try:
            bids = dict((book or {}).get("bids") or {})
            asks = dict((book or {}).get("asks") or {})
            best_bid = max(bids) if bids else None
            best_ask = min(asks) if asks else None
            canonical = {
                "bids": [[format(float(k), ".10g"), format(float(v), ".12g")] for k, v in sorted(bids.items())],
                "asks": [[format(float(k), ".10g"), format(float(v), ".12g")] for k, v in sorted(asks.items())],
            }
            digest = hashlib.sha256(json.dumps(canonical,separators=(",",":"),sort_keys=True).encode("utf-8")).hexdigest()
            ctx = int(context_at_ms if context_at_ms is not None else time.time()*1000)
            with self.lock:
                self.db.execute(
                    """UPDATE strategy_input_snapshots_v1
                       SET book_source_ms=?,book_context_at_ms=?,book_bid_count=?,book_ask_count=?,
                           book_best_bid=?,book_best_ask=?,book_state_hash=?
                       WHERE controller_version=? AND market_id=? AND sampled_at_ms=?""",
                    (int(book_source_ms) if book_source_ms is not None else None,ctx,len(bids),len(asks),
                     float(best_bid) if best_bid is not None else None,float(best_ask) if best_ask is not None else None,digest,
                     str(controller_version),int(market_id),int(sampled_at_ms)),
                )
                self.db.commit()
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {str(exc)[:500]}"

    def close(self) -> None:
        try:
            with self.lock:
                self.db.close()
        except Exception:
            pass
