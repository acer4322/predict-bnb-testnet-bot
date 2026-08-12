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
POLY_DELETE_BATCH_ROWS = max(
    1_000,
    min(
        250_000,
        int(os.environ.get("PREDICT_CROSS_ORACLE_POLY_DELETE_BATCH_ROWS", "50000")),
    ),
)
POLY_DELETE_BATCHES_PER_CLEANUP = max(
    1,
    min(
        32,
        int(
            os.environ.get(
                "PREDICT_CROSS_ORACLE_POLY_DELETE_BATCHES_PER_CLEANUP", "8"
            )
        ),
    ),
)
DEFER_POLY_BACKLOG_CLEANUP = PROFILE == "POLY_LIVE"

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
    return None


def _next_expired_poly_market_with_events(
    self: Any, poly_cutoff: int
) -> str | None:
    row = self.db.execute(
        """
        SELECT pm.slug
        FROM polymarket_markets AS pm
        WHERE pm.window_end_ms < ?
          AND EXISTS (
              SELECT 1
              FROM polymarket_events AS pe
              WHERE pe.market_slug = pm.slug
              LIMIT 1
          )
        ORDER BY pm.window_end_ms
        LIMIT 1
        """,
        (poly_cutoff,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])


def _poly_backlog_status(self: Any, poly_cutoff: int) -> tuple[int, str | None]:
    row = self.db.execute(
        """
        SELECT COUNT(*), MIN(pm.window_end_ms)
        FROM polymarket_markets AS pm
        WHERE pm.window_end_ms < ?
          AND EXISTS (
              SELECT 1
              FROM polymarket_events AS pe
              WHERE pe.market_slug = pm.slug
              LIMIT 1
          )
        """,
        (poly_cutoff,),
    ).fetchone()
    remaining = max(0, int(row[0] or 0)) if row is not None else 0
    if not remaining:
        return 0, None
    oldest = self.db.execute(
        """
        SELECT pm.slug
        FROM polymarket_markets AS pm
        WHERE pm.window_end_ms < ?
          AND EXISTS (
              SELECT 1
              FROM polymarket_events AS pe
              WHERE pe.market_slug = pm.slug
              LIMIT 1
          )
        ORDER BY pm.window_end_ms
        LIMIT 1
        """,
        (poly_cutoff,),
    ).fetchone()
    return remaining, (str(oldest[0]) if oldest is not None else None)


def _cleanup_once(self: Any) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    poly_cutoff = now_ms - int(POLY_HOURS * 3600 * 1000)
    chain_cutoff_ns = (
        now_ms - int(CHAIN_HOURS * 3600 * 1000)
    ) * 1_000_000
    deleted: dict[str, Any] = {
        "polyEvents": 0,
        "polyBatches": 0,
        "polyMarketsDrained": 0,
        "polyExpiredMarketsRemaining": None,
        "polyOldestPendingMarket": None,
        "chainlinkTicks": 0,
    }

    # Do not delete polymarket_markets metadata. Instead, only select an expired
    # market while it still owns raw events. The previous LIMIT 1 query selected
    # the same already-empty oldest market forever and permanently stalled
    # retention for every later market.
    if not DEFER_POLY_BACKLOG_CLEANUP:
        for _ in range(POLY_DELETE_BATCHES_PER_CLEANUP):
            with self.db_lock:
                slug = _next_expired_poly_market_with_events(self, poly_cutoff)
                if slug is None:
                    break
                cur = self.db.execute(
                    """
                    DELETE FROM polymarket_events
                    WHERE id IN (
                        SELECT id
                        FROM polymarket_events
                        WHERE market_slug = ?
                        LIMIT ?
                    )
                    """,
                    (slug, POLY_DELETE_BATCH_ROWS),
                )
                batch_deleted = max(0, int(cur.rowcount or 0))
                self.db.commit()
            deleted["polyEvents"] += batch_deleted
            deleted["polyBatches"] += 1
            if batch_deleted < POLY_DELETE_BATCH_ROWS:
                deleted["polyMarketsDrained"] += 1
            if batch_deleted == 0:
                # Defensive escape: EXISTS should prevent this, but do not spin
                # through the batch budget if the database changes concurrently.
                break

        with self.db_lock:
            remaining, oldest = _poly_backlog_status(self, poly_cutoff)
        deleted["polyExpiredMarketsRemaining"] = remaining
        deleted["polyOldestPendingMarket"] = oldest

    with self.db_lock:
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
            started = time.monotonic()
            result = _cleanup_once(self)
            result["durationMs"] = int((time.monotonic() - started) * 1000)
            self._retention_last = result
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


