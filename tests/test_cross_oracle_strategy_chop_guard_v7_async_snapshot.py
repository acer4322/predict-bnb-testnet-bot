from __future__ import annotations

import threading
import time

from predict_bot import cross_oracle_strategy_chop_guard_v6 as v6
from predict_bot import cross_oracle_strategy_chop_guard_v7 as v7


def _bare_engine() -> v7.CoverageQualifiedLeadLagPaperEngine:
    engine = object.__new__(v7.CoverageQualifiedLeadLagPaperEngine)
    engine.lock = threading.RLock()
    engine.runtime = {"status": "LIVE", "updatedAtMs": int(time.time() * 1000)}
    engine.last_flip = None

    engine._lead_cache = None
    engine._lead_cache_at_ms = 0
    engine._lead_ui_refresh_lock = threading.Lock()
    engine._lead_ui_refresh_running = False
    engine._lead_ui_refresh_error = None
    engine._lead_ui_refresh_started_at_ms = None
    engine._lead_ui_refresh_completed_at_ms = None
    engine._lead_ui_refresh_duration_ms = None
    engine._lead_ui_refresh_next_allowed_at_ms = 0

    engine._state_ui_refresh_lock = threading.Lock()
    engine._state_ui_cache = None
    engine._state_ui_cache_at_ms = 0
    engine._state_ui_refresh_running = False
    engine._state_ui_refresh_error = None
    engine._state_ui_refresh_started_at_ms = None
    engine._state_ui_refresh_completed_at_ms = None
    engine._state_ui_refresh_duration_ms = None
    engine._state_ui_refresh_next_allowed_at_ms = 0

    engine._next_lead_prune_at = time.monotonic() + 3600
    return engine


def _wait_until_lead_idle(engine: v7.CoverageQualifiedLeadLagPaperEngine) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        with engine._lead_ui_refresh_lock:
            if not engine._lead_ui_refresh_running:
                return
        time.sleep(0.005)
    raise AssertionError("lead UI analytics worker did not finish")


def _wait_until_state_idle(engine: v7.CoverageQualifiedLeadLagPaperEngine) -> None:
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        with engine._state_ui_refresh_lock:
            if not engine._state_ui_refresh_running:
                return
        time.sleep(0.005)
    raise AssertionError("state UI snapshot worker did not finish")


def test_stale_lead_snapshot_returns_immediately_while_background_refresh_runs(monkeypatch) -> None:
    engine = _bare_engine()
    now_ms = int(time.time() * 1000)
    engine._lead_cache = {
        "version": "old",
        "currentRegime": "POLY_LEADING",
        "windows": {},
        "recentMarkets": [],
    }
    engine._lead_cache_at_ms = now_ms - v7.LEAD_UI_ANALYTICS_CACHE_MS - 100

    started = threading.Event()
    release = threading.Event()

    def slow_refresh(self):
        started.set()
        assert release.wait(1.0)
        payload = {
            "version": "new",
            "currentRegime": "MIXED",
            "windows": {},
            "recentMarkets": [],
        }
        self._lead_cache = payload
        self._lead_cache_at_ms = int(time.time() * 1000)
        return dict(payload)

    monkeypatch.setattr(v6.LeadLagValidationPaperEngine, "_lead_validation_snapshot", slow_refresh)

    before = time.monotonic()
    payload = engine._lead_validation_snapshot()
    elapsed = time.monotonic() - before

    assert elapsed < 0.25
    assert payload["version"] == "old"
    assert payload["currentRegime"] == "POLY_LEADING"
    assert payload["uiAnalyticsRefresh"]["requestPathBlocksOnRefresh"] is False
    assert started.wait(0.5)

    release.set()
    _wait_until_lead_idle(engine)
    assert engine._lead_cache["version"] == "new"


