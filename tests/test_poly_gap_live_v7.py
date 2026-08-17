from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live_v6 import ExitPriorityPolyGapLiveEngine
from predict_bot.poly_gap_live_v7 import (
    ENTRY_RETRY_COOLDOWN_MS,
    NO_DEPTH_RETRY_COOLDOWN_MS,
    PLACE_REJECT_RETRY_COOLDOWN_MS,
    ReArmingScalpPolyGapLiveEngine,
)


def test_v7_builds_on_v6_exit_priority_executor() -> None:
    assert issubclass(ReArmingScalpPolyGapLiveEngine, ExitPriorityPolyGapLiveEngine)


def test_v7_snapshot_declares_repeatable_scalp_cycle(tmp_path: Path) -> None:
    engine = ReArmingScalpPolyGapLiveEngine(tmp_path / "poly_gap_live_v7.db")
    try:
        state = engine.snapshot()
        cycle = state["scalpCycle"]
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V7"
        assert cycle["repeatSameMarketRounds"] is True
        assert cycle["definiteEntryFailureRetries"] is True
        assert cycle["rearmImmediatelyAfterConfirmedFlat"] is True
        assert cycle["ambiguousPlacementStillHaltsMarket"] is True
        assert cycle["unconfirmedPositionStillHaltsMarket"] is True
        assert cycle["oneActiveRoundAtATime"] is True
        assert cycle["entryRetryCooldownMs"] == ENTRY_RETRY_COOLDOWN_MS
        assert cycle["placeRejectRetryCooldownMs"] == PLACE_REJECT_RETRY_COOLDOWN_MS
        assert cycle["noDepthRetryCooldownMs"] == NO_DEPTH_RETRY_COOLDOWN_MS
        assert "definite entry failure after cooldown" in state["signalGeneration"]["rearm"]
    finally:
        engine.stop()


def test_v7_definite_failure_rearms_instead_of_market_long_latch(tmp_path: Path) -> None:
    engine = ReArmingScalpPolyGapLiveEngine(tmp_path / "poly_gap_live_retry.db")
    try:
        key = (123, "UP")
        engine.entry_signal_latch = key
        engine._schedule_retry(key, 500, "SIGNED_QUOTE_EDGE_GONE")
        assert engine.entry_signal_latch is None
        assert engine._retry_remaining_ms(key) > 0
        assert engine.entry_retry_reason[key] == "SIGNED_QUOTE_EDGE_GONE"
    finally:
        engine.stop()


def test_v7_confirmed_flat_round_clears_latch_and_rearms(tmp_path: Path) -> None:
    engine = ReArmingScalpPolyGapLiveEngine(tmp_path / "poly_gap_live_flat.db")
    try:
        now = 1_000
        with engine.db_lock:
            engine.db.execute(
                """INSERT INTO poly_gap_live_rounds(
                       market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                       entry_cost_usdt,shares,created_at_ms,updated_at_ms
                   ) VALUES(123,456,1,'UP','token','OPEN',1.0,1.0,2.0,?,?)""",
                (now, now),
            )
            round_id = int(engine.db.execute("SELECT last_insert_rowid()").fetchone()[0])
            row = engine.db.execute(
                "SELECT * FROM poly_gap_live_rounds WHERE id=?", (round_id,)
            ).fetchone()
            engine.db.commit()
        assert row is not None
        engine.entry_signal_latch = (123, "UP")
        engine.entry_retry_not_before[(123, "UP")] = 999999999.0
        engine._finish_round(dict(row), 0.1, 1.1, "POLY_DIRECTION_FLIP")
        assert engine.entry_signal_latch is None
        assert engine.entry_retry_not_before == {}
        assert engine.last_rearm_reason == "confirmed flat after POLY_DIRECTION_FLIP"
        with engine.db_lock:
            closed = engine.db.execute(
                "SELECT state FROM poly_gap_live_rounds WHERE id=?", (round_id,)
            ).fetchone()
        assert closed["state"] == "CLOSED"
    finally:
        engine.stop()


def test_supervisor_runs_v7_entrypoint() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v7" in source
    assert "predict_bot.poly_gap_live_v6" not in source
