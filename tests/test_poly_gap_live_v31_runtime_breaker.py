from __future__ import annotations

from predict_bot.poly_gap_live_v31 import RuntimeReversalBreakerPolyGapLiveEngine


def _insert_reversal_exit(engine, market_id: int, round_no: int) -> None:
    now = 1_780_000_000_000 + round_no
    with engine.db_lock:
        engine.db.execute(
            """INSERT INTO poly_gap_live_rounds(
                   market_id, topic_id, round_no, side, token_id, state, stake_usdt,
                   exit_signal_at_ms, exit_intent, created_at_ms, updated_at_ms
               ) VALUES (?, 1, ?, 'UP', ?, 'CLOSED', 1.0, ?,
                         'POLY_DIRECTION_FLIP', ?, ?)""",
            (market_id, round_no, f"token-{round_no}", now, now, now),
        )
        engine.db.commit()


def test_runtime_threshold_controls_flat_and_projected_breakers(tmp_path):
    engine = RuntimeReversalBreakerPolyGapLiveEngine(tmp_path / "poly-gap-live.db")
    try:
        engine._set_setting("same_market_reversal_exit_threshold", "3")
        _insert_reversal_exit(engine, 1001, 1)
        _insert_reversal_exit(engine, 1001, 2)

        flat = engine._live_same_market_breaker(1001)
        assert flat["completedReversalExits"] == 2
        assert flat["threshold"] == 3
        assert flat["blocked"] is False
        assert flat["remainingBeforeBlock"] == 1

        projected = engine._projected_reversal_breaker(1001)
        assert projected["projectedIfCurrentExitCompletes"] == 3
        assert projected["threshold"] == 3
        assert projected["handoffBlocked"] is True
    finally:
        engine.db.close()


def test_dashboard_setting_is_persisted_and_validated(tmp_path):
    engine = RuntimeReversalBreakerPolyGapLiveEngine(tmp_path / "poly-gap-live.db")
    try:
        # Avoid exercising unrelated snapshot diagnostics in this narrow settings test.
        engine.snapshot = lambda: {"settings": engine._settings()}  # type: ignore[method-assign]
        state = engine.update_settings({"sameMarketReversalExitThreshold": 4})
        assert state["settings"]["sameMarketReversalExitThreshold"] == 4
        assert engine._setting("same_market_reversal_exit_threshold", "") == "4"

        try:
            engine.update_settings({"sameMarketReversalExitThreshold": 2.5})
        except ValueError as exc:
            assert "integer between 1 and 20" in str(exc)
        else:
            raise AssertionError("fractional reversal threshold must be rejected")
    finally:
        engine.db.close()
