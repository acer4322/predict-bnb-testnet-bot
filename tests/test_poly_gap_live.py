from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live import PolyGapLiveEngine
from predict_bot.poly_gap_live_v2 import RolloverSafePolyGapLiveEngine


def test_dedicated_poly_gap_live_defaults_fail_closed(tmp_path: Path) -> None:
    engine = PolyGapLiveEngine(tmp_path / "poly_gap_live.db")
    try:
        state = engine.snapshot()
        assert state["strategy"] == "R_POLY_GAP_SCALP_LIVE"
        assert state["realMoney"] is True
        assert state["settings"]["runtimeEnabled"] is False
        assert state["rules"]["sameMarketMultipleRounds"] is True
        assert state["rules"]["oneActiveRoundAtATime"] is True
        assert state["rules"]["rearmRequiresFlatPosition"] is True
        assert state["rules"]["permanentStrategyMarketDedup"] is False
        assert "MARKET/FOK" in state["rules"]["entryExecution"]
        assert "MARKET/FOK" in state["rules"]["exitExecution"]
    finally:
        engine.stop()


def test_dedicated_settings_allow_independent_stake_and_max_loss(tmp_path: Path) -> None:
    engine = PolyGapLiveEngine(tmp_path / "poly_gap_live.db")
    try:
        state = engine.update_settings(
            {
                "stakeUsdt": 3.25,
                "maximumLossEnabled": True,
                "maximumLossUsdt": 7.50,
            }
        )
        assert state["settings"]["stakeUsdt"] == 3.25
        assert state["settings"]["maximumLossEnabled"] is True
        assert state["settings"]["maximumLossUsdt"] == 7.50
    finally:
        engine.stop()


def test_database_allows_multiple_rounds_in_same_market(tmp_path: Path) -> None:
    engine = PolyGapLiveEngine(tmp_path / "poly_gap_live.db")
    try:
        now = 1_000
        with engine.db_lock:
            for round_no in (1, 2, 3):
                engine.db.execute(
                    """INSERT INTO poly_gap_live_rounds(
                           market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                           created_at_ms,updated_at_ms
                       ) VALUES(?,?,?,?,?,'CLOSED',?,?,?)""",
                    (123, 456, round_no, "UP", "token", 1.0, now, now),
                )
            engine.db.commit()
            row = engine.db.execute(
                "SELECT COUNT(*) AS count FROM poly_gap_live_rounds WHERE market_id=123"
            ).fetchone()
        assert int(row["count"]) == 3
    finally:
        engine.stop()


def test_supervised_v2_is_rollover_safe_subclass() -> None:
    assert issubclass(RolloverSafePolyGapLiveEngine, PolyGapLiveEngine)
