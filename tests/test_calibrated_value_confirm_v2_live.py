from __future__ import annotations

from predict_bot import live_trading
from predict_bot import m_realtime
from predict_bot import research_forward
from predict_bot.calibrated_value_confirm_v2_live_patch import STRATEGY


def _valid_signal() -> dict[str, object]:
    return {
        "strategy": STRATEGY,
        "market_id": 123,
        "side": "UP",
        "entry_price": 0.603,
        "raw_top_ask": 0.60,
        "seconds_left": 58.0,
        "market_data_integrity_ok": True,
        "calibrated_value_confirmation_variant": True,
        "variant_mode": "FOLLOW_CONFIRMED_REPRICING",
    }


def test_confirm_v2_is_registered_for_live_selection_and_forwarding() -> None:
    assert STRATEGY in live_trading.LIVE_RESEARCH_STRATEGIES
    assert STRATEGY in live_trading.LIVE_SUPPORTED_STRATEGIES
    assert STRATEGY in m_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert STRATEGY in live_trading.CONFIRMATION_ADD_SOURCE_STRATEGIES
    assert STRATEGY in research_forward.CONFIRMATION_ADD_SOURCE_STRATEGIES
    assert live_trading.LIVE_RESEARCH_REPRICE_GAPS[STRATEGY] == live_trading.Decimal("0.05")


def test_confirm_v2_live_rules_accept_fixed_and_confirmation_add_modes() -> None:
    fixed = live_trading.normalize_live_rules(
        {
            "strategies": [STRATEGY],
            "strategyStakesUsdt": [1.0],
        }
    )
    assert fixed["strategies"] == [STRATEGY]
    assert fixed["strategyExecutionModes"] == [
        live_trading.LIVE_EXECUTION_MODE_FIXED
    ]

    add = live_trading.normalize_live_rules(
        {
            "strategies": [STRATEGY],
            "strategyExecutionModes": [
                live_trading.LIVE_EXECUTION_MODE_CONFIRMATION_ADD
            ],
            "strategyInitialStakesUsdt": [1.0],
            "strategyConfirmationAddStakesUsdt": [1.0],
            "strategyStakesUsdt": [5.0],
        }
    )
    assert add["strategies"] == [STRATEGY]
    assert add["strategyExecutionModes"] == [
        live_trading.LIVE_EXECUTION_MODE_CONFIRMATION_ADD
    ]
    assert add["strategyStakesUsdt"] == [5.0]


def test_confirm_v2_live_provenance_guard_accepts_only_frozen_follow_signal() -> None:
    allowed, reason = live_trading.LiveM0WEngine._research_signal_price_is_allowed(
        _valid_signal(),
        STRATEGY,
    )
    assert allowed is True
    assert reason == ""

    reverse = {**_valid_signal(), "variant_mode": "REVERSE_CONFIRMED_REPRICING"}
    allowed, reason = live_trading.LiveM0WEngine._research_signal_price_is_allowed(
        reverse,
        STRATEGY,
    )
    assert allowed is False
    assert "confirmed follow" in reason

    stale_window = {**_valid_signal(), "seconds_left": 54.9}
    allowed, reason = live_trading.LiveM0WEngine._research_signal_price_is_allowed(
        stale_window,
        STRATEGY,
    )
    assert allowed is False
    assert "55-61s" in reason

    expensive = {**_valid_signal(), "raw_top_ask": 0.701}
    allowed, reason = live_trading.LiveM0WEngine._research_signal_price_is_allowed(
        expensive,
        STRATEGY,
    )
    assert allowed is False
    assert "0.70" in reason
