from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from . import predict_wallet_shadow_observer as base
from . import predict_wallet_shadow_observer_v4_14 as v4_14


REPORT_CACHE_PATH = Path(
    os.environ.get(
        "PREDICT_WALLET_SHADOW_REPORT_CACHE",
        base.ROOT / "data" / "wallet-shadow-last-good-state.json",
    )
)
REPORT_CACHE_MAX_BYTES = 64 * 1024 * 1024


def _substantial(payload: Any) -> bool:
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


def install() -> None:
    cls = v4_14.WalletShadowObserver
    if getattr(cls, "_persistent_cache_patch_installed", False):
        return

    original_init = cls.__init__
    original_snapshot = cls.snapshot
    original_health_snapshot = cls.health_snapshot

    def load_cache(self: Any) -> None:
        path = REPORT_CACHE_PATH
        try:
            if not path.exists():
                return
            stat = path.stat()
            if stat.st_size <= 0 or stat.st_size > REPORT_CACHE_MAX_BYTES:
                self._persistent_cache_error = f"cache size rejected: {stat.st_size} bytes"
                return
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not _substantial(payload):
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

    def persist_cache(self: Any, payload: dict[str, Any]) -> None:
        if not _substantial(payload):
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

    def diagnostics(self: Any) -> dict[str, Any]:
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

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        self._persistent_cache_loaded = False
        self._persistent_cache_loaded_at_ms = None
        self._persistent_cache_source_ms = None
        self._persistent_cache_error = None
        self._fresh_report_completed_this_run = False
        original_init(self, *args, **kwargs)
        load_cache(self)

    def patched_report_worker(self: Any) -> None:
        started = time.perf_counter()
        try:
            payload = self._build_full_report()
            generation_ms = (time.perf_counter() - started) * 1_000.0
            completed_ms = base._now_ms()
            # Publish to memory first; disk persistence is never allowed to hold
            # the Dashboard behind the already-expensive full report.
            with self._report_lock:
                self._report_last_generation_ms = generation_ms
                self._report_last_completed_ms = completed_ms
                self._report_error = None
                self._report_cache = payload
                self._fresh_report_completed_this_run = True
            persist_cache(self, payload)
        except Exception as exc:
            with self._report_lock:
                self._report_error = str(exc)[:500]
        finally:
            with self._report_lock:
                self._report_building = False

    def patched_snapshot(self: Any) -> dict[str, Any]:
        payload = original_snapshot(self)
        current = payload.get("observerDiagnostics")
        merged = dict(current) if isinstance(current, dict) else {}
        merged.update(diagnostics(self))
        payload["observerDiagnostics"] = merged
        payload["historicalReportSource"] = merged["historicalReportSource"]
        payload["coldStartDataAvailable"] = merged["coldStartDataAvailable"]
        return payload

    def patched_health_snapshot(self: Any) -> dict[str, Any]:
        payload = original_health_snapshot(self)
        payload.update(diagnostics(self))
        return payload

    cls.__init__ = patched_init
    cls._report_worker = patched_report_worker
    cls.snapshot = patched_snapshot
    cls.health_snapshot = patched_health_snapshot
    cls._load_persistent_report_cache = load_cache
    cls._persist_report_cache = persist_cache
    cls._persistent_cache_diagnostics = diagnostics
    cls._persistent_cache_patch_installed = True
