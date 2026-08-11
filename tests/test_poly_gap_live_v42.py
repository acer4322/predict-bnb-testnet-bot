from predict_bot import poly_gap_live as base
from predict_bot.poly_gap_live_v40 import ImmediateExitCautiousReentryPolyGapLiveEngine
from predict_bot.poly_gap_live_v41 import PolySourceFreshnessGuardPolyGapLiveEngine
from predict_bot.poly_gap_live_v42 import (
    TAKE_PROFIT_MARKET_LOCK_STATUS,
    TakeProfitMarketLockPolyGapLiveEngine,
    take_profit_reentry_policy,
)


def _market(market_id: int) -> dict[str, object]:
    now_ms = base._now_ms()
    return {
        "market_id": market_id,
        "topic_id": market_id + 1000,
        "up_token_id": f"up-{market_id}",
        "down_token_id": f"down-{market_id}",
        "fee_rate_bps": 200,
        "start_ms": now_ms - 1_000,
        "end_ms": now_ms + 299_000,
    }


def _insert_round(engine: TakeProfitMarketLockPolyGapLiveEngine, market_id: int) -> dict[str, object]:
    market = _market(market_id)
    return engine._insert_round(
        market=market,
        side="UP",
        token_id=str(market["up_token_id"]),
        stake=1.0,
        poly_selected=0.90,
        ask=0.50,
        edge=0.40,
    )


def test_take_profit_reentry_policy() -> None:
    assert take_profit_reentry_policy(locked=False) == "ALLOW"
    assert take_profit_reentry_policy(locked=True) == "BLOCK_TAKE_PROFIT_MARKET"


def test_v42_preserves_v40_sell_and_v41_source_guard_lineage() -> None:
    assert issubclass(
        TakeProfitMarketLockPolyGapLiveEngine,
        PolySourceFreshnessGuardPolyGapLiveEngine,
    )
    assert (
        TakeProfitMarketLockPolyGapLiveEngine._observe_exit_flip
        is ImmediateExitCautiousReentryPolyGapLiveEngine._observe_exit_flip
    )
    assert (
        TakeProfitMarketLockPolyGapLiveEngine._exit_round
        is ImmediateExitCautiousReentryPolyGapLiveEngine._exit_round
    )


def test_only_take_profit_intent_locks_same_market(tmp_path) -> None:
    db_path = tmp_path / "poly_gap_v42.db"
    engine = TakeProfitMarketLockPolyGapLiveEngine(db_path)
    try:
        row = _insert_round(engine, 4242)
        round_id = int(row["id"])

        engine._set_exit_intent(round_id, "POLY_DIRECTION_FLIP")
        assert engine._take_profit_market_lock(4242) is None

        engine._set_exit_intent(round_id, "TAKE_PROFIT")
        lock = engine._take_profit_market_lock(4242)
        assert lock is not None
        assert lock["marketId"] == 4242
        assert lock["sourceRoundId"] == round_id
        assert lock["reason"] == "TAKE_PROFIT"
        assert engine._is_take_profit_locked_market(4242) is True
        assert engine._is_take_profit_locked_market(4243) is False

        # A later intent update must never reopen the market after TP triggered.
        engine._set_exit_intent(round_id, "POLY_DIRECTION_FLIP")
        assert engine._is_take_profit_locked_market(4242) is True
    finally:
        engine.stop()


def test_take_profit_lock_rejects_raced_same_market_round_before_quote(tmp_path) -> None:
    db_path = tmp_path / "poly_gap_v42_block.db"
    engine = TakeProfitMarketLockPolyGapLiveEngine(db_path)
    try:
        source = _insert_round(engine, 4343)
        engine._set_exit_intent(int(source["id"]), "TAKE_PROFIT")

        raced = _insert_round(engine, 4343)
        engine._open_round(
            raced,
            {"direction": "UP", "selectedMid": 0.90},
        )
        refreshed = engine._round_state(int(raced["id"]))
        assert refreshed is not None
        assert refreshed["state"] == "REJECTED"
        assert refreshed["close_reason"] == "TAKE_PROFIT_MARKET_REENTRY_BLOCKED_V42"
        assert refreshed["error_kind"] == "TAKE_PROFIT_MARKET_REENTRY_BLOCKED_V42"
        assert engine.status == TAKE_PROFIT_MARKET_LOCK_STATUS
        assert refreshed["entry_quote_started_at_ms"] is None
        assert refreshed["entry_order_id"] is None
    finally:
        engine.stop()


def test_take_profit_market_lock_survives_restart(tmp_path) -> None:
    db_path = tmp_path / "poly_gap_v42_restart.db"
    first = TakeProfitMarketLockPolyGapLiveEngine(db_path)
    try:
        row = _insert_round(first, 5252)
        first._set_exit_intent(int(row["id"]), "TAKE_PROFIT")
        assert first._is_take_profit_locked_market(5252) is True
    finally:
        first.stop()

    second = TakeProfitMarketLockPolyGapLiveEngine(db_path)
    try:
        assert second._is_take_profit_locked_market(5252) is True
        lock = second._take_profit_market_lock(5252)
        assert lock is not None
        assert lock["reason"] == "TAKE_PROFIT"
    finally:
        second.stop()
