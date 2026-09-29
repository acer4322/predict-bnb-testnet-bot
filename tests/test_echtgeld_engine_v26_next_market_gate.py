from __future__ import annotations

import pytest

from predict_bot import echtgeld_engine_v26 as v26


class FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config
    def close(self) -> None:
        pass
    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0}
    def execute(self, **_kwargs):
        raise AssertionError("generic executor not expected")


def make_engine(tmp_path):
    engine = v26.EchtgeldEngine(
        tmp_path / "echtgeld.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "none.db",
        target_official_db_path=tmp_path / "none2.db",
    )
    engine.select_entry_source(v26.CAP100_SOURCE)
    engine.cap100_heartbeat({
        "entrySource": v26.CAP100_SOURCE,
        "marketId": 1513000,
        "bucketStartSec": 2000,
        "windowEndMs": 2300000,
    })
    return engine


def payload(market_id: int, bucket: int):
    return {
        "entrySource": v26.CAP100_SOURCE,
        "marketId": market_id,
        "bucketStartSec": bucket,
        "windowEndMs": (bucket + 300) * 1000,
    }


def test_resume_enters_wait_next_market_and_blocks_anchor(tmp_path):
    engine = make_engine(tmp_path)
    try:
        state = engine.resume()
        gate = state["nextMarketArmGate"]
        assert gate["waitingNextMarket"] is True
        assert gate["activationMarketId"] == 1513000
        with pytest.raises(v26.v1.EchtgeldEngineError, match="ARMED_WAIT_NEXT_MARKET"):
            engine._enforce_next_market_gate(payload(1513000, 2000))
        gate = engine.next_market_arm_state()
        assert gate["waitingNextMarket"] is True
        assert gate["liveMarketId"] is None
    finally:
        engine.close()


def test_first_next_market_unlocks_and_stale_anchor_remains_blocked(tmp_path):
    engine = make_engine(tmp_path)
    try:
        engine.resume()
        engine._enforce_next_market_gate(payload(1513001, 2300))
        gate = engine.next_market_arm_state()
        assert gate["waitingNextMarket"] is False
        assert gate["liveMarketId"] == 1513001
        engine._enforce_next_market_gate(payload(1513001, 2300))
        with pytest.raises(v26.v1.EchtgeldEngineError, match="stale/out-of-order"):
            engine._enforce_next_market_gate(payload(1513000, 2000))
    finally:
        engine.close()


def test_pause_clears_activation_gate(tmp_path):
    engine = make_engine(tmp_path)
    try:
        engine.resume()
        engine.pause("test")
        gate = engine.next_market_arm_state()
        assert gate["armed"] is False
        assert gate["waitingNextMarket"] is False
        assert gate["activationMarketId"] is None
        assert gate["liveMarketId"] is None
    finally:
        engine.close()