def _storage_metrics(self: Any) -> dict[str, Any]:
    """Cheap SQLite page telemetry for the Dashboard V2 storage monitor."""
    try:
        with self.db_lock:
            page_size_row = self.db.execute("PRAGMA page_size").fetchone()
            page_count_row = self.db.execute("PRAGMA page_count").fetchone()
            freelist_row = self.db.execute("PRAGMA freelist_count").fetchone()
        page_size = max(0, int(page_size_row[0] or 0)) if page_size_row else 0
        page_count = max(0, int(page_count_row[0] or 0)) if page_count_row else 0
        freelist_count = max(0, int(freelist_row[0] or 0)) if freelist_row else 0
        db_bytes = page_size * page_count
        reusable_bytes = page_size * freelist_count
        live_bytes = max(0, db_bytes - reusable_bytes)
        reusable_fraction = (
            freelist_count / page_count if page_count > 0 else 0.0
        )
        return {
            "pageSizeBytes": page_size,
            "pageCount": page_count,
            "freelistCount": freelist_count,
            "dbBytes": db_bytes,
            "reusableBytes": reusable_bytes,
            "liveBytesApprox": live_bytes,
            "reusableFraction": reusable_fraction,
            "storageMetricsError": None,
        }
    except Exception as exc:
        return {
            "pageSizeBytes": None,
            "pageCount": None,
            "freelistCount": None,
            "dbBytes": None,
            "reusableBytes": None,
            "liveBytesApprox": None,
            "reusableFraction": None,
            "storageMetricsError": str(exc)[:300],
        }


def _retention_health(self: Any) -> tuple[str, int | None]:
    error = getattr(self, "_retention_error", None)
    last_at_ms = getattr(self, "_retention_at_ms", None)
    if error:
        return "ERROR", None
    if DEFER_POLY_BACKLOG_CLEANUP:
        return "DEFERRED", None
    if last_at_ms is None:
        return "STARTING", None

    age_ms = max(0, int(time.time() * 1000) - int(last_at_ms))
    stale_after_ms = int(max(180.0, CLEANUP_SECONDS * 3.0) * 1000)
    if age_ms > stale_after_ms:
        return "STALE", age_ms

    last = getattr(self, "_retention_last", {})
    remaining = last.get("polyExpiredMarketsRemaining") if isinstance(last, dict) else None
    if remaining is None:
        return "UNKNOWN", age_ms
    return ("HEALTHY" if int(remaining) == 0 else "CATCHING_UP"), age_ms


def _snapshot(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    storage = payload.setdefault("storage", {})
    retention_status, cleanup_age_ms = _retention_health(self)
    storage.update(
        runtimeProfile=PROFILE,
        polyRawArchiveEnabled=POLY_ARCHIVE,
        polyRawRetentionHours=POLY_HOURS,
        polyDeleteBatchRows=POLY_DELETE_BATCH_ROWS,
        polyDeleteBatchesPerCleanup=POLY_DELETE_BATCHES_PER_CLEANUP,
        chainlinkRetentionHours=CHAIN_HOURS,
        cleanupIntervalSeconds=CLEANUP_SECONDS,
        polyBacklogCleanupDeferred=DEFER_POLY_BACKLOG_CLEANUP,
        lastRetentionCleanupAtMs=getattr(self, "_retention_at_ms", None),
        lastRetentionCleanupAgeMs=cleanup_age_ms,
        lastRetentionCleanup=getattr(self, "_retention_last", {}),
        retentionStatus=retention_status,
        retentionError=getattr(self, "_retention_error", None),
        sqliteDeleteShrinksFileImmediately=False,
        historicalCountsSkipped=PROFILE == "POLY_LIVE",
        rawPayloadsStored=POLY_ARCHIVE,
        skippedPolyRawInserts=(
            int(getattr(self.db, "skipped_poly_inserts", 0))
            if not POLY_ARCHIVE
            else 0
        ),
        **_storage_metrics(self),
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
