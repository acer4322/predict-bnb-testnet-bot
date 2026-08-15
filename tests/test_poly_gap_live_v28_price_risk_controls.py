from __future__ import annotations

from pathlib import Path

import pytest

from predict_bot.poly_gap_live_v28 import PriceRiskControlsPolyGapLiveEngine


def _engine(tmp_path: Path) -> PriceRiskControlsPolyGapLiveEngine:
    return PriceRiskControlsPolyGapLiveEngine(tmp_path / "poly_gap_live_v28.db")


def _close(engine: PriceRiskControlsPolyGapLiveEngine) -> None:
    try:
        engine.http.close()
    except Exception:
        pass
    try:
        engine.db.close()
    except Exception:
        pass


def _insert_closed_round(
    engine: PriceRiskControlsPolyGapLiveEngine,
    *,
    market_id: int,
    round_no: int,
    exit_signal_at_ms: int | None,
    exit_intent: str | None,
) -> None:
    now = 1_000_000 + round_no
    with engine.db_lock:
        engine.db.execute(
            """INSERT INTO poly_gap_live_rounds(
                   market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                   exit_signal_at_ms,exit_intent,created_at_ms,updated_at_ms
               ) VALUES(?,?,?,?,?,'CLOSED',?,?,?,?,?)""",
            (
                market_id,
                1,
                round_no,
                "UP",
                f"token-{round_no}",
                1.0,
                exit_signal_at_ms,
                exit_intent,
                now,
                now,
            ),
        )
        engine.db.commit()


def test_defaults_and_dashboard_settings_persist(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        settings = engine._settings()
        assert settings["takeProfitPrice"] == pytest.approx(0.95)
        assert settings["maxEntryPrice"] == pytest.approx(0.90)

        engine.update_settings({"takeProfitPrice": 0.965, "maxEntryPrice": 0.875})
        assert engine._settings()["takeProfitPrice"] == pytest.approx(0.965)
        assert engine._settings()["maxEntryPrice"] == pytest.approx(0.875)
    finally:
        _close(engine)

    reopened = _engine(tmp_path)
    try:
        assert reopened._settings()["takeProfitPrice"] == pytest.approx(0.965)
        assert reopened._settings()["maxEntryPrice"] == pytest.approx(0.875)
    finally:
        _close(reopened)


def test_max_entry_must_remain_below_take_profit(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        with pytest.raises(ValueError, match="maxEntryPrice must be lower"):
            engine.update_settings({"maxEntryPrice": 0.95})
        with pytest.raises(ValueError, match="maxEntryPrice must be lower"):
            engine.update_settings({"takeProfitPrice": 0.90})
    finally:
        _close(engine)


def test_take_profit_exit_does_not_consume_reversal_breaker(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        _insert_closed_round(
            engine,
            market_id=5001,
            round_no=1,
            exit_signal_at_ms=1_100,
            exit_intent="TAKE_PROFIT",
        )
        assert engine._completed_reversal_exits_for_market(5001) == 0

        _insert_closed_round(
            engine,
            market_id=5001,
            round_no=2,
            exit_signal_at_ms=1_200,
            exit_intent="POLY_DIRECTION_FLIP",
        )
        assert engine._completed_reversal_exits_for_market(5001) == 1
    finally:
        _close(engine)


def test_pre_v28_closed_exit_signal_remains_legacy_reversal(tmp_path: Path):
    engine = _engine(tmp_path)
    try:
        _insert_closed_round(
            engine,
            market_id=6001,
            round_no=1,
            exit_signal_at_ms=2_000,
            exit_intent=None,
        )
        assert engine._completed_reversal_exits_for_market(6001) == 1
    finally:
        _close(engine)
