from __future__ import annotations

from pathlib import Path

from predict_bot.cross_oracle_strategies import SCALP_MIN_EDGE
from predict_bot.poly_quote_canary import PolyQuoteCanary


def bare_canary() -> PolyQuoteCanary:
    return object.__new__(PolyQuoteCanary)


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


def test_canary_source_contains_no_order_placement_calls() -> None:
    source = Path(__file__).resolve().parents[1] / "src" / "predict_bot" / "poly_quote_canary.py"
    text = source.read_text(encoding="utf-8")
    assert ".get_quote(" in text
    assert ".place_limit_order(" not in text
    assert ".place_market_order(" not in text
    assert "trade/place-order-bundle" not in text
