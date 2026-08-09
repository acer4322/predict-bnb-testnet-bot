from __future__ import annotations

from pathlib import Path

import pytest

from predict_bot import poly_gap_live as live_base
from predict_bot.poly_gap_live_v22 import PreflightOrderedSharedMarketPolyGapLiveEngine
from predict_bot.poly_gap_live_v23 import (
    MAX_ENTRY_DELAY_SECONDS,
    MIN_ENTRY_DELAY_SECONDS,
    DelayedEntryPolyGapLiveEngine,
)


def _seed_current_market(engine: DelayedEntryPolyGapLiveEngine, start_ms: int) -> None:
    with engine.lock:
        engine.market_cache = {
            "market_id": 7001,
            "topic_id": 8001,
            "up_token_id": "up-token",
            "down_token_id": "down-token",
            "fee_rate_bps": 200,
            "start_ms": start_ms,
            "end_ms": start_ms + 300_000,
        }


def test_v23_default_entry_delay_is_at_least_ten_seconds(tmp_path: Path) -> None:
    engine = DelayedEntryPolyGapLiveEngine(tmp_path / "v23-default.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V23"
        assert state["settings"]["entryDelaySeconds"] >= 10.0
        assert state["entryDelay"]["minimumAllowedSeconds"] == MIN_ENTRY_DELAY_SECONDS
        assert state["entryDelay"]["maximumAllowedSeconds"] == MAX_ENTRY_DELAY_SECONDS
        assert state["entryDelay"]["activePositionExitNeverDelayed"] is True
    finally:
        engine.stop()


def test_v23_entry_delay_setting_persists_and_rejects_below_minimum(tmp_path: Path) -> None:
    db_path = tmp_path / "v23-setting.db"
    engine = DelayedEntryPolyGapLiveEngine(db_path)
    try:
        with pytest.raises(ValueError, match="between 10 and 240"):
            engine.update_settings({"entryDelaySeconds": 9})

        state = engine.update_settings({"entryDelaySeconds": 25})
        assert state["settings"]["entryDelaySeconds"] == 25.0
    finally:
        engine.stop()

    reopened = DelayedEntryPolyGapLiveEngine(db_path)
    try:
        assert reopened.snapshot()["settings"]["entryDelaySeconds"] == 25.0
    finally:
        reopened.stop()


def test_v23_rejects_entire_settings_payload_before_partial_delay_write(tmp_path: Path) -> None:
    engine = DelayedEntryPolyGapLiveEngine(tmp_path / "v23-atomic-validation.db")
    try:
        before = engine.snapshot()["settings"]["entryDelaySeconds"]
        with pytest.raises(ValueError, match="unsupported settings"):
            engine.update_settings({"entryDelaySeconds": 30, "notASetting": True})
        after = engine.snapshot()["settings"]["entryDelaySeconds"]
        assert after == before
    finally:
        engine.stop()


def test_v23_market_open_delay_counts_from_exact_market_start(tmp_path: Path, monkeypatch) -> None:
    engine = DelayedEntryPolyGapLiveEngine(tmp_path / "v23-clock.db")
    try:
        start_ms = 1_800_000_000_000
        engine._set_setting("entry_delay_seconds", "10")
        _seed_current_market(engine, start_ms)

        monkeypatch.setattr(live_base, "_now_ms", lambda: start_ms + 9_999)
        waiting = engine._entry_delay_state()
        assert waiting["identityCurrent"] is True
        assert waiting["ready"] is False
        assert waiting["remainingMs"] == 1

        monkeypatch.setattr(live_base, "_now_ms", lambda: start_ms + 10_000)
        ready = engine._entry_delay_state()
        assert ready["ready"] is True
        assert ready["remainingMs"] == 0
    finally:
        engine.stop()


def test_v23_blocks_new_buy_during_delay_but_keeps_execution_preflight_warm(
    tmp_path: Path, monkeypatch
) -> None:
    engine = DelayedEntryPolyGapLiveEngine(tmp_path / "v23-gate.db")
    try:
        start_ms = 1_800_000_000_000
        monkeypatch.setattr(live_base, "_now_ms", lambda: start_ms + 5_000)
        monkeypatch.setattr(live_base, "MASTER_ENABLED", True)
        engine._set_setting("runtime_enabled", "1")
        engine._set_setting("entry_delay_seconds", "10")
        _seed_current_market(engine, start_ms)
        monkeypatch.setattr(engine, "_preload_shared_market_identity", lambda: dict(engine.market_cache or {}))

        preflight_calls: list[bool] = []

        def fake_ensure_clients() -> bool:
            preflight_calls.append(True)
            engine.execution_preflight_ready = True
            return True

        monkeypatch.setattr(engine, "_ensure_clients", fake_ensure_clients)

        parent_calls: list[bool] = []

        def fake_parent_tick(self: PreflightOrderedSharedMarketPolyGapLiveEngine) -> None:
            parent_calls.append(True)

        monkeypatch.setattr(PreflightOrderedSharedMarketPolyGapLiveEngine, "_tick", fake_parent_tick)

        engine._tick()

        assert engine.status == "WAITING_ENTRY_DELAY"
        assert preflight_calls == [True]
        assert parent_calls == []
        assert engine._current_active_round() is None
    finally:
        engine.stop()


def test_v23_active_round_bypasses_entry_delay_gate(tmp_path: Path, monkeypatch) -> None:
    engine = DelayedEntryPolyGapLiveEngine(tmp_path / "v23-active.db")
    try:
        monkeypatch.setattr(
            engine,
            "_current_active_round",
            lambda: {"id": 1, "state": "OPEN", "market_id": 7001},
        )
        parent_calls: list[bool] = []

        def fake_parent_tick(self: PreflightOrderedSharedMarketPolyGapLiveEngine) -> None:
            parent_calls.append(True)

        monkeypatch.setattr(PreflightOrderedSharedMarketPolyGapLiveEngine, "_tick", fake_parent_tick)
        engine._tick()
        assert parent_calls == [True]
    finally:
        engine.stop()


def test_supervisor_launches_v23() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v23" in source
    assert "predict_bot.poly_gap_live_v22" in source
