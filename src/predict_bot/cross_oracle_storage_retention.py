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
POLY_HOURS = max(0.25, float(os.environ.get("PREDICT_CROSS_ORACLE_POLY_RAW_RETENTION_HOURS", "6" if PROFILE != "RESEARCH" else "72")))
CHAIN_HOURS = max(1.0, float(os.environ.get("PREDICT_CROSS_ORACLE_CHAINLINK_RETENTION_HOURS", "72")))
CLEANUP_SECONDS = max(30.0, float(os.environ.get("PREDICT_CROSS_ORACLE_CLEANUP_INTERVAL_SECONDS", "60")))

_original_start = Collector.start
_original_snapshot = Collector.snapshot


def _install_policy(self: Any) -> None:
    if POLY_ARCHIVE:
        return
    with self.db_lock:
        self.db.execute(
            "CREATE TEMP TRIGGER IF NOT EXISTS skip_poly_raw BEFORE INSERT ON polymarket_events BEGIN SELECT RAISE(IGNORE); END"
        )
        self.db.commit()


def _cleanup_once(self: Any) -> dict[str, int]:
    now_ms = int(time.time() * 1000)
    poly_cutoff = now_ms - int(POLY_HOURS * 3600 * 1000)
    chain_cutoff_ns = (now_ms - int(CHAIN_HOURS * 3600 * 1000)) * 1_000_000
    deleted = {"polyEvents": 0, "chainlinkTicks": 0}
    with self.db_lock:
        row = self.db.execute(
            "SELECT slug FROM polymarket_markets WHERE window_end_ms < ? ORDER BY window_end_ms LIMIT 1",
            (poly_cutoff,),
        ).fetchone()
        if row is not None:
            cur = self.db.execute("DELETE FROM polymarket_events WHERE market_slug=?", (str(row[0]),))
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
    threading.Thread(target=_cleanup_loop, args=(self,), name="cross-oracle-retention", daemon=True).start()


def _snapshot(self: Any) -> dict[str, Any]:
    payload = _original_snapshot(self)
    payload.setdefault("storage", {}).update(
        runtimeProfile=PROFILE,
        polyRawArchiveEnabled=POLY_ARCHIVE,
        polyRawRetentionHours=POLY_HOURS,
        chainlinkRetentionHours=CHAIN_HOURS,
        cleanupIntervalSeconds=CLEANUP_SECONDS,
        lastRetentionCleanupAtMs=getattr(self, "_retention_at_ms", None),
        lastRetentionCleanup=getattr(self, "_retention_last", {}),
        retentionError=getattr(self, "_retention_error", None),
        sqliteDeleteShrinksFileImmediately=False,
    )
    return payload


Collector.start = _start
Collector.snapshot = _snapshot


def main() -> int:
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
