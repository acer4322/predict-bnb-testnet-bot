from __future__ import annotations

import threading
import time
from http.server import ThreadingHTTPServer
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_2 as v4_2
from . import predict_wallet_shadow_observer_v4_13 as v4_13


VERSION = "PREDICT_WALLET_SHADOW_V0_17_NONBLOCKING_REPORT_CACHE"
REPORT_REFRESH_MS = 60_000
REPORT_ERROR_RETRY_MS = 300_000
REPORT_BUILD_MIN_SECONDS_LEFT = 150.0
REPORT_BUILD_MAX_SECONDS_LEFT = 240.0


class WalletShadowObserver(v4_13.WalletShadowObserver):
    """V4.13 with a non-blocking stale-while-revalidate /state report cache.

    Strategy collection stays on the normal observer thread. Expensive historical
    report generation is allowed to run only once in a daemon worker and only in
    the middle of a 5-minute market. HTTP /state requests never execute the
    expensive V4.13 report synchronously.
    """

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=v4_2.SIMULATION_DB_PATH) -> None:
        self._report_lock = threading.Lock()
        self._report_cache: dict[str, Any] | None = None
        self._report_thread: threading.Thread | None = None
        self._report_building = False
        self._report_build_count = 0
        self._report_last_started_ms: int | None = None
        self._report_last_completed_ms: int | None = None
        self._report_last_generation_ms: float | None = None
        self._report_error: str | None = None
        super().__init__(db_path, simulation_db_path)

    def _report_safe_window(self) -> tuple[bool, float | None]:
        snapshot = self.latest_public_signal_snapshot
        if not isinstance(snapshot, dict):
            return False, None
        try:
            seconds_left = float(snapshot.get("seconds_left"))
        except (TypeError, ValueError, OverflowError):
            return False, None
        return REPORT_BUILD_MIN_SECONDS_LEFT <= seconds_left <= REPORT_BUILD_MAX_SECONDS_LEFT, seconds_left

    def _report_diagnostics(self) -> dict[str, Any]:
        now_ms = base._now_ms()
        with self._report_lock:
            has_cache = self._report_cache is not None
            building = self._report_building
            completed = self._report_last_completed_ms
            started = self._report_last_started_ms
            generation_ms = self._report_last_generation_ms
            build_count = self._report_build_count
            error = self._report_error
        age_ms = now_ms - completed if completed is not None else None
        safe, seconds_left = self._report_safe_window()
        stale = age_ms is not None and age_ms >= REPORT_REFRESH_MS
        error_backoff = bool(error and started is not None and now_ms - started < REPORT_ERROR_RETRY_MS)
        if not has_cache:
            if building:
                status = "BUILDING_FIRST_REPORT"
            elif error_backoff:
                status = "ERROR_BACKOFF"
            elif error:
                status = "ERROR_WAITING_SAFE_WINDOW" if not safe else "ERROR_RETRY_READY"
            else:
                status = "WAITING_SAFE_WINDOW" if not safe else "BUILD_READY"
        elif building:
            status = "REFRESHING"
        elif error_backoff:
            status = "STALE_ERROR_BACKOFF" if stale else "READY_WITH_REPORT_ERROR"
        elif stale:
            status = "STALE_WAITING_SAFE_WINDOW" if not safe else "STALE_REFRESH_READY"
        else:
            status = "READY"
        return {
            "reportStatus": status,
            "reportAgeMs": age_ms,
            "lastReportStartedMs": started,
            "lastReportCompletedMs": completed,
            "lastReportGenerationMs": generation_ms,
            "reportBuildCount": build_count,
            "reportError": error,
            "reportRefreshMs": REPORT_REFRESH_MS,
            "reportErrorRetryMs": REPORT_ERROR_RETRY_MS,
            "reportBuildSafeWindowSecondsLeft": [REPORT_BUILD_MIN_SECONDS_LEFT, REPORT_BUILD_MAX_SECONDS_LEFT],
            "reportBuildSafeNow": safe,
            "secondsLeftAtCheck": seconds_left,
            "nonBlockingState": True,
        }

    def _build_full_report(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        return payload

    def _report_worker(self) -> None:
        started = time.perf_counter()
        try:
            payload = self._build_full_report()
            generation_ms = (time.perf_counter() - started) * 1_000.0
            completed_ms = base._now_ms()
            with self._report_lock:
                self._report_last_generation_ms = generation_ms
                self._report_last_completed_ms = completed_ms
                self._report_error = None
                self._report_cache = payload
        except Exception as exc:  # report failures must never stop strategy collection
            with self._report_lock:
                self._report_error = str(exc)[:500]
        finally:
            with self._report_lock:
                self._report_building = False

    def _maybe_start_report_build(self) -> None:
        now_ms = base._now_ms()
        with self._report_lock:
            if self._report_building:
                return
            if (
                self._report_error
                and self._report_last_started_ms is not None
                and now_ms - self._report_last_started_ms < REPORT_ERROR_RETRY_MS
            ):
                return
            if (
                self._report_cache is not None
                and self._report_last_completed_ms is not None
                and now_ms - self._report_last_completed_ms < REPORT_REFRESH_MS
            ):
                return
        safe, _ = self._report_safe_window()
        if not safe:
            return
        with self._report_lock:
            if self._report_building:
                return
            if (
                self._report_error
                and self._report_last_started_ms is not None
                and now_ms - self._report_last_started_ms < REPORT_ERROR_RETRY_MS
            ):
                return
            if (
                self._report_cache is not None
                and self._report_last_completed_ms is not None
                and now_ms - self._report_last_completed_ms < REPORT_REFRESH_MS
            ):
                return
            self._report_building = True
            self._report_build_count += 1
            self._report_last_started_ms = now_ms
            thread = threading.Thread(
                target=self._report_worker,
                name="wallet-shadow-full-report",
                daemon=True,
            )
            self._report_thread = thread
        thread.start()

    def _lightweight_snapshot(self) -> dict[str, Any]:
        health = super().health_snapshot()
        return {
            "version": VERSION,
            "status": health.get("status"),
            "paperOnly": True,
            "liveOrdersAffected": False,
            "market": {"marketId": health.get("marketId")},
            "reportOnlyState": True,
            "message": "Full research report is built asynchronously; strategy collection continues independently.",
        }

    def _overlay_live_reconstructed_state(self, payload: dict[str, Any]) -> None:
        state = self.reconstructed_state
        existing = payload.get("reconstructedMakerRulesLab")
        if not isinstance(existing, dict):
            return
        active_orders = list(state["orders"].values())
        current_reserved = sum(float(order["price"]) * float(order["shares"]) for order in active_orders)
        lab = dict(existing)
        lab["status"] = "ACTIVE" if state["active"] else "WAITING_NEXT_COMPLETE_MARKET"
        lab["current"] = {
            "marketId": self.market_id,
            "initializationStatus": state["initializationStatus"],
            "frozen": state["frozen"],
            "activeOrders": len(active_orders),
            "upOrders": sum(order["side"] == "UP" for order in active_orders),
            "downOrders": sum(order["side"] == "DOWN" for order in active_orders),
            "openingOrders": sum(order["origin"] == "OPENING_RAIL" for order in active_orders),
            "refillOrders": sum(order["origin"] == "REFILL" for order in active_orders),
            "currentReservedUsdt": current_reserved,
            "initialReservedUsdt": state["initialReservedUsdt"],
            "peakReservedUsdt": state["peakReservedUsdt"],
            "inventory": self._current_reconstructed_inventory(),
        }
        payload["reconstructedMakerRulesLab"] = lab

    def snapshot(self) -> dict[str, Any]:
        self._maybe_start_report_build()
        with self._report_lock:
            cached = self._report_cache
        payload = dict(cached) if cached is not None else self._lightweight_snapshot()
        payload["version"] = VERSION
        self._overlay_live_reconstructed_state(payload)
        diagnostics = self._report_diagnostics()
        previous_diagnostics = payload.get("observerDiagnostics")
        merged_diagnostics = dict(previous_diagnostics) if isinstance(previous_diagnostics, dict) else {}
        merged_diagnostics.update(diagnostics)
        payload["observerDiagnostics"] = merged_diagnostics
        payload["reportStatus"] = diagnostics["reportStatus"]
        payload["reportAgeMs"] = diagnostics["reportAgeMs"]
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload.update(self._report_diagnostics())
        return payload


class _Handler(v4_13._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_14Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "non-blocking cached full reports enabled; paperOnly=true; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
