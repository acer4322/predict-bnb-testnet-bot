from __future__ import annotations

from pathlib import Path

from predict_bot.xpair_btc_eth_paper import MarketRef, XPairStore, utc_iso
from predict_bot.xpair_dashboard_paper_sim import (
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


def test_fixed_direction_uses_only_selected_variant() -> None:
    chosen = selected_preview_trial(analysis(), "BTC_DOWN_ETH_UP")
    assert chosen is not None
    assert chosen["variant"] == "BTC_DOWN_ETH_UP"


def test_cheapest_mode_uses_monitor_selected_variant() -> None:
    chosen = selected_preview_trial(analysis(), "CHEAPEST_ELIGIBLE")
    assert chosen is not None
    assert chosen["variant"] == "BTC_DOWN_ETH_UP"


def test_preview_converts_to_single_paper_trade() -> None:
    chosen = selected_preview_trial(analysis(), "BTC_DOWN_ETH_UP")
    trial = trial_for_store(chosen or {})
    assert trial is not None
    assert trial["btc_side"] == "DOWN"
    assert trial["eth_side"] == "UP"
    assert trial["filled_shares"] == 4.0
    assert trial["total_cost"] == 3.8


def test_summary_counts_one_win_two_wins_double_loss_and_roi(tmp_path: Path) -> None:
    path = tmp_path / "xpair.db"
    store = XPairStore(path)
    btc = market("BTCUSDT", 1, 11)
    eth = market("ETHUSDT", 2, 22)
    try:
        base_trial = trial_for_store(
            selected_preview_trial(analysis(), "BTC_DOWN_ETH_UP") or {}
        )
        assert base_trial is not None
        store.record_capture(
            btc=btc,
            eth=eth,
            signal_at=utc_iso(),
            seconds_left=250.0,
            requested_stake=4.0,
            btc_book_age_ms=100.0,
            eth_book_age_ms=100.0,
            cross_book_skew_ms=100.0,
            trials=[base_trial],
        )
        store.settle_pair(
            btc_market_id=btc.market_id,
            eth_market_id=eth.market_id,
            btc_winner="DOWN",
            eth_winner="DOWN",
            settled_at=utc_iso(),
        )
    finally:
        store.close()

    payload = paper_dashboard_payload(path)
    summary = payload["summary"]
    assert summary["captured"] == 1
    assert summary["settled"] == 1
    assert summary["pending"] == 0
    assert summary["oneWin"] == 1
    assert summary["twoWins"] == 0
    assert summary["doubleLosses"] == 0
    assert summary["pnlUsdt"] == 0.2
    assert round(summary["roi"], 8) == round(0.2 / 3.8, 8)
