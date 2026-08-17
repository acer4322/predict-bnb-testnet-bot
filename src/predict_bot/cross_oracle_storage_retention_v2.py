from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import cross_oracle_storage_retention as v1


Collector = v1.Collector
ROOT = Path(__file__).resolve().parents[2]
HISTORY_DB_PATH = Path(
    os.environ.get(
        "PREDICT_CROSS_ORACLE_HISTORY_DB",
        ROOT / "data" / "cross_oracle_history_7d.db",
    )
)
HISTORY_RETENTION_HOURS = max(
    24.0,
    float(os.environ.get("PREDICT_CROSS_ORACLE_HISTORY_RETENTION_HOURS", "168")),
)
HISTORY_SAMPLE_MS = max(
    100,
    int(os.environ.get("PREDICT_CROSS_ORACLE_HISTORY_SAMPLE_MS", "250")),
)
HISTORY_CLEANUP_SECONDS = max(
    30.0,
    float(os.environ.get("PREDICT_CROSS_ORACLE_HISTORY_CLEANUP_SECONDS", "60")),
)

_original_start = Collector.start
_original_snapshot = Collector.snapshot


def _history_connect() -> sqlite3.Connection:
    HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(HISTORY_DB_PATH, timeout=5.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS cross_oracle_history_samples (
            bucket_ms INTEGER PRIMARY KEY,
            market_slug TEXT NOT NULL,
            condition_id TEXT,
            market_start_ms INTEGER,
            market_end_ms INTEGER,
            poly_source_ms INTEGER,
            poly_received_ms INTEGER,
            up_bid REAL,
            up_ask REAL,
            up_last REAL,
            down_bid REAL,
            down_ask REAL,
            down_last REAL,
            chainlink_price REAL,
            chainlink_source_ms INTEGER,
            chainlink_received_ms INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_cross_oracle_history_market
            ON cross_oracle_history_samples(market_slug, bucket_ms);
        """
    )
    db.commit()
    return db


def _sample_payload(self: Any, bucket_ms: int) -> tuple[Any, ...] | None:
    with self.lock:
        market = dict(self.market or {})
        poly = dict(self.polymarket or {})
        up = dict(poly.get("up") or {})
        down = dict(poly.get("down") or {})
        chain = dict(self.chainlink or {})
    slug = str(market.get("slug") or "")
    if not slug:
        return None
    return (
        int(bucket_ms),
        slug,
        market.get("conditionId"),
        market.get("windowStartMs"),
        market.get("windowEndMs"),
        poly.get("sourceTimestampMs"),
        poly.get("receivedTimestampMs"),
        up.get("bestBid"),
        up.get("bestAsk"),
        up.get("lastTrade"),
        down.get("bestBid"),
        down.get("bestAsk"),
        down.get("lastTrade"),
        chain.get("price"),
        chain.get("sourceTimestampMs"),
        chain.get("receivedTimestampMs"),
    )


def _history_loop(self: Any) -> None:
    db: sqlite3.Connection | None = None
    try:
        db = _history_connect()
        self._compact_history_status = "RUNNING"
        self._compact_history_error = None
        self._compact_history_last_cleanup = time.monotonic()
        self._compact_history_samples_written = 0
        last_bucket = -1
        while not self.stop_event.is_set():
            now_ms = int(time.time() * 1000)
            bucket_ms = (now_ms // HISTORY_SAMPLE_MS) * HISTORY_SAMPLE_MS
            if bucket_ms != last_bucket:
                row = _sample_payload(self, bucket_ms)
                if row is not None:
                    db.execute(
                        """INSERT OR REPLACE INTO cross_oracle_history_samples(
                               bucket_ms, market_slug, condition_id, market_start_ms,
                               market_end_ms, poly_source_ms, poly_received_ms,
                               up_bid, up_ask, up_last, down_bid, down_ask, down_last,
                               chainlink_price, chainlink_source_ms, chainlink_received_ms
                           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        row,
                    )
                    self._compact_history_samples_written += 1
                last_bucket = bucket_ms

            now_mono = time.monotonic()
            if now_mono - self._compact_history_last_cleanup >= HISTORY_CLEANUP_SECONDS:
                cutoff = now_ms - int(HISTORY_RETENTION_HOURS * 3600 * 1000)
                db.execute(
                    """DELETE FROM cross_oracle_history_samples
                       WHERE bucket_ms < ?""",
                    (cutoff,),
                )
                db.commit()
                self._compact_history_last_cleanup = now_mono
                self._compact_history_last_cleanup_at_ms = now_ms
            elif self._compact_history_samples_written % 20 == 0:
                db.commit()

            sleep_s = max(0.02, HISTORY_SAMPLE_MS / 1000.0 / 2.0)
            if self.stop_event.wait(sleep_s):
                break
        db.commit()
    except Exception as exc:
        self._compact_history_status = "ERROR"
        self._compact_history_error = str(exc)[:500]
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        if getattr(self, "_compact_history_status", None) != "ERROR":
            self._compact_history_status = "STOPPED"


def _start(self: Any) -> None:
    _original_start(self)
    self._compact_history_status = "STARTING"
    self._compact_history_error = None
    self._compact_history_samples_written = 0
    self._compact_history_last_cleanup_at_ms = None
    threading.Thread(
        target=_history_loop,
        args=(self,),
        name="cross-oracle-history-7d",
        daemon=True,
    ).start()


def _snapshot(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    try:
        db_bytes = HISTORY_DB_PATH.stat().st_size
        wal = HISTORY_DB_PATH.with_name(HISTORY_DB_PATH.name + "-wal")
        if wal.exists():
            db_bytes += wal.stat().st_size
    except OSError:
        db_bytes = 0
    payload.setdefault("storage", {})["compactHistory"] = {
        "enabled": True,
        "dbPath": str(HISTORY_DB_PATH),
        "dbBytes": db_bytes,
        "retentionHours": HISTORY_RETENTION_HOURS,
        "sampleMs": HISTORY_SAMPLE_MS,
        "rawJsonStored": False,
        "bookDepthStored": False,
        "topOfBookStored": True,
        "chainlinkStored": True,
        "status": getattr(self, "_compact_history_status", "STARTING"),
        "error": getattr(self, "_compact_history_error", None),
        "samplesWrittenThisRun": int(
            getattr(self, "_compact_history_samples_written", 0)
        ),
        "lastCleanupAtMs": getattr(
            self, "_compact_history_last_cleanup_at_ms", None
        ),
    }
    return payload


Collector.start = _start
Collector.snapshot = _snapshot


def main() -> int:
    return v1.main()


if __name__ == "__main__":
    raise SystemExit(main())
