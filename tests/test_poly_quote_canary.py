from __future__ import annotations

from pathlib import Path

from predict_bot.cross_oracle_strategies import SCALP_MIN_EDGE
from predict_bot.poly_quote_canary import PolyQuoteCanary
from predict_bot.poly_quote_canary_live_sizing import (
    LiveSizedPolyQuoteCanary,
    configured_live_stake_from_state,
)


def bare_canary() -> PolyQuoteCanary:
    return object.__new__(PolyQuoteCanary)


def bare_live_sized_canary() -> LiveSizedPolyQuoteCanary:
    return object.__new__(LiveSizedPolyQuoteCanary)


def test_gap_scalp_price_limit_preserves_minimum_edge() -> None:
    canary = bare_canary()
    event = {
        "strategy": "R_POLY_GAP_SCALP",
        "phase": "ENTRY",
        "side": "UP",
        "paper_price": 0.50,
        "poly_selected_mid": 0.56,
    }
    limit = canary._price_limit(event, {"up_ask": 0.52})
    assert limit <= 0.56 - SCALP_MIN_EDGE + 1e-12
    assert limit == 0.53


def test_lead_signal_fails_if_binance_catches_up_before_quote_response() -> None:
    canary = bare_canary()
    valid, reason, edge = canary._signal_still_valid(
        {
            "strategy": "R_POLY_LEAD_ENTRY",
            "phase": "ENTRY",
            "side": "UP",
            "poly_up_mid": 0.60,
        },
        quote_average=0.61,
        current_binance_up_mid=0.56,
        current_poly_up_mid=0.60,
    )
    assert valid is False
    assert "caught up" in reason
    assert edge is None


def test_live_sized_lead_also_fails_if_poly_is_no_longer_confident() -> None:
    canary = bare_live_sized_canary()
    valid, reason, edge = canary._signal_still_valid(
        {
            "strategy": "R_POLY_LEAD_ENTRY",
            "phase": "ENTRY",
            "side": "UP",
            "poly_up_mid": 0.60,
        },
        quote_average=0.52,
        current_binance_up_mid=0.49,
        current_poly_up_mid=0.52,
    )
    assert valid is False
    assert "no longer confidently supports" in reason
    assert edge is None


def test_gap_signal_uses_signed_quote_price_not_paper_ask() -> None:
    canary = bare_canary()
    valid, reason, edge = canary._signal_still_valid(
        {
            "strategy": "R_POLY_GAP_SCALP",
            "phase": "ENTRY",
            "side": "UP",
            "poly_up_mid": 0.60,
        },
        quote_average=0.58,
        current_binance_up_mid=0.54,
        current_poly_up_mid=0.60,
    )
    assert valid is False
    assert edge is not None and edge < SCALP_MIN_EDGE
    assert "signed quote" in reason


def test_live_rule_initial_stake_overrides_paper_size_when_selected() -> None:
    stake, source = configured_live_stake_from_state(
        {
            "rules": {
                "strategies": ["R_MICROPRICE", "R_POLY_GAP_SCALP"],
                "strategyStakesUsdt": [2.0, 9.0],
                "strategyInitialStakesUsdt": [2.0, 7.5],
            }
        },
        "R_POLY_GAP_SCALP",
    )
    assert stake == 7.5
    assert source == "LIVE_RULE_INITIAL_STAKE"


def test_unselected_poly_strategy_does_not_borrow_another_strategy_stake() -> None:
    stake, source = configured_live_stake_from_state(
        {
            "rules": {
                "strategies": ["R_MICROPRICE"],
                "strategyStakesUsdt": [10.0],
                "strategyInitialStakesUsdt": [10.0],
            }
        },
        "R_POLY_LEAD_ENTRY",
    )
    assert stake is None
    assert source is None


def test_canary_source_contains_no_order_placement_calls() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "predict_bot"
    text = (root / "poly_quote_canary.py").read_text(encoding="utf-8")
    sizing = (root / "poly_quote_canary_live_sizing.py").read_text(encoding="utf-8")
    combined = text + "\n" + sizing
    assert ".get_quote(" in combined
    assert ".place_limit_order(" not in combined
    assert ".place_market_order(" not in combined
    assert "trade/place-order-bundle" not in combined
