from __future__ import annotations

import sqlite3

import pytest

import predict_bot.poly_gap_live_v27 as v27
from predict_bot.poly_gap_live_v27 import LocalDbPaperGuardPolyGapLiveEngine


def _db(path, *, paused: int, heartbeat_ms: int, reversals: int = 0, status: str = "ACTIVE"):
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE poly_chop_guard_state (
            id INTEGER PRIMARY KEY,
            paused INTEGER NOT NULL,
            changed_at_ms INTEGER NOT NULL,
            reason TEXT
        );
        CREATE TABLE poly_chop_guard_heartbeat (
            id INTEGER PRIMARY KEY,
            healthy_at_ms INTEGER NOT NULL
        );
        CREATE TABLE poly_chop_guard_markets (
            market_id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            confirmed_reversals INTEGER NOT NULL,
            distinct_receipts INTEGER NOT NULL,
            evaluable INTEGER NOT NULL,
            last_seen_at_ms INTEGER NOT NULL,
            finalized_at_ms INTEGER,
            reason TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO poly_chop_guard_state VALUES(1,?,?,?)",
        (paused, heartbeat_ms - 10_000, "TEST_STATE"),
    )
    db.execute(
        "INSERT INTO poly_chop_guard_heartbeat VALUES(1,?)",
        (heartbeat_ms,),
    )
    db.execute(
        "INSERT INTO poly_chop_guard_markets VALUES(?,?,?,?,?,?,?,?)",
        (123, status, reversals, 10, 1, heartbeat_ms - 20_000, None, None),
    )
    db.commit()
    db.close()


def test_neutral_or_old_direction_sample_does_not_make_fresh_health_heartbeat_unverified(tmp_path, monkeypatch):
    now_ms = 1_000_000
    path = tmp_path / "cross_oracle.db"
    # The market-level last_seen_at_ms is deliberately 20 seconds old. V27 must
    # ignore it for health and trust the dedicated fresh strategy heartbeat.
    _db(path, paused=0, heartbeat_ms=now_ms - 500, reversals=0)
    monkeypatch.setattr(v27, "CROSS_ORACLE_DB_PATH", path)

    engine = LocalDbPaperGuardPolyGapLiveEngine.__new__(LocalDbPaperGuardPolyGapLiveEngine)
    state = engine._local_guard_snapshot(now_ms)

    assert state["verified"] is True
    assert state["blocked"] is False
    assert state["persistentPaused"] is False
    assert state["ageMs"] == 500
    assert state["source"] == "LOCAL_CROSS_ORACLE_DB_HEALTH_HEARTBEAT_V27"
    assert state["full8768SnapshotRequiredForEntryGate"] is False


def test_persistent_pause_still_blocks_when_heartbeat_is_fresh(tmp_path, monkeypatch):
    now_ms = 2_000_000
    path = tmp_path / "cross_oracle.db"
    _db(path, paused=1, heartbeat_ms=now_ms - 250, reversals=4)
    monkeypatch.setattr(v27, "CROSS_ORACLE_DB_PATH", path)

    engine = LocalDbPaperGuardPolyGapLiveEngine.__new__(LocalDbPaperGuardPolyGapLiveEngine)
    state = engine._local_guard_snapshot(now_ms)

    assert state["verified"] is True
    assert state["blocked"] is True
    assert state["persistentPaused"] is True
    assert state["currentMarketChoppy"] is True


def test_stale_paper_health_heartbeat_fails_closed(tmp_path, monkeypatch):
    now_ms = 3_000_000
    path = tmp_path / "cross_oracle.db"
    _db(
        path,
        paused=0,
        heartbeat_ms=now_ms - v27.PAPER_GUARD_DB_MAX_HEARTBEAT_AGE_MS - 1,
    )
    monkeypatch.setattr(v27, "CROSS_ORACLE_DB_PATH", path)

    engine = LocalDbPaperGuardPolyGapLiveEngine.__new__(LocalDbPaperGuardPolyGapLiveEngine)
    with pytest.raises(RuntimeError, match="heartbeat stale"):
        engine._local_guard_snapshot(now_ms)
