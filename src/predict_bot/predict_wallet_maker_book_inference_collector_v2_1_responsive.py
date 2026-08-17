from __future__ import annotations

"""Responsive HTTP/cache layer for the BTC Maker-book V2.1 collector.

The inference implementation stays in the sibling _impl module. This wrapper
keeps heavy SQLite summary work off request threads so Dashboard health/state
polls cannot stall the live collector.
"""

import copy
import os
import sqlite3
import threading
from typing import Any

from . import predict_wallet_maker_book_inference_collector as base
from . import predict_wallet_maker_book_inference_collector_v2_1_impl as impl

STATE_CACHE_REFRESH_MS = max(
    5_000,
    int(os.environ.get("PREDICT_WALLET_MAKER_BOOK_STATE_CACHE_REFRESH_MS", "15000")),
)


class ResponsiveMakerBookConsumableLifecycleCollector(impl.MakerBookConsumableLifecycleCollector):
    """Serve state from cache while a separate read-only connection refreshes it."""

    def __init__(self, db_path=base.DB_PATH, target_db_path=base.TARGET_DB_PATH) -> None:
        self._responsive_cache_lock = threading.RLock()
        self._responsive_state_cache: dict[str, Any] | None = None
        self._responsive_cache_at_ms = 0
        self._responsive_cache_error: str | None = None
        self._responsive_cache_build_ms: int | None = None
        super().__init__(db_path, target_db_path)

    def start(self) -> None:
        super().start()
        threading.Thread(
            target=self._responsive_snapshot_loop,
            name="maker-book-state-cache",
            daemon=True,
        ).start()

    def _runtime_status(self) -> str:
        current = self.current_market_id
        if not self.api_key:
            return "CONFIG_REQUIRED"
        if current is None:
            return "WAITING_MARKET"
        if self.ws_status == "LIVE" and self.last_error is None:
            return "LIVE"
        return "DEGRADED"

    def health_snapshot(self) -> dict[str, Any]:
        """Memory-only health payload. Never waits on SQLite."""
        now = base.now_ms()
        with self.lock:
            current = self.current_market_id
            title = self.current_title
            window_end = self.current_window_end_ms
            last_source = self.last_source_ms
            last_received = self.last_received_ms
            ws_status = self.ws_status
            ws_generation = self.ws_generation
            ws_error = self.ws_error
            last_error = self.last_error
        with self._responsive_cache_lock:
            cache_at = self._responsive_cache_at_ms
            cache_error = self._responsive_cache_error
            build_ms = self._responsive_cache_build_ms
            cache_ready = self._responsive_state_cache is not None
        return {
            "version": impl.VERSION,
            "cohort": self.cohort,
            "asset": self.asset,
            "timeframe": "5M",
            "status": self._runtime_status(),
            "responsiveHealth": True,
            "readOnly": True,
            "paperOnly": True,
            "forwardOnly": True,
            "current": {
                "marketId": current,
                "title": title,
                "windowEndMs": window_end,
                "lastSourceMs": last_source,
                "lastReceivedMs": last_received,
                "sampleAgeMs": now - last_received if last_received else None,
            },
            "websocket": {
                "status": ws_status,
                "generation": ws_generation,
                "error": ws_error,
            },
            "snapshotCache": {
                "ready": cache_ready,
                "lastBuiltAtMs": cache_at or None,
                "ageMs": now - cache_at if cache_at else None,
                "lastBuildDurationMs": build_ms,
                "lastError": cache_error,
                "refreshMs": STATE_CACHE_REFRESH_MS,
            },
            "lifecycleInference": {
                "version": impl.VERSION,
                "enabled": True,
                "readOnly": True,
                "consumableQuantityAllocation": True,
                "status": "CACHED" if cache_ready else "WARMING_CACHE",
            },
            "lastError": last_error,
            "checkedAtMs": now,
        }

    def _fallback_state(self) -> dict[str, Any]:
        """Fast state shape used only until the first background snapshot exists."""
        health = self.health_snapshot()
        health.update(
            {
                "targetWallet": base.TARGET_WALLET,
                "ordersSupported": False,
                "liveOrdersAffected": False,
                "storage": {
                    "database": str(self.db_path),
                    "databaseBytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
                    "snapshotCached": False,
                },
                "targetInference": {"status": "WARMING_SNAPSHOT_CACHE"},
                "targetActivity": {"status": "WARMING_SNAPSHOT_CACHE"},
                "lifecycleInferenceV2Retired": {
                    "status": "RETIRED_MANY_TO_ONE_EVIDENCE_REUSE",
                    "historicalTablesPreserved": True,
                },
            }
        )
        return health

    def _build_snapshot_from_reader(self) -> dict[str, Any]:
        """Build the expensive snapshot on an independent WAL reader connection."""
        uri = f"file:{self.db_path.resolve()}?mode=ro"
        con = sqlite3.connect(
            uri,
            uri=True,
            timeout=1.0,
            check_same_thread=False,
        )
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA busy_timeout=1000")

            reader = copy.copy(self)
            reader.db = con
            reader.db_lock = threading.RLock()
            reader.v21_cache = None
            reader.v21_cache_at = 0

            payload = base.MakerBookInferenceCollector.snapshot(reader)
            payload["version"] = impl.VERSION if self.asset == "BTC" else payload.get("version")
            if self.asset == "BTC":
                payload["lifecycleInference"] = impl.MakerBookConsumableLifecycleCollector._v21_snapshot(reader)
                payload["lifecycleInferenceV2Retired"] = {
                    "status": "RETIRED_MANY_TO_ONE_EVIDENCE_REUSE",
                    "historicalTablesPreserved": True,
                }
            return payload
        finally:
            con.close()

    def _responsive_snapshot_loop(self) -> None:
        while not self.stop_event.is_set():
            started = base.now_ms()
            try:
                payload = self._build_snapshot_from_reader()
                finished = base.now_ms()
                with self._responsive_cache_lock:
                    self._responsive_state_cache = payload
                    self._responsive_cache_at_ms = finished
                    self._responsive_cache_build_ms = max(0, finished - started)
                    self._responsive_cache_error = None
            except Exception as exc:
                finished = base.now_ms()
                with self._responsive_cache_lock:
                    self._responsive_cache_build_ms = max(0, finished - started)
                    self._responsive_cache_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            if self.stop_event.wait(STATE_CACHE_REFRESH_MS / 1000.0):
                return

    def snapshot(self) -> dict[str, Any]:
        """Return immediately; heavy DB summary work is never done here."""
        now = base.now_ms()
        with self._responsive_cache_lock:
            cached = self._responsive_state_cache
            cache_at = self._responsive_cache_at_ms
            cache_error = self._responsive_cache_error
            build_ms = self._responsive_cache_build_ms
        if cached is None:
            return self._fallback_state()
        payload = dict(cached)
        payload["snapshotCache"] = {
            "servedFromCache": True,
            "lastBuiltAtMs": cache_at,
            "ageMs": max(0, now - cache_at),
            "lastBuildDurationMs": build_ms,
            "lastError": cache_error,
            "refreshMs": STATE_CACHE_REFRESH_MS,
        }
        payload["checkedAtMs"] = now
        return payload


class ResponsiveHandler(base.Handler):
    collector: ResponsiveMakerBookConsumableLifecycleCollector

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/health":
            self._send(200, {"ok": True, "state": self.collector.health_snapshot()})
            return
        if path in {"/", "/state", "/api/state"}:
            self._send(200, {"ok": True, "state": self.collector.snapshot()})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        try:
            super()._send(status, payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) in {10053, 10054}:
                return
            raise


def main() -> int:
    collector = ResponsiveMakerBookConsumableLifecycleCollector(base.DB_PATH, base.TARGET_DB_PATH)
    collector.start()
    handler = type("ResponsiveMakerBookInferenceV21Handler", (ResponsiveHandler,), {"collector": collector})
    server = base.ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"{impl.VERSION} listening on http://{base.HOST}:{base.PORT}/state; asset={base.ASSET}; "
        "consumableQuantityAllocation=true; responsiveHealth=true; cachedState=true; "
        "readOnly=true; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        collector.stop()
    return 0
