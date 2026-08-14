from __future__ import annotations

import json
import os
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_16 as v4_16


VERSION = "PREDICT_WALLET_SHADOW_V0_21_PERSISTENT_WARM_REPORT_CACHE"
REPORT_CACHE_PATH = Path(
    os.environ.get(
        "PREDICT_WALLET_SHADOW_REPORT_CACHE",
        base.ROOT / "data" / "wallet-shadow-last-good-state.json",
    )
)
REPORT_CACHE_MAX_BYTES = 64 * 1024 * 1024


class WalletShadowObserver(v4_16.WalletShadowObserver):
    """V4.16 with a persistent last-good full report for warm Dashboard restarts.

    The expensive historical report remains asynchronous. A successful report is
    saved atomically and loaded on the next process start, while current strategy
    state is still overlaid from live in-memory state by the V4.14+ snapshot path.
    """

    def __init__(self, db_path=base.DB_PATH, simulation_db_path=None) -> None:
        self._persistent_cache_loaded = False
        self._persistent_cache_loaded_at_ms: int | None = None
        self._persistent_cache_source_ms: int | None = None
        self._persistent_cache_error: str | None = None
        self._fresh_report_completed_this_run = False
        super().__init__(db_path, simulation_db_path)
        self._load_persistent_report_cache()

    @staticmethod
    def _cache_payload_is_substantial(payload: Any) -> bool:
        if not isinstance(payload, dict) or payload.get("reportOnlyState") is True:
            return False
        return any(
            key in payload
            for key in (
                "makerInventoryTakerSharedLab",
                "targetTakerMirrorAudit",
                "targetAccounting",
                "reconstructedMakerRulesLab",
                "makerFlowAlphaLab",
            )
        )

    def _load_persistent_report_cache(self) -> None:
        path = REPORT_CACHE_PATH
        try:
            if not path.exists():
                return
            stat = path.stat()
            if stat.st_size <= 0 or stat.st_size > REPORT_CACHE_MAX_BYTES:
                self._persistent_cache_error = f"cache size rejected: {stat.st_size} bytes"
                return
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not self._cache_payload_is_substantial(payload):
                self._persistent_cache_error = "cache did not contain a substantial full-state payload"
                return
            source_ms = int(stat.st_mtime * 1_000)
            with self._report_lock:
                self._report_cache = payload
                self._report_last_completed_ms = source_ms
                self._report_error = None
            self._persistent_cache_loaded = True
            self._persistent_cache_loaded_at_ms = base._now_ms()
            self._persistent_cache_source_ms = source_ms
        except Exception as exc:
            self._persistent_cache_error = f"load failed: {exc}"[:500]

    def _persist_report_cache(self, payload: dict[str, Any]) -> None:
        if not self._cache_payload_is_substantial(payload):
            return
        path = REPORT_CACHE_PATH
        temp = path.with_name(path.name + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
            if len(text.encode("utf-8")) > REPORT_CACHE_MAX_BYTES:
                self._persistent_cache_error = "generated report exceeded persistent cache size limit"
                return
            temp.write_text(text, encoding="utf-8")
            temp.replace(path)
            self._persistent_cache_source_ms = int(path.stat().st_mtime * 1_000)
            self._persistent_cache_error = None
        except Exception as exc:
            self._persistent_cache_error = f"persist failed: {exc}"[:500]
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _report_worker(self) -> None:
        started = time.perf_counter()
        try:
            payload = self._build_full_report()
            generation_ms = (time.perf_counter() - started) * 1_000.0
            completed_ms = base._now_ms()
            # Make the fresh report visible before disk persistence, so a slow or
            # failing cache write can never delay Dashboard readiness.
            with self._report_lock:
                self._report_last_generation_ms = generation_ms
                self._report_last_completed_ms = completed_ms
                self._report_error = None
                self._report_cache = payload
                self._fresh_report_completed_this_run = True
            self._persist_report_cache(payload)
        except Exception as exc:
            with self._report_lock:
                self._report_error = str(exc)[:500]
        finally:
            with self._report_lock:
                self._report_building = False

    def _persistent_cache_diagnostics(self) -> dict[str, Any]:
        now_ms = base._now_ms()
        source_ms = self._persistent_cache_source_ms
        if self._fresh_report_completed_this_run:
            source = "FRESH_IN_PROCESS"
        elif self._persistent_cache_loaded:
            source = "PERSISTED_LAST_GOOD"
        elif self._report_cache is not None:
            source = "MEMORY_ONLY"
        else:
            source = "LIGHTWEIGHT_ONLY"
        return {
            "persistentReportCache": True,
            "persistentCacheLoadedAtBoot": self._persistent_cache_loaded,
            "persistentCacheFile": REPORT_CACHE_PATH.name,
            "persistentCacheSourceMs": source_ms,
            "persistentCacheAgeMs": now_ms - source_ms if source_ms is not None else None,
            "persistentCacheError": self._persistent_cache_error,
            "historicalReportSource": source,
            "coldStartDataAvailable": self._report_cache is not None,
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = VERSION
        diagnostics = payload.get("observerDiagnostics")
        merged = dict(diagnostics) if isinstance(diagnostics, dict) else {}
        merged.update(self._persistent_cache_diagnostics())
        payload["observerDiagnostics"] = merged
        payload["historicalReportSource"] = merged["historicalReportSource"]
        payload["coldStartDataAvailable"] = merged["coldStartDataAvailable"]
        return payload

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        payload["version"] = VERSION
        payload.update(self._persistent_cache_diagnostics())
        return payload


class _Handler(v4_16._Handler):
    observer: WalletShadowObserver


def main() -> int:
    observer = WalletShadowObserver()
    observer.start()
    handler = type("PredictWalletShadowV4_17Handler", (_Handler,), {"observer": observer})
    server = ThreadingHTTPServer((base.HOST, base.PORT), handler)
    print(
        f"Predict wallet shadow {VERSION} listening on http://{base.HOST}:{base.PORT}/state; "
        "persistentLastGoodReport=true; liveStateOverlay=true; paperOnly=true; liveOrdersAffected=false",
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
