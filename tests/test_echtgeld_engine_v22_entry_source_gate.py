from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from predict_bot import echtgeld_engine_v1 as v1
from predict_bot import echtgeld_engine_v22 as v22


class _FakeExecutor:
    executions: list[dict] = []

    def __init__(self, config) -> None:
        self.config = config

    def close(self) -> None:
        return

    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0, "source": "test"}

    def execute(self, **kwargs) -> dict:
        self.executions.append(dict(kwargs))
        return {
            "status": "SUBMITTED",
            "completedAtMs": int(time.time() * 1000),
            "executionPrice": 0.5,
            "shares": 2.0,
            "submittedUsdt": 1.0,
            "vendorOrderId": "test-order",
        }


def _official_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE target_markets(
               market_id INTEGER PRIMARY KEY,
               status TEXT,
               winner TEXT,
               resolved_at_ms INTEGER
           )"""
    )
    db.commit()
    db.close()


def _engine(tmp_path: Path) -> v22.EchtgeldEngine:
    official = tmp_path / "target_wallet_official.db"
    _official_db(official)
    _FakeExecutor.executions.clear()
    return v22.EchtgeldEngine(
        tmp_path / "echtgeld.db",
        executor_factory=_FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "retired-wallet-shadow.db",
        target_official_db_path=official,
    )


def _intent(source: str | None = None, *, intent_id: str = "cap100-1") -> dict:
    now = int(time.time() * 1000)
    payload = {
        "intentId": intent_id,
        "dedupeKey": intent_id,
        "strategy": "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD",
        "cohort": "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY",
        "signalId": intent_id,
        "marketId": 1234567,
        "createdAtMs": now,
        "side": "UP",
        "signalAsk": 0.5,
        "decision": {"decision": "TRADE", "side": "UP", "ask": 0.5},
        "snapshot": {"market_id": 1234567},
    }
    if source is not None:
        payload["entrySource"] = source
    return payload


def test_source_gate_defaults_to_deny_all_and_resume_fails_closed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        gate = engine.entry_source_state()
        assert gate["selectedSourceId"] is None
        assert gate["entryAdmissionReady"] is False
        with pytest.raises(v1.EchtgeldEngineError, match="no live entry source selected"):
            engine.resume()

        result = engine.submit_intent(_intent())
        assert result["accepted"] is False
        assert result["status"] == "IGNORED_SOURCE_DISABLED"
        assert engine.db.execute("SELECT COUNT(*) FROM engine_intents").fetchone()[0] == 0
    finally:
        engine.close()


def test_only_selected_source_can_queue_and_source_change_requires_pause(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        gate = engine.select_entry_source("CAP100_8787")
        assert gate["selectedSourceId"] == "CAP100_8787"
        engine.resume()

        wrong = engine.submit_intent(_intent("OLD_TARGET_TAKER", intent_id="wrong"))
        assert wrong["accepted"] is False
        assert wrong["status"] == "IGNORED_SOURCE_DISABLED"

        good = engine.submit_intent(_intent("cap100_8787", intent_id="good"))
        assert good["accepted"] is True
        assert good["status"] == "QUEUED"

        with pytest.raises(v1.EchtgeldEngineError, match="Pause Echtgeld"):
            engine.select_entry_source("OTHER")
    finally:
        engine.close()


def test_worker_rechecks_source_before_any_venue_write(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    try:
        engine.select_entry_source("CAP100_8787")
        engine.resume()
        result = engine.submit_intent(_intent("CAP100_8787", intent_id="queued"))
        assert result["status"] == "QUEUED"

        # Operator pause + source clear happens before the worker receives the
        # queued item.  Source fencing must win before any venue execution.
        engine.pause("test")
        engine.select_entry_source(None)
        engine._process_intent("queued")

        row = engine.db.execute(
            "SELECT status FROM engine_intents WHERE intent_id='queued'"
        ).fetchone()
        assert row["status"] == "ABORTED_SOURCE_DISABLED"
        assert _FakeExecutor.executions == []
        assert engine.db.execute("SELECT COUNT(*) FROM engine_orders").fetchone()[0] == 0
    finally:
        engine.close()


def test_source_selection_is_cleared_on_restart(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    db_path = engine.db_path
    official = engine.target_official_db_path
    engine.select_entry_source("CAP100_8787")
    engine.close()

    restarted = v22.EchtgeldEngine(
        db_path,
        executor_factory=_FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "retired-wallet-shadow.db",
        target_official_db_path=official,
    )
    try:
        assert restarted.entry_source_state()["selectedSourceId"] is None
        assert restarted.armed is False
    finally:
        restarted.close()
