from __future__ import annotations

from pathlib import Path

import pytest

from predict_bot.xpair_btc_eth_paper import MarketRef, XPairStore, utc_iso
from predict_bot.xpair_dashboard_paper_sim import (
    captured_variants,
    paper_dashboard_payload,
    selected_preview_trial,
    trial_for_store,
)


def market(symbol: str, topic_id: int, market_id: int) -> MarketRef:
    return MarketRef(
        symbol=symbol,
        topic_id=topic_id,
        market_id=market_id,
        title=symbol,
        start_ms=1_000,
        end_ms=301_000,
        start_price=100.0,
        fee_bps=0,
        up_token_id=f"{symbol}-up",
        down_token_id=f"{symbol}-down",
    )


def analysis() -> dict:
    return {
        "selectedVariant": "BTC_DOWN_ETH_UP",
        "trials": [
            {
                "variant": "BTC_UP_ETH_DOWN",
                "eligible": True,
                "entryStatus": "ELIGIBLE",
                "filledShares": 4.0,
                "costPerShare": 0.94,
                "btcVwap": 0.44,
                "ethVwap": 0.50,
            },
            {
                "variant": "BTC_DOWN_ETH_UP",
                "eligible": True,
                "entryStatus": "ELIGIBLE",
                "filledShares": 4.0,
                "costPerShare": 0.95,
                "btcVwap": 0.40,
                "ethVwap": 0.55,
            },
        ],
    }


def paper_trial(variant: str) -> dict:
    chosen = selected_preview_trial(analysis(), variant)
    trial = trial_for_store(chosen or {})
    assert trial is not None
    return trial


def record_dual_market(
    store: XPairStore,
    *,
    btc: MarketRef,
    eth: MarketRef,
    seconds_left: float = 250.0,
) -> None:
    store.record_capture(
        btc=btc,
        eth=eth,
        signal_at=utc_iso(),
        seconds_left=seconds_left,
        requested_stake=4.0,
        btc_book_age_ms=100.0,
        eth_book_age_ms=100.0,
        cross_book_skew_ms=100.0,
        trials=[
            paper_trial("BTC_DOWN_ETH_UP"),
            paper_trial("BTC_UP_ETH_DOWN"),
        ],
    )


def test_each_direction_can_be_selected_independently() -> None:
    down = selected_preview_trial(analysis(), "BTC_DOWN_ETH_UP")
    up = selected_preview_trial(analysis(), "BTC_UP_ETH_DOWN")
    assert down is not None and down["variant"] == "BTC_DOWN_ETH_UP"
    assert up is not None and up["variant"] == "BTC_UP_ETH_DOWN"


def test_cheapest_mode_still_uses_monitor_selected_variant() -> None:
    chosen = selected_preview_trial(analysis(), "CHEAPEST_ELIGIBLE")
    assert chosen is not None
    assert chosen["variant"] == "BTC_DOWN_ETH_UP"


def test_preview_converts_both_variants_to_independent_trades() -> None:
    down = paper_trial("BTC_DOWN_ETH_UP")
    up = paper_trial("BTC_UP_ETH_DOWN")
    assert down["btc_side"] == "DOWN"
    assert down["eth_side"] == "UP"
    assert down["total_cost"] == pytest.approx(3.8)
    assert up["btc_side"] == "UP"
    assert up["eth_side"] == "DOWN"
    assert up["total_cost"] == pytest.approx(3.76)


def test_capture_tracking_is_per_variant_not_whole_market(tmp_path: Path) -> None:
    store = XPairStore(tmp_path / "xpair.db")
    btc = market("BTCUSDT", 1, 11)
    eth = market("ETHUSDT", 2, 22)
    try:
        store.record_capture(
            btc=btc,
            eth=eth,
            signal_at=utc_iso(),
            seconds_left=260.0,
            requested_stake=4.0,
            btc_book_age_ms=100.0,
            eth_book_age_ms=100.0,
            cross_book_skew_ms=100.0,
            trials=[paper_trial("BTC_DOWN_ETH_UP")],
        )
        assert captured_variants(
            store.db,
            btc_market_id=btc.market_id,
            eth_market_id=eth.market_id,
        ) == {"BTC_DOWN_ETH_UP"}

        store.record_capture(
            btc=btc,
            eth=eth,
            signal_at=utc_iso(),
            seconds_left=230.0,
            requested_stake=4.0,
            btc_book_age_ms=100.0,
            eth_book_age_ms=100.0,
            cross_book_skew_ms=100.0,
            trials=[paper_trial("BTC_UP_ETH_DOWN")],
        )
        assert captured_variants(
            store.db,
            btc_market_id=btc.market_id,
            eth_market_id=eth.market_id,
        ) == {"BTC_DOWN_ETH_UP", "BTC_UP_ETH_DOWN"}
    finally:
        store.close()


def test_dashboard_compares_dual_variant_outcomes_pnl_and_roi(tmp_path: Path) -> None:
    path = tmp_path / "xpair.db"
    store = XPairStore(path)
    btc1 = market("BTCUSDT", 1, 11)
    eth1 = market("ETHUSDT", 2, 22)
    btc2 = market("BTCUSDT", 3, 33)
    eth2 = market("ETHUSDT", 4, 44)
    try:
        record_dual_market(store, btc=btc1, eth=eth1, seconds_left=260.0)
        # BTC UP + ETH DOWN: DOWN/UP variant double-loses, inverse variant double-wins.
        store.settle_pair(
            btc_market_id=btc1.market_id,
            eth_market_id=eth1.market_id,
            btc_winner="UP",
            eth_winner="DOWN",
            settled_at=utc_iso(),
        )

        record_dual_market(store, btc=btc2, eth=eth2, seconds_left=210.0)
        # BTC DOWN + ETH DOWN gives each opposite-pair variant exactly one win.
        store.settle_pair(
            btc_market_id=btc2.market_id,
            eth_market_id=eth2.market_id,
            btc_winner="DOWN",
            eth_winner="DOWN",
            settled_at=utc_iso(),
        )
    finally:
        store.close()

    payload = paper_dashboard_payload(path)
    overall = payload["summary"]
    variants = payload["variants"]
    comparison = payload["comparison"]
    down = variants["BTC_DOWN_ETH_UP"]
    up = variants["BTC_UP_ETH_DOWN"]

    assert overall["captured"] == 4
    assert overall["settled"] == 4
    assert comparison["uniqueMarkets"] == 2
    assert comparison["bothVariantsCapturedMarkets"] == 2
    assert comparison["bothVariantsSettledMarkets"] == 2

    assert down["settled"] == 2
    assert down["oneWin"] == 1
    assert down["twoWins"] == 0
    assert down["doubleLosses"] == 1
    assert down["pnlUsdt"] == pytest.approx(-3.6)
    assert down["roi"] == pytest.approx(-3.6 / 7.6)

    assert up["settled"] == 2
    assert up["oneWin"] == 1
    assert up["twoWins"] == 1
    assert up["doubleLosses"] == 0
    assert up["pnlUsdt"] == pytest.approx(4.48)
    assert up["roi"] == pytest.approx(4.48 / 7.52)

    assert comparison["downMinusUpPnlUsdt"] == pytest.approx(-8.08)
    assert comparison["downMinusUpRoi"] == pytest.approx(
        (-3.6 / 7.6) - (4.48 / 7.52)
    )
    assert comparison["downMinusUpDoubleLossRate"] == pytest.approx(0.5)
