from __future__ import annotations

import time

import pytest

from predict_bot import echtgeld_engine_v1 as v1
from predict_bot import echtgeld_engine_v23 as v23
from predict_bot import echtgeld_engine_v27 as v27
from predict_bot import echtgeld_engine_v29 as v29


class FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config

    def close(self) -> None:
        pass

    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0}


def heartbeat(market_id: int, bucket: int) -> dict:
    return {
        "entrySource": v23.R2_R21_SOURCE,
        "controllerVersion": "TEST_R2_R21_V33",
        "marketId": market_id,
        "bucketStartSec": bucket,
        "windowEndMs": (bucket + 300) * 1000,
    }


def make_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(v27, "REPLAY_DB", tmp_path / "replay.db")
    monkeypatch.setattr(v27, "CONTROLLER_DB", tmp_path / "controller.db")
    monkeypatch.setattr(v27, "R2_R21_CONTROLLER_DB", tmp_path / "r2-r21-controller.db")
    engine = v29.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "settlement.db",
        target_official_db_path=tmp_path / "official.db",
    )
    engine.select_entry_source(v23.R2_R21_SOURCE)
    engine.cap100_heartbeat(heartbeat(1001, 2_000))
    return engine


def test_one_market_run_waits_anchor_activates_next_and_auto_pauses(tmp_path, monkeypatch) -> None:
    engine = make_engine(tmp_path, monkeypatch)
    try:
        state = engine.resume_next_market_once()
        assert state["runtimeStatus"] == "ARMED_WAIT_NEXT_MARKET"
        assert state["singleMarketRun"]["status"] == "WAITING_NEXT_MARKET"
        assert state["nextMarketArmGate"]["activationMarketId"] == 1001
        assert state["strategyExecution"]["sourceId"] == v23.R2_R21_SOURCE
        assert state["strategyExecution"]["displayName"] == "R2 + R2.1 V3.3"
        assert state["strategyExecution"]["executionVenue"] == "BINANCE_PREDICTION"
        assert state["strategyExecution"]["executionApiBase"] == "https://api.binance.com"
        assert state["strategyExecution"]["marketVendor"] == "PREDICT_FUN"
        health = engine.health()
        assert health["strategyExecutionVenue"] == "BINANCE_PREDICTION"
        assert health["strategyExecutionApiBase"] == "https://api.binance.com"
        assert health["strategyMarketVendor"] == "PREDICT_FUN"

        engine.cap100_heartbeat(heartbeat(1001, 2_000))
        assert engine.single_market_run_state()["targetMarketId"] is None
        assert engine.armed is True

        engine.cap100_heartbeat(heartbeat(1002, 2_300))
        running = engine.single_market_run_state()
        assert running["status"] == "RUNNING"
        assert running["targetMarketId"] == 1002
        assert engine.next_market_arm_state()["liveMarketId"] == 1002

        engine._enforce_single_market_run_gate(heartbeat(1002, 2_300))
        engine.cap100_heartbeat(heartbeat(1003, 2_600))
        completed = engine.single_market_run_state()
        assert engine.armed is False
        assert completed["status"] == "COMPLETED"
        assert completed["lastCompletedMarketId"] == 1002
        assert completed["targetMarketId"] == 1002
        assert engine._cap100_rows(active_only=False) == []
    finally:
        engine.close()


def test_cap100_order_projection_separates_binance_venue_from_market_vendor(tmp_path, monkeypatch) -> None:
    engine = make_engine(tmp_path, monkeypatch)
    try:
        payload = heartbeat(1001, 2_000) | {
            "clientOrderId": "R2-VENUE-PROJECTION",
            "side": "UP",
            "price": 0.40,
            "shares": 10.0,
        }
        market = {
            "source_market_id": 1001,
            "market_id": 7001,
            "bucket_start_sec": 2_000,
            "end_ms": 2_300_000,
            "up_token_id": "UPTOKEN",
            "down_token_id": "DOWNTOKEN",
            "fee_rate_bps": 200,
        }
        engine._cap100_insert(payload, "MAKER", market, "R2-VENUE-PROJECTION", "UP", 10.0, 0.40)
        row = engine._cap100_order_projection(1)[0]
        assert row["venue"] == "binance"
        assert row["executionVenue"] == "BINANCE_PREDICTION"
        assert row["executionApiBase"] == "https://api.binance.com"
        assert row["marketVendor"] == "PREDICT_FUN"
        assert row["result"]["executionVenue"] == "BINANCE_PREDICTION"
        assert row["context"]["marketVendor"] == "PREDICT_FUN"
    finally:
        engine.close()


def test_second_market_entry_is_blocked_and_pauses_before_venue_path(tmp_path, monkeypatch) -> None:
    engine = make_engine(tmp_path, monkeypatch)
    try:
        engine.resume_next_market_once()
        engine._enforce_single_market_run_gate(heartbeat(1002, 2_300))
        assert engine.single_market_run_state()["targetMarketId"] == 1002

        with pytest.raises(v1.EchtgeldEngineError, match="second-market entry"):
            engine._enforce_single_market_run_gate(heartbeat(1003, 2_600))
        deadline = time.monotonic() + 2.0
        while engine.single_market_run_state()["status"] == "COMPLETING" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert engine.armed is False
        assert engine.single_market_run_state()["status"] == "COMPLETED"
        assert engine._cap100_rows(active_only=False) == []
    finally:
        engine.close()


def test_second_market_rejection_returns_before_slow_pause_finishes(tmp_path, monkeypatch) -> None:
    engine = make_engine(tmp_path, monkeypatch)
    try:
        engine.resume_next_market_once()
        engine._enforce_single_market_run_gate(heartbeat(1002, 2_300))
        original_pause = v27.EchtgeldEngine.pause

        def slow_pause(self, reason="operator"):
            time.sleep(0.25)
            return original_pause(self, reason)

        monkeypatch.setattr(v27.EchtgeldEngine, "pause", slow_pause)
        started = time.monotonic()
        with pytest.raises(v1.EchtgeldEngineError, match="second-market entry"):
            engine._enforce_single_market_run_gate(heartbeat(1003, 2_600))
        assert time.monotonic() - started < 0.15
        assert engine.single_market_run_state()["status"] == "COMPLETING"

        thread = engine.single_market_completion_thread
        assert thread is not None
        thread.join(timeout=2.0)
        assert engine.armed is False
        assert engine.single_market_run_state()["status"] == "COMPLETED"
        assert engine._cap100_rows(active_only=False) == []
    finally:
        engine.close()


def test_delayed_anchor_request_does_not_end_selected_market(tmp_path, monkeypatch) -> None:
    engine = make_engine(tmp_path, monkeypatch)
    try:
        engine.resume_next_market_once()
        engine.cap100_heartbeat(heartbeat(1002, 2_300))
        with pytest.raises(v1.EchtgeldEngineError, match="stale/out-of-order"):
            engine._enforce_single_market_run_gate(heartbeat(1001, 2_000))
        assert engine.armed is True
        assert engine.single_market_run_state()["status"] == "RUNNING"
    finally:
        engine.close()


def test_ordinary_resume_keeps_existing_continuous_after_next_market_contract(tmp_path, monkeypatch) -> None:
    engine = make_engine(tmp_path, monkeypatch)
    try:
        engine.resume()
        assert engine.single_market_run_state()["status"] == "IDLE"
        engine._enforce_next_market_gate(heartbeat(1002, 2_300))
        engine._enforce_next_market_gate(heartbeat(1003, 2_600))
        assert engine.armed is True
        assert engine.next_market_arm_state()["liveMarketId"] == 1003
    finally:
        engine.close()
