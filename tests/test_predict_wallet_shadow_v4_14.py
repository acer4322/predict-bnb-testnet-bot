from __future__ import annotations

import threading
import time

from predict_bot.predict_wallet_shadow_observer_v4_14 import VERSION, WalletShadowObserver


def close_observer(observer: WalletShadowObserver) -> None:
    thread = observer._report_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=2)
    for timer in observer.mirror_timers:
        timer.cancel()
    observer.http.close()
    observer.local_http.close()
    observer.db.close()


def test_v4_14_state_is_nonblocking_and_single_flight(tmp_path, monkeypatch) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    gate = threading.Event()
    entered = threading.Event()
    calls = 0

    def slow_report() -> dict:
        nonlocal calls
        calls += 1
        entered.set()
        assert gate.wait(timeout=2)
        return {"version": "old", "status": "LIVE", "probe": "full", "observerDiagnostics": {}}

    monkeypatch.setattr(observer, "_build_full_report", slow_report)
    observer.latest_public_signal_snapshot = {"seconds_left": 180.0}
    try:
        started = time.perf_counter()
        first = observer.snapshot()
        assert time.perf_counter() - started < 0.5
        assert first["version"] == VERSION
        assert first["reportStatus"] == "BUILDING_FIRST_REPORT"
        assert entered.wait(timeout=1)

        for _ in range(8):
            started = time.perf_counter()
            state = observer.snapshot()
            assert time.perf_counter() - started < 0.5
            assert state["reportStatus"] == "BUILDING_FIRST_REPORT"
        assert calls == 1

        started = time.perf_counter()
        health = observer.health_snapshot()
        assert time.perf_counter() - started < 0.5
        assert health["version"] == VERSION
        assert health["reportStatus"] == "BUILDING_FIRST_REPORT"

        gate.set()
        assert observer._report_thread is not None
        observer._report_thread.join(timeout=2)
        assert not observer._report_thread.is_alive()

        state = observer.snapshot()
        assert state["probe"] == "full"
        assert state["reportStatus"] == "READY"
        assert state["observerDiagnostics"]["nonBlockingState"] is True
        assert state["observerDiagnostics"]["reportBuildCount"] == 1
    finally:
        gate.set()
        close_observer(observer)


def test_v4_14_defers_heavy_report_near_market_open(tmp_path, monkeypatch) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    calls = 0

    def report() -> dict:
        nonlocal calls
        calls += 1
        return {"version": VERSION, "status": "LIVE"}

    monkeypatch.setattr(observer, "_build_full_report", report)
    observer.latest_public_signal_snapshot = {"seconds_left": 295.0}
    try:
        started = time.perf_counter()
        state = observer.snapshot()
        assert time.perf_counter() - started < 0.5
        assert calls == 0
        assert state["reportStatus"] == "WAITING_SAFE_WINDOW"
        assert state["observerDiagnostics"]["reportBuildSafeNow"] is False
    finally:
        close_observer(observer)
