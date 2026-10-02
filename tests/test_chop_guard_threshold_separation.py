from __future__ import annotations

from predict_bot import cross_oracle_strategy_chop_guard as persistent
from predict_bot import cross_oracle_strategy_chop_guard_v2 as immediate


def test_persistent_chop_guard_defaults_are_less_sensitive() -> None:
    assert persistent.CHOP_GUARD_REVERSALS_PER_MARKET == 3
    assert persistent.CHOP_GUARD_TRIGGER_WINDOW_MARKETS == 5
    assert persistent.CHOP_GUARD_TRIGGER_CHOPPY_MARKETS == 3
    assert persistent.CHOP_GUARD_RESUME_CALM_MARKETS == 2


def test_immediate_same_market_breaker_stays_at_two_reversals() -> None:
    assert immediate.IMMEDIATE_CHOP_BREAKER_REVERSALS == 2
    assert (
        immediate.IMMEDIATE_CHOP_BREAKER_REVERSALS
        < persistent.CHOP_GUARD_REVERSALS_PER_MARKET
    )
