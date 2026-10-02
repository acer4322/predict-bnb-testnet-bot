from __future__ import annotations

import os

os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ASSET", "ETH")
os.environ.setdefault("PREDICT_WALLET_MAKER_CLONE_ENABLED", "false")

from predict_bot.wallet_maker_clone_live_v4 import (  # noqa: E402
    MAX_BOOK_SPREAD,
    book_is_sane,
    market_elapsed_seconds,
    market_seconds_left,
)


def test_wide_floor_ceiling_book_is_blocked() -> None:
    sane, reason = book_is_sane({"bestBid": 0.01, "bestAsk": 0.99})
    assert sane is False
    assert "spread" in reason


def test_legitimate_low_price_tight_book_is_allowed() -> None:
    sane, reason = book_is_sane({"bestBid": 0.02, "bestAsk": 0.03})
    assert sane is True
    assert reason == "OK"


def test_maximum_spread_boundary_is_allowed() -> None:
    sane, _ = book_is_sane({"bestBid": 0.30, "bestAsk": 0.30 + MAX_BOOK_SPREAD})
    assert sane is True


def test_market_open_timing_uses_five_minute_window() -> None:
    market = {"end_ms": 1_300_000}
    assert market_seconds_left(market, 1_000_000) == 300.0
    assert market_elapsed_seconds(market, 1_000_000) == 0.0
    assert market_elapsed_seconds(market, 1_008_000) == 8.0
    assert market_seconds_left(market, 1_240_000) == 60.0
