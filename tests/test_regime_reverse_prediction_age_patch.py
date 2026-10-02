from __future__ import annotations

import time

from predict_bot.live_trading import LiveM0WEngine
from predict_bot.regime_reverse_prediction_age_patch import (
    REGIME_REVERSE_3L_STRATEGY,
    _strategy_prediction_age_context,
    prediction_book_age_limit_ms,
)


def verified_book(age_ms: float) -> dict[str, object]:
    return {
        "market_id": 202,
        "orientation": "DIRECT_UP_VERIFIED",
        "received_monotonic_ns": time.monotonic_ns() - int(age_ms * 1_000_000),
        "book_age_ms": age_ms,
        "up_bid": 0.32,
        "up_ask": 0.33,
        "up_bid_size": 100.0,
        "up_ask_size": 100.0,
        "down_bid": 0.66,
        "down_ask": 0.67,
        "down_bid_size": 100.0,
        "down_ask_size": 100.0,
    }


def bare_engine(age_ms: float) -> LiveM0WEngine:
    engine = object.__new__(LiveM0WEngine)
    engine.max_prediction_book_age_ms = 2_000.0
    engine.current_verified_prediction_book = lambda: verified_book(age_ms)
    return engine


def test_only_regime_reverse_3l_gets_2500ms_limit():
    assert prediction_book_age_limit_ms(REGIME_REVERSE_3L_STRATEGY, 2_000.0) == 2_500.0
    assert prediction_book_age_limit_ms("R_FUTURES_LEAD", 2_000.0) == 2_000.0


def test_2200ms_book_is_blocked_normally_but_allowed_for_regime_reverse_3l():
    engine = bare_engine(2_200.0)

    normal = engine._latest_prediction_book_check(market_id=202, side="UP")
    assert normal[1] == "BLOCKED_STALE_PREDICTION_BOOK"

    with _strategy_prediction_age_context(REGIME_REVERSE_3L_STRATEGY):
        allowed = engine._latest_prediction_book_check(market_id=202, side="UP")

    assert allowed[0] is not None
    assert allowed[1] is None
    assert engine.max_prediction_book_age_ms == 2_000.0


def test_regime_reverse_3l_still_blocks_above_2500ms():
    engine = bare_engine(2_600.0)

    with _strategy_prediction_age_context(REGIME_REVERSE_3L_STRATEGY):
        blocked = engine._latest_prediction_book_check(market_id=202, side="UP")

    assert blocked[1] == "BLOCKED_STALE_PREDICTION_BOOK"
    assert "2500.000ms" in str(blocked[3])
