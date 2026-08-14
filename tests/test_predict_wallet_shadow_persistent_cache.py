from __future__ import annotations

import json

from predict_bot import predict_wallet_shadow_persistent_cache as persistent_cache
from predict_bot.predict_wallet_shadow_observer_v4_16 import VERSION, WalletShadowObserver


def close_observer(observer: WalletShadowObserver) -> None:
    thread = observer._report_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)
    for timer in observer.mirror_timers:
        timer.cancel()
    observer.http.close()
    observer.local_http.close()
    observer.db.close()


def test_v4_16_loads_raw_state_envelope_as_warm_cache(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "wallet-shadow-last-good-state.json"
    cache.write_text(
        json.dumps(
            {
                "state": {
                    "version": "OLD",
                    "status": "LIVE",
                    "probe": "persisted-history",
                    "makerInventoryTakerSharedLab": {"variants": []},
                    "observerDiagnostics": {"old": True},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(persistent_cache, "REPORT_CACHE_PATH", cache)

    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    observer.latest_public_signal_snapshot = {"seconds_left": 120.0}
    try:
        state = observer.snapshot()
        assert state["version"] == VERSION
        assert state["probe"] == "persisted-history"
        assert state["coldStartDataAvailable"] is True
        assert state["historicalReportSource"] == "PERSISTED_LAST_GOOD"
        assert state["observerDiagnostics"]["persistentCacheLoadedAtBoot"] is True
        assert state["observerDiagnostics"]["persistentCacheError"] is None
        assert state.get("reportOnlyState") is not True
    finally:
        close_observer(observer)


def test_v4_16_persists_successful_full_report_without_blocking_memory_publish(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "wallet-shadow-last-good-state.json"
    monkeypatch.setattr(persistent_cache, "REPORT_CACHE_PATH", cache)
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    payload = {
        "version": "FULL",
        "status": "LIVE",
        "probe": "fresh-history",
        "makerInventoryTakerSharedLab": {"variants": []},
    }
    monkeypatch.setattr(observer, "_build_full_report", lambda: dict(payload))
    try:
        observer._report_worker()
        assert observer._report_cache is not None
        assert observer._report_cache["probe"] == "fresh-history"
        assert observer._fresh_report_completed_this_run is True
        assert cache.exists()
        stored = json.loads(cache.read_text(encoding="utf-8"))
        assert stored["probe"] == "fresh-history"
        health = observer.health_snapshot()
        assert health["historicalReportSource"] == "FRESH_IN_PROCESS"
        assert health["coldStartDataAvailable"] is True
    finally:
        close_observer(observer)
