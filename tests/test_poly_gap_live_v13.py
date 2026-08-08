from __future__ import annotations

from pathlib import Path

from predict_bot import poly_gap_live as base
from predict_bot.poly_gap_live_v12 import StableExitPolyGapLiveEngine
from predict_bot.poly_gap_live_v13 import PaperChopGuardedPolyGapLiveEngine


def test_v13_builds_on_v12() -> None:
    assert issubclass(PaperChopGuardedPolyGapLiveEngine, StableExitPolyGapLiveEngine)


def test_persistent_paper_guard_blocks_new_entry_before_live_clients(tmp_path: Path, monkeypatch) -> None:
    engine = PaperChopGuardedPolyGapLiveEngine(tmp_path / "v13-paused.db")
    try:
        monkeypatch.setattr(base, "MASTER_ENABLED", True)
        engine._set_setting("runtime_enabled", "1")
        monkeypatch.setattr(
            engine,
            "_refresh_chop_guard",
            lambda **_kwargs: {
                "verified": True,
                "blocked": True,
                "persistentPaused": True,
                "currentMarketChoppy": False,
                "reason": "2/3 recent markets CHOPPY",
            },
        )
        engine._tick()
        assert engine.status == "BLOCKED_PAPER_CHOP_GUARD"
        assert "CHOPPY" in str(engine.last_error)
    finally:
        engine.stop()


def test_current_market_chop_breaker_blocks_reentry_immediately(tmp_path: Path, monkeypatch) -> None:
    engine = PaperChopGuardedPolyGapLiveEngine(tmp_path / "v13-current.db")
    try:
        monkeypatch.setattr(base, "MASTER_ENABLED", True)
        engine._set_setting("runtime_enabled", "1")
        monkeypatch.setattr(
            engine,
            "_refresh_chop_guard",
            lambda **_kwargs: {
                "verified": True,
                "blocked": True,
                "persistentPaused": False,
                "currentMarketChoppy": True,
                "reason": "current market already has 2 confirmed reversals",
            },
        )
        engine._tick()
        assert engine.status == "BLOCKED_PAPER_CURRENT_MARKET_CHOP"
        assert "2 confirmed reversals" in str(engine.last_error)
    finally:
        engine.stop()


def test_unverified_paper_guard_fails_closed_for_new_entry(tmp_path: Path, monkeypatch) -> None:
    engine = PaperChopGuardedPolyGapLiveEngine(tmp_path / "v13-unverified.db")
    try:
        monkeypatch.setattr(base, "MASTER_ENABLED", True)
        engine._set_setting("runtime_enabled", "1")
        monkeypatch.setattr(
            engine,
            "_refresh_chop_guard",
            lambda **_kwargs: {
                "verified": False,
                "blocked": None,
                "reason": "Paper guard unavailable",
                "error": "HTTP 503",
            },
        )
        engine._tick()
        assert engine.status == "BLOCKED_PAPER_CHOP_GUARD_UNVERIFIED"
        assert "503" in str(engine.last_error)
    finally:
        engine.stop()


def test_guard_never_blocks_management_of_existing_round(tmp_path: Path, monkeypatch) -> None:
    engine = PaperChopGuardedPolyGapLiveEngine(tmp_path / "v13-existing.db")
    try:
        now = base._now_ms()
        with engine.db_lock:
            engine.db.execute(
                """INSERT INTO poly_gap_live_rounds(
                       market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                       entry_cost_usdt,shares,created_at_ms,updated_at_ms
                   ) VALUES(10,20,1,'UP','token-up','OPEN',1.0,1.0,1.5,?,?)""",
                (now, now),
            )
            engine.db.commit()

        called = {"parent": 0, "guard": 0}

        def parent_tick(_self) -> None:
            called["parent"] += 1

        def guard_refresh(**_kwargs):
            called["guard"] += 1
            return {"verified": True, "blocked": True}

        monkeypatch.setattr(StableExitPolyGapLiveEngine, "_tick", parent_tick)
        monkeypatch.setattr(engine, "_refresh_chop_guard", guard_refresh)
        engine._tick()
        assert called["parent"] == 1
        assert called["guard"] == 0
    finally:
        engine.stop()


def test_v13_snapshot_exposes_fail_closed_guard_policy(tmp_path: Path) -> None:
    engine = PaperChopGuardedPolyGapLiveEngine(tmp_path / "v13-snapshot.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V13"
        guard = state["paperChopGuard"]
        assert guard["failClosedForNewEntries"] is True
        assert guard["sameMarketImmediateBreaker"] is True
        assert guard["persistentCrossMarketPause"] is True
        assert guard["existingPositionManagementNeverBlocked"] is True
        assert guard["automaticResumeOwnedByPaper"] is True
    finally:
        engine.stop()
