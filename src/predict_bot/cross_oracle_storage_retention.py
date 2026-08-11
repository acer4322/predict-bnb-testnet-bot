from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any

from . import cross_oracle_gamma_redundant_discovery as runtime

cross = runtime.cross
resilient = runtime.resilient
Collector = resilient.ResilientCrossOracleCollector
PROFILE = os.environ.get("PREDICT_RUNTIME_PROFILE", "FULL_LAB").strip().upper()
POLY_ARCHIVE = os.environ.get(
    "PREDICT_CROSS_ORACLE_POLY_RAW_ARCHIVE_ENABLED",
    "0" if PROFILE == "POLY_LIVE" else "1",
).strip().lower() not in {"0", "false", "no", "off"}
POLY_HOURS = max(
    0.25,
    float(
        os.environ.get(
            "PREDICT_CROSS_ORACLE_POLY_RAW_RETENTION_HOURS",
            "6" if PROFILE != "RESEARCH" else "72",
        )
    ),
)
CHAIN_HOURS = max(
    1.0,
    float(os.environ.get("PREDICT_CROSS_ORACLE_CHAINLINK_RETENTION_HOURS", "72")),
)
CLEANUP_SECONDS = max(
    30.0,
    float(os.environ.get("PREDICT_CROSS_ORACLE_CLEANUP_INTERVAL_SECONDS", "60")),
)

_original_base_init = cross.CrossOracleCollector.__init__
_original_start = Collector.start
_original_snapshot = Collector.snapshot


class _IgnoredCursor:
    rowcount = 0


class _ArchiveFilterConnection:
    """Delegate SQLite normally, but drop high-rate Poly archival INSERTs."""

    def __init__(self, inner: sqlite3.Connection) -> None:
        self._inner = inner
        self.skipped_poly_inserts = 0

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        normalized = " ".join(str(sql).split()).upper()
        if normalized.startswith("INSERT INTO POLYMARKET_EVENTS"):
            self.skipped_poly_inserts += 1
            return _IgnoredCursor()
        return self._inner.execute(sql, parameters)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _base_init(self: Any) -> None:
    if PROFILE != "POLY_LIVE":
        return _original_base_init(self)

    # Same runtime state as the base collector, but deliberately avoid historical
    # COUNT(*) over a potentially huge raw archive during lightweight startup.
    self.lock = threading.RLock()
    self.db_lock = threading.RLock()
    self.stop_event = threading.Event()
    self.started_at_ms = int(time.time() * 1000)
    self.chainlink_ws = None
    self.polymarket_ws = None
    self.polymarket_generation = 0
    self.market = None
    self.chainlink_ticks = []
    self.chainlink = {
        "status": "STARTING",
        "symbol": cross.CHAINLINK_SYMBOL,
        "price": None,
        "sourceTimestampMs": None,
        "receivedTimestampMs": None,
        "error": None,
    }
    self.polymarket = {
        "status": "WAITING_MARKET",
        "receivedTimestampMs": None,
        "sourceTimestampMs": None,
        "error": None,
        "up": {"tokenId": None, "bestBid": None, "bestAsk": None, "lastTrade": None},
        "down": {"tokenId": None, "bestBid": None, "bestAsk": None, "lastTrade": None},
        "startPrice": None,
        "startPriceTimestampMs": None,
        "startPriceOffsetMs": None,
    }
    cross.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw_db = sqlite3.connect(cross.DB_PATH, check_same_thread=False, timeout=5.0)
    raw_db.row_factory = sqlite3.Row
    raw_db.execute("PRAGMA journal_mode=WAL")
    raw_db.execute("PRAGMA synchronous=NORMAL")
    raw_db.execute("PRAGMA busy_timeout=5000")
    self.db = raw_db
    self._create_schema()
    if not POLY_ARCHIVE:
        self.db = _ArchiveFilterConnection(raw_db)
    self.chainlink_rows = 0
    self.polymarket_rows = 0


cross.CrossOracleCollector.__init__ = _base_init


def _install_policy(self: Any) -> None:
    # FULL_LAB/RESEARCH use the normal SQLite connection. POLY_LIVE's filtered
    # connection was installed during base initialization before sockets start.
    return None


def _cleanup_once(self: Any) -> dict[str, int]:
    now_ms = int(time.time() * 1000)
    poly_cutoff = now_ms - int(POLY_HOURS * 3600 * 1000)
    chain_cutoff_ns = (
        now_ms - int(CHAIN_HOURS * 3600 * 1000)
    ) * 1_000_000
    deleted = {"polyEvents": 0, "chainlinkTicks": 0}
    with self.db_lock:
        row = self.db.execute(
            "SELECT slug FROM polymarket_markets WHERE window_end_ms < ? ORDER BY window_end_ms LIMIT 1",
            (poly_cutoff,),
        ).fetchone()
        if row is not None:
            cur = self.db.execute(
                "DELETE FROM polymarket_events WHERE market_slug=?",
                (str(row[0]),),
            )
            deleted["polyEvents"] = max(0, int(cur.rowcount or 0))
        cur = self.db.execute(
            "DELETE FROM chainlink_ticks WHERE id IN (SELECT id FROM chainlink_ticks WHERE received_wall_ns < ? ORDER BY id LIMIT 50000)",
            (chain_cutoff_ns,),
        )
        deleted["chainlinkTicks"] = max(0, int(cur.rowcount or 0))
        self.db.commit()
    return deleted


def _cleanup_loop(self: Any) -> None:
    if self.stop_event.wait(15.0):
        return
    while not self.stop_event.is_set():
        try:
            self._retention_last = _cleanup_once(self)
            self._retention_at_ms = int(time.time() * 1000)
            self._retention_error = None
        except Exception as exc:
            self._retention_error = str(exc)[:400]
        if self.stop_event.wait(CLEANUP_SECONDS):
            return


def _start(self: Any) -> None:
    _install_policy(self)
    _original_start(self)
    self._retention_last = {}
    self._retention_at_ms = None
    self._retention_error = None
    threading.Thread(
        target=_cleanup_loop,
        args=(self,),
        name="cross-oracle-retention",
        daemon=True,
    ).start()


def _snapshot(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    storage = payload.setdefault("storage", {})
    storage.update(
        runtimeProfile=PROFILE,
        polyRawArchiveEnabled=POLY_ARCHIVE,
        polyRawRetentionHours=POLY_HOURS,
        chainlinkRetentionHours=CHAIN_HOURS,
        cleanupIntervalSeconds=CLEANUP_SECONDS,
        lastRetentionCleanupAtMs=getattr(self, "_retention_at_ms", None),
        lastRetentionCleanup=getattr(self, "_retention_last", {}),
        retentionError=getattr(self, "_retention_error", None),
        sqliteDeleteShrinksFileImmediately=False,
        historicalCountsSkipped=PROFILE == "POLY_LIVE",
        rawPayloadsStored=POLY_ARCHIVE,
        skippedPolyRawInserts=(
            int(getattr(self.db, "skipped_poly_inserts", 0))
            if not POLY_ARCHIVE
            else 0
        ),
    )
    if PROFILE == "POLY_LIVE":
        storage["chainlinkRows"] = None
        storage["polymarketRows"] = None
    return payload


Collector.start = _start
Collector.snapshot = _snapshot


def main() -> int:
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