def test_first_lead_snapshot_returns_building_without_waiting_for_analysis(monkeypatch) -> None:
    engine = _bare_engine()
    started = threading.Event()
    release = threading.Event()

    def slow_refresh(self):
        started.set()
        assert release.wait(1.0)
        payload = {
            "version": "built",
            "currentRegime": "MIXED",
            "windows": {},
            "recentMarkets": [],
        }
        self._lead_cache = payload
        self._lead_cache_at_ms = int(time.time() * 1000)
        return dict(payload)

    monkeypatch.setattr(v6.LeadLagValidationPaperEngine, "_lead_validation_snapshot", slow_refresh)

    before = time.monotonic()
    payload = engine._lead_validation_snapshot()
    elapsed = time.monotonic() - before

    assert elapsed < 0.25
    assert payload["currentRegime"] == "BUILDING"
    assert payload["recentMarkets"] == []
    assert payload["uiAnalyticsRefresh"]["requestPathBlocksOnRefresh"] is False
    assert started.wait(0.5)

    release.set()
    _wait_until_lead_idle(engine)
    assert engine._lead_cache["version"] == "built"


def test_evaluation_keeps_completed_ui_cache_even_when_it_is_old(monkeypatch) -> None:
    engine = _bare_engine()
    old_cache = {
        "version": "old",
        "currentRegime": "MIXED",
        "windows": {},
        "recentMarkets": [],
    }
    old_at_ms = int(time.time() * 1000) - 60_000
    engine._lead_cache = old_cache
    engine._lead_cache_at_ms = old_at_ms

    def invalidate(self):
        self._lead_cache = None

    monkeypatch.setattr(v6.LeadLagValidationPaperEngine, "_evaluate_once", invalidate)

    engine._evaluate_once()

    assert engine._lead_cache is old_cache
    assert engine._lead_cache_at_ms == old_at_ms


def test_first_full_state_snapshot_returns_immediately_while_background_build_runs(monkeypatch) -> None:
    engine = _bare_engine()
    started = threading.Event()
    release = threading.Event()

    def slow_build(self):
        started.set()
        assert release.wait(1.0)
        return {
            "status": "LIVE",
            "paperOnly": True,
            "runtime": {"status": "LIVE"},
            "summaries": {"marker": "built"},
        }

    monkeypatch.setattr(v7.CoverageQualifiedLeadLagPaperEngine, "_build_state_snapshot", slow_build)

    before = time.monotonic()
    payload = engine.snapshot()
    elapsed = time.monotonic() - before

    assert elapsed < 0.25
    assert payload["status"] == "LIVE"
    assert payload["summaries"] == {}
    assert payload["stateSnapshotRefresh"]["status"] == "BUILDING"
    assert payload["stateSnapshotRefresh"]["requestPathBlocksOnRefresh"] is False
    assert payload["stateSnapshotRefresh"]["databaseReadsOnRequestPath"] is False
    assert started.wait(0.5)

    release.set()
    _wait_until_state_idle(engine)
    assert engine._state_ui_cache is not None
    assert engine._state_ui_cache["summaries"]["marker"] == "built"


def test_stale_full_state_snapshot_is_served_while_background_rebuild_runs(monkeypatch) -> None:
    engine = _bare_engine()
    now_ms = int(time.time() * 1000)
    engine._state_ui_cache = {
        "status": "LIVE",
        "paperOnly": True,
        "runtime": {"status": "OLD"},
        "summaries": {"marker": "old"},
    }
    engine._state_ui_cache_at_ms = now_ms - v7.STATE_UI_SNAPSHOT_CACHE_MS - 100

    started = threading.Event()
    release = threading.Event()

    def slow_build(self):
        started.set()
        assert release.wait(1.0)
        return {
            "status": "LIVE",
            "paperOnly": True,
            "runtime": {"status": "NEW"},
            "summaries": {"marker": "new"},
        }

    monkeypatch.setattr(v7.CoverageQualifiedLeadLagPaperEngine, "_build_state_snapshot", slow_build)

    before = time.monotonic()
    payload = engine.snapshot()
    elapsed = time.monotonic() - before

    assert elapsed < 0.25
    assert payload["summaries"]["marker"] == "old"
    assert payload["runtime"]["status"] == "LIVE"
    assert payload["stateSnapshotRefresh"]["status"] == "REFRESHING"
    assert payload["stateSnapshotRefresh"]["requestPathBlocksOnRefresh"] is False
    assert payload["stateSnapshotRefresh"]["databaseReadsOnRequestPath"] is False
    assert started.wait(0.5)

    release.set()
    _wait_until_state_idle(engine)
    assert engine._state_ui_cache is not None
    assert engine._state_ui_cache["summaries"]["marker"] == "new"
