from pathlib import Path

import pytest

from predict_bot.xpair_btc_eth_paper import (
    MarketRef,
    XPairStore,
    build_trials,
    classify_pair_result,
    equal_share_cross_fill,
    wilson_interval,
)


def market(symbol: str, market_id: int, fee_bps: int = 200) -> MarketRef:
    return MarketRef(
        symbol=symbol,
        topic_id=market_id + 100,
        market_id=market_id,
        title=f"{symbol} 5m",
        start_ms=1_000,
        end_ms=301_000,
        start_price=100.0,
        fee_bps=fee_bps,
        up_token_id=f"{symbol}-up",
        down_token_id=f"{symbol}-down",
    )


def book(ask: float, size: float = 100.0, timestamp: int = 10_000) -> dict:
    return {
        "asks": [[str(ask), str(size)]],
        "bids": [[str(max(0.001, ask - 0.01)), str(size)]],
        "updateTimestampMs": timestamp,
    }


def test_equal_share_fill_includes_both_fees_and_caps_budget():
    result = equal_share_cross_fill(
        [(0.30, 100.0)],
        [(0.60, 100.0)],
        total_budget=9.12,
        first_fee_bps=200,
        second_fee_bps=200,
        max_total_cost_per_share=0.92,
    )
    assert result is not None
    assert result["filled_shares"] == pytest.approx(10.0)
    assert result["total_cost"] == pytest.approx(9.12)
    assert result["cost_per_share"] == pytest.approx(0.912)
    assert result["first_vwap"] == pytest.approx(0.30)
    assert result["second_vwap"] == pytest.approx(0.60)


def test_equal_share_fill_rejects_top_cost_above_limit():
    assert (
        equal_share_cross_fill(
            [(0.50, 10.0)],
            [(0.50, 10.0)],
            total_budget=10.0,
            first_fee_bps=200,
            second_fee_bps=200,
            max_total_cost_per_share=1.0,
        )
        is None
    )


def test_classify_pair_result_identifies_double_loss():
    result = classify_pair_result(
        btc_side="UP",
        eth_side="DOWN",
        btc_winner="DOWN",
        eth_winner="UP",
        shares=10.0,
        total_cost=9.0,
    )
    assert result["winning_legs"] == 0
    assert result["double_loss"] is True
    assert result["payout"] == 0.0
    assert result["pnl"] == -9.0


def test_build_trials_keeps_two_opposite_variants_separate():
    btc = market("BTCUSDT", 1)
    eth = market("ETHUSDT", 2)
    books = {
        "btc_up": book(0.25, timestamp=9_990),
        "btc_down": book(0.72, timestamp=9_995),
        "eth_up": book(0.70, timestamp=9_992),
        "eth_down": book(0.26, timestamp=9_998),
    }
    trials, metrics = build_trials(
        btc=btc,
        eth=eth,
        books=books,
        total_stake=10.0,
        max_total_cost_per_share=0.55,
        minimum_filled_shares=1.0,
        max_book_age_ms=100.0,
        max_book_skew_ms=20.0,
        max_cross_book_skew_ms=20.0,
        now_ms=10_000,
    )
    by_variant = {trial["variant"]: trial for trial in trials}
    assert by_variant["BTC_UP_ETH_DOWN"]["eligible"] is True
    assert by_variant["BTC_DOWN_ETH_UP"]["eligible"] is False
    assert by_variant["BTC_DOWN_ETH_UP"]["entry_status"] == "SKIPPED_PRICE"
    assert metrics["cross_book_skew_ms"] == pytest.approx(8.0)


def test_store_reports_eligible_double_loss_rate_and_interval(tmp_path: Path):
    btc = market("BTCUSDT", 11)
    eth = market("ETHUSDT", 12)
    store = XPairStore(tmp_path / "xpair.db")
    store.record_capture(
        btc=btc,
        eth=eth,
        signal_at="2026-08-03T00:00:00+00:00",
        seconds_left=180.0,
        requested_stake=10.0,
        btc_book_age_ms=10.0,
        eth_book_age_ms=10.0,
        cross_book_skew_ms=5.0,
        trials=[
            {
                "variant": "BTC_UP_ETH_DOWN",
                "btc_side": "UP",
                "eth_side": "DOWN",
                "entry_status": "ELIGIBLE",
                "eligible": True,
                "requested_shares": 10.0,
                "filled_shares": 10.0,
                "fill_ratio": 1.0,
                "btc_vwap": 0.30,
                "eth_vwap": 0.60,
                "btc_fee": 0.02,
                "eth_fee": 0.02,
                "total_cost": 9.04,
                "cost_per_share": 0.904,
            },
            {
                "variant": "BTC_DOWN_ETH_UP",
                "btc_side": "DOWN",
                "eth_side": "UP",
                "entry_status": "SKIPPED_PRICE",
                "eligible": False,
                "rejection_reason": "TOP_COST_ABOVE_LIMIT",
            },
        ],
    )
    store.settle_pair(
        btc_market_id=btc.market_id,
        eth_market_id=eth.market_id,
        btc_winner="DOWN",
        eth_winner="UP",
        settled_at="2026-08-03T00:05:00+00:00",
    )
    summary = store.summary()
    arm = summary["variants"]["BTC_UP_ETH_DOWN"]
    assert arm["eligible_settled"] == 1
    assert arm["eligible_double_losses"] == 1
    assert arm["eligible_double_loss_rate"] == 1.0
    assert arm["eligible_pnl"] == pytest.approx(-9.04)
    lower, upper = arm["eligible_double_loss_ci95"]
    assert 0.0 < lower < upper <= 1.0
    store.close()


def test_wilson_interval_is_empty_without_samples():
    assert wilson_interval(0, 0) == (None, None)
