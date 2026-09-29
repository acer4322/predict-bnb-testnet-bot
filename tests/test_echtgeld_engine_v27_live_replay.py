from __future__ import annotations

import sqlite3

from predict_bot import echtgeld_engine_v27 as v27
from predict_bot import echtgeld_engine_v26 as v26


class FakeExecutor:
    def __init__(self, config) -> None: self.config = config
    def close(self) -> None: pass
    def available_balance_snapshot(self): return {"status": "OK", "availableUsdt": 100.0}


def make_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(v27, "REPLAY_DB", tmp_path / "replay.db")
    monkeypatch.setattr(v27, "CONTROLLER_DB", tmp_path / "controller.db")
    engine = v27.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "settlement.db",
        target_official_db_path=tmp_path / "official.db",
    )
    return engine


def test_replay_db_is_separate_and_records_operator_and_engine_events(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch)
    try:
        engine.select_entry_source(v26.CAP100_SOURCE)
        engine.cap100_heartbeat({"entrySource": v26.CAP100_SOURCE, "marketId": 1001})
        state = engine.resume()
        assert state["nextMarketArmGate"]["waitingNextMarket"] is True
        engine._cap100_event("TEST_REAL_EVENT", detail="test")
        replay = engine.replay_state()
        assert replay["enabled"] is True
        assert replay["syntheticDataIncluded"] is False
        events = engine.replay_events()
        kinds = [x["event_type"] for x in events]
        assert "OPERATOR_RESUME" in kinds
        assert "ENGINE_TEST_REAL_EVENT" in kinds
        assert all("STRESS" not in k and "DRILL" not in k for k in kinds)
    finally:
        engine.close()


def test_intent_block_is_recorded_with_join_keys(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch)
    try:
        engine.select_entry_source(v26.CAP100_SOURCE)
        engine.cap100_heartbeat({"entrySource": v26.CAP100_SOURCE, "marketId": 2001})
        engine.resume()
        payload = {
            "entrySource": v26.CAP100_SOURCE,
            "clientOrderId": "CID-1",
            "decisionId": "D-1",
            "marketId": 2001,
            "bucketStartSec": 100,
            "windowEndMs": 400000,
            "asset": "BTC", "side": "UP", "price": 0.4, "shares": 18,
        }
        try:
            engine.submit_cap100_maker(payload)
        except Exception:
            pass
        rows = [x for x in engine.replay_events() if x["event_type"] in {"MAKER_INTENT_RECEIVED", "MAKER_INTENT_BLOCKED"}]
        assert len(rows) == 2
        assert rows[0]["decision_id"] == "D-1"
        assert rows[0]["client_order_id"] == "CID-1"
        assert rows[0]["market_id"] == 2001
    finally:
        engine.close()


def test_replay_meta_declares_training_join_boundary(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch)
    try:
        db = sqlite3.connect(tmp_path / "replay.db")
        value = db.execute("SELECT value FROM replay_meta WHERE key='dataset'").fetchone()[0]
        assert "CAP100_REAL_ECHTGELD_REPLAY" in value
        assert '"syntheticDataIncluded":false' in value
        assert '"targetDataIncluded":false' in value
    finally:
        engine.close()
