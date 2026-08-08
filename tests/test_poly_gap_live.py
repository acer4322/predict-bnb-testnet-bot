from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live import PolyGapLiveEngine
from predict_bot.poly_gap_live_v2 import RolloverSafePolyGapLiveEngine
from predict_bot.poly_gap_live_v3 import SignalGenerationPolyGapLiveEngine
from predict_bot.poly_gap_live_v4 import ImmediateRiskPolyGapLiveEngine
from predict_bot.poly_gap_live_v5 import OperationalMetricsPolyGapLiveEngine


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


def test_v5_builds_on_all_previous_dedicated_guards() -> None:
    assert issubclass(RolloverSafePolyGapLiveEngine, PolyGapLiveEngine)
    assert issubclass(SignalGenerationPolyGapLiveEngine, RolloverSafePolyGapLiveEngine)
    assert issubclass(ImmediateRiskPolyGapLiveEngine, SignalGenerationPolyGapLiveEngine)
    assert issubclass(OperationalMetricsPolyGapLiveEngine, ImmediateRiskPolyGapLiveEngine)


def test_v5_snapshot_separates_current_status_last_error_and_entry_success(tmp_path: Path) -> None:
    engine = OperationalMetricsPolyGapLiveEngine(tmp_path / "poly_gap_live_v5.db")
    try:
        now = 1_000
        with engine.db_lock:
            engine.db.execute(
                """INSERT INTO poly_gap_live_rounds(
                       market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                       entry_order_id,created_at_ms,updated_at_ms
                   ) VALUES(1,2,1,'UP','token','CLOSED',1.0,'order-1',?,?)""",
                (now, now),
            )
            first_id = int(engine.db.execute("SELECT last_insert_rowid()").fetchone()[0])
            engine.db.execute(
                """INSERT INTO poly_gap_live_events(
                       at_ms,level,event_type,market_id,round_id,message
                   ) VALUES(?, 'WARN', 'ENTRY_PLACED', 1, ?, 'submitted')""",
                (now + 1, first_id),
            )
            engine.db.execute(
                """INSERT INTO poly_gap_live_events(
                       at_ms,level,event_type,market_id,round_id,message
                   ) VALUES(?, 'INFO', 'ENTRY_CONFIRMED', 1, ?, 'confirmed')""",
                (now + 2, first_id),
            )
            engine.db.execute(
                """INSERT INTO poly_gap_live_rounds(
                       market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                       error_kind,error_message,close_reason,created_at_ms,updated_at_ms
                   ) VALUES(1,2,2,'DOWN','token2','REJECTED',1.0,
                            'ENTRY_QUOTE_REJECTED','HTTP 400','ENTRY_QUOTE_REJECTED',?,?)""",
                (now + 3, now + 4),
            )
            engine.db.commit()
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V5"
        assert state["currentStatus"]["status"] == state["status"]
        assert state["lastErrorDetail"]["kind"] == "ENTRY_QUOTE_REJECTED"
        assert state["lastErrorDetail"]["message"] == "HTTP 400"
        assert state["entryExecution"]["attempts"] == 2
        assert state["entryExecution"]["submitted"] == 1
        assert state["entryExecution"]["confirmed"] == 1
        assert state["entryExecution"]["successRate"] == 0.5
        assert state["entryExecution"]["quoteRejected"] == 1
        assert state["lossGuard"]["settingsApplyIsImmediate"] is True
        assert state["generalLiveConflict"]["singleRealMoneyOwner"] is True
    finally:
        engine.stop()


def test_lowered_loss_cap_trips_immediately(tmp_path: Path) -> None:
    engine = ImmediateRiskPolyGapLiveEngine(tmp_path / "poly_gap_live_risk.db")
    try:
        now = 1_000
        with engine.db_lock:
            engine.db.execute(
                """INSERT INTO poly_gap_live_rounds(
                       market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                       pnl_usdt,created_at_ms,updated_at_ms
                   ) VALUES(1,2,1,'UP','token','CLOSED',5.0,-3.0,?,?)""",
                (now, now),
            )
            engine.db.commit()
        state = engine.update_settings(
            {"maximumLossEnabled": True, "maximumLossUsdt": 2.0}
        )
        assert state["lossGuard"]["tripped"] is True
        assert state["settings"]["runtimeEnabled"] is False
    finally:
        engine.stop()


def test_supervisor_runs_v5_entrypoint() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "predict_bot" / "supervisor.py").read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v5" in source
    assert "predict_bot.poly_gap_live_v4" not in source
