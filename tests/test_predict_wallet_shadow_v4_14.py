from __future__ import annotations

import threading
import time

import pytest

from predict_bot import predict_wallet_maker_inventory_taker_strategy as shared_strategy
from predict_bot.predict_wallet_shadow_observer_v4_14 import (
    TARGET_CORE_V2_COHORT,
    VERSION,
    WalletShadowObserver,
)


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


def test_target_core_v2_cuts_maker_slots_without_changing_taker_policy(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    try:
        assert TARGET_CORE_V2_COHORT in observer.shared_states
        v1 = next(item for item in shared_strategy.COHORTS if item["cohort"] == "TARGET_CORE_INTEGRATED_V1")
        v2 = next(item for item in shared_strategy.COHORTS if item["cohort"] == TARGET_CORE_V2_COHORT)

        balanced = shared_strategy.inventory_state(maker_up_shares=180, maker_down_shares=180)
        sample = {"seconds_left": 180.0}
        v1_plan = shared_strategy.depth_plan(v1, sample, balanced)
        v2_plan = shared_strategy.depth_plan(v2, sample, balanced)
        assert (v1_plan["upLevels"], v1_plan["downLevels"]) == (15, 15)
        assert (v2_plan["upLevels"], v2_plan["downLevels"]) == (5, 5)

        soft = shared_strategy.inventory_state(maker_up_shares=234, maker_down_shares=180)
        v2_soft = shared_strategy.depth_plan(v2, sample, soft)
        assert (v2_soft["upLevels"], v2_soft["downLevels"]) == (2, 5)

        policy = shared_strategy.policy(v2)["targetCoreIntegrated"]
        assert policy["maker"]["levelsPerSide"] == 5
        assert policy["allocationExperiment"]["makerQuoteSlotReduction"] == pytest.approx(2 / 3)
        assert policy["allocationExperiment"]["takerSignalSameAsV1"] is True
        assert policy["allocationExperiment"]["takerSizingSameAsV1"] is True
    finally:
        close_observer(observer)


def test_cold_state_exposes_target_core_v1_and_v2_before_full_report(tmp_path) -> None:
    observer = WalletShadowObserver(tmp_path / "shadow.db", tmp_path / "missing.db")
    observer.latest_public_signal_snapshot = {"seconds_left": 295.0}
    try:
        state = observer.snapshot()
        variants = state["makerInventoryTakerSharedLab"]["variants"]
        cohorts = {item["cohort"] for item in variants}
        assert "TARGET_CORE_INTEGRATED_V1" in cohorts
        assert TARGET_CORE_V2_COHORT in cohorts
        v2 = next(item for item in variants if item["cohort"] == TARGET_CORE_V2_COHORT)
        assert v2["performance"] == {}
        assert v2["policy"]["targetCoreIntegrated"]["maker"]["levelsPerSide"] == 5
        assert state["reportStatus"] == "WAITING_SAFE_WINDOW"
    finally:
        close_observer(observer)
