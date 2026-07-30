import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from predict_bot.core import taker_fee
from predict_bot.server import Store


def m_snapshot(*, market_id: int, seconds_left: float = 300.0, **overrides):
    row = {
        "timestamp": "2026-07-17T00:00:00+00:00",
        "topic_id": 50_000 + market_id,
        "market_id": market_id,
        "title": "BTC Up or Down 5m",
        "start_price": 65_000.0,
        "spot_price": 65_001.0,
        "seconds_left": seconds_left,
        # The opposite side is deliberately much cheaper. M must still use
        # only the sign of spot minus official start to choose its side.
        "up_ask": 0.99,
        "up_bid": 0.98,
        "down_ask": 0.01,
        "down_bid": 0.00,
        "up_ask_size": 100.0,
        "up_bid_size": 100.0,
        "down_ask_size": 100.0,
        "down_bid_size": 100.0,
        "book_skew_ms": 100.0,
        "book_age_ms": 250.0,
    }
    row.update(overrides)
    return row


def m_trades(store: Store, market_id: int):
    return store.db.execute(
        "SELECT * FROM trades WHERE strategy='M' AND market_id=? ORDER BY id",
        (market_id,),
    ).fetchall()


def collector_stub():
    return SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=SimpleNamespace(last_rate_limits={}),
    )


def test_m_uses_spot_sign_only_and_opens_once_without_price_gate(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    row = m_snapshot(market_id=1001)

    store.maybe_enter(row, fee_bps=200)
    store.maybe_enter(row, fee_bps=200)

    trades = m_trades(store, 1001)
    assert len(trades) == 1
    trade = trades[0]
    assert trade["side"] == "UP"
    assert trade["entry_price"] == pytest.approx(0.99)
    assert trade["target_price"] is None
    assert trade["stake"] == pytest.approx(10.0)
    assert trade["strategy_version"] == "M_v1_opening_spot_direction_hold"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["signal_source"] == "binance_spot_minus_official_start"
    assert diagnostics["signal_cross_source"] is True
    assert diagnostics["direction_uses_prediction_price"] is False
    assert diagnostics["prediction_market_role"] == "execution_only"
    assert diagnostics["signal_side"] == "UP"
    assert diagnostics["official_strike"] == pytest.approx(65_000.0)
    assert diagnostics["current_spot"] == pytest.approx(65_001.0)
    assert diagnostics["distance"] == pytest.approx(1.0)
    assert diagnostics["distance_bps"] == pytest.approx(1 / 65_000 * 10_000)
    assert diagnostics["quoted_ask"] == pytest.approx(0.99)
    assert diagnostics["quoted_bid"] == pytest.approx(0.98)
    assert diagnostics["requested_stake"] == pytest.approx(10.0)
    assert diagnostics["filled_stake"] == pytest.approx(10.0)
    assert diagnostics["config"]["strategy_m_stake"] == pytest.approx(10.0)


def test_m_down_signal_cannot_be_flipped_by_cheaper_up_contract(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    row = m_snapshot(
        market_id=1002,
        spot_price=64_999.0,
        up_ask=0.01,
        up_bid=0.00,
        down_ask=0.97,
        down_bid=0.96,
    )
    store.maybe_enter(row, fee_bps=200)

    trade = m_trades(store, 1002)[0]
    assert trade["side"] == "DOWN"
    assert trade["entry_price"] == pytest.approx(0.97)
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["signal_delta"] == pytest.approx(-1.0)
    assert diagnostics["signal_delta_bps"] < 0


def test_m_tie_or_invalid_execution_waits_and_recomputes_next_snapshot(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    tie = m_snapshot(market_id=1003, spot_price=65_000.0)
    store.maybe_enter(tie, fee_bps=200)
    assert m_trades(store, 1003) == []

    no_up_depth = m_snapshot(
        market_id=1003, seconds_left=295.0, spot_price=65_002.0, up_ask_size=None
    )
    store.maybe_enter(no_up_depth, fee_bps=200)
    assert m_trades(store, 1003) == []

    # The signal is not persisted: a later valid snapshot may select DOWN.
    down_later = m_snapshot(
        market_id=1003,
        seconds_left=291.0,
        spot_price=64_998.0,
        down_ask=0.80,
        down_bid=0.79,
    )
    store.maybe_enter(down_later, fee_bps=200)
    trade = m_trades(store, 1003)[0]
    assert trade["side"] == "DOWN"
    assert json.loads(trade["diagnostics_json"])["elapsed_seconds"] == pytest.approx(9.0)


@pytest.mark.parametrize(
    ("seconds_left", "enters"),
    [
        (300.0, True),
        (290.0, True),
        (300.001, False),
        (289.999, False),
    ],
)
def test_m_five_minute_opening_window_boundaries(
    tmp_path: Path, seconds_left: float, enters: bool
):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(
        m_snapshot(market_id=1004, seconds_left=seconds_left), fee_bps=200
    )
    assert bool(m_trades(store, 1004)) is enters


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_price": 0.0},
        {"start_price": -1.0},
        {"start_price": math.nan},
        {"start_price": math.inf},
        {"spot_price": 0.0},
        {"spot_price": -1.0},
        {"spot_price": math.nan},
        {"spot_price": math.inf},
        {"spot_price": 65_000.0},
    ],
)
def test_m_rejects_invalid_or_equal_signal_prices(tmp_path: Path, overrides: dict):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(m_snapshot(market_id=1005, **overrides), fee_bps=200)
    assert m_trades(store, 1005) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"up_ask": None},
        {"up_ask": 0.0},
        {"up_ask": 1.01},
        {"up_ask": math.nan},
        {"up_bid": None},
        {"up_bid": -0.01},
        {"up_bid": 1.0},  # crossed above the default 0.99 ask
        {"up_bid": math.inf},
        {"up_ask_size": None},
        {"up_ask_size": 0.0},
        {"up_ask_size": -1.0},
        {"up_ask_size": math.nan},
        {"up_ask_size": math.inf},
        {"down_ask": None},
        {"down_ask": math.nan},
        {"down_bid": None},
        {"down_bid": -0.01},
        {"down_bid": 0.02},  # crossed above the default 0.01 ask
        {"book_age_ms": None},
        {"book_age_ms": -1.0},
        {"book_age_ms": 2000.001},
        {"book_age_ms": math.nan},
        {"book_skew_ms": None},
        {"book_skew_ms": -1.0},
        {"book_skew_ms": 500.001},
        {"book_skew_ms": math.inf},
    ],
)
def test_m_rejects_invalid_selected_execution_book(tmp_path: Path, overrides: dict):
    store = Store(tmp_path / "sim.db")
    candidate = store.strategy_m_entry_candidate(
        m_snapshot(market_id=1006, **overrides), store.config()
    )
    assert candidate is None


def test_m_accepts_selected_ask_one_without_a_price_gate(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    row = m_snapshot(
        market_id=1007,
        up_ask=1.0,
        up_bid=1.0,
    )
    store.maybe_enter(row, fee_bps=200)
    trade = m_trades(store, 1007)[0]
    assert trade["side"] == "UP"
    assert trade["entry_price"] == pytest.approx(1.0)


def test_m_caps_partial_fill_at_visible_selected_ask_depth(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    row = m_snapshot(market_id=1008, up_ask=0.80, up_bid=0.79, up_ask_size=4.0)
    store.maybe_enter(row, fee_bps=200)

    trade = m_trades(store, 1008)[0]
    diagnostics = json.loads(trade["diagnostics_json"])
    assert trade["shares"] == pytest.approx(4.0)
    assert trade["stake"] == pytest.approx(3.2)
    assert trade["fees"] == pytest.approx(taker_fee(4.0, 0.80, 200))
    assert diagnostics["requested_stake"] == pytest.approx(10.0)
    assert diagnostics["requested_shares"] == pytest.approx(12.5)
    assert diagnostics["filled_stake"] == pytest.approx(3.2)
    assert diagnostics["filled_shares"] == pytest.approx(4.0)
    assert diagnostics["fill_ratio"] == pytest.approx(4.0 / 12.5)
    assert diagnostics["partial_fill"] is True


def test_m_holds_without_exit_fee_until_existing_settlement(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(
        m_snapshot(market_id=1009, up_ask=0.80, up_bid=0.79), fee_bps=200
    )
    before = m_trades(store, 1009)[0]
    shares = float(before["shares"])
    entry_fee = taker_fee(shares, 0.80, 200)

    store.settle_market(1009, winner="UP", official=False)
    proxy = m_trades(store, 1009)[0]
    assert proxy["status"] == "SETTLED_WIN"
    assert proxy["fees"] == pytest.approx(entry_fee)
    assert proxy["pnl"] == pytest.approx(shares - 10.0 - entry_fee)

    store.settle_market(1009, winner="UP", official=True)
    official = m_trades(store, 1009)[0]
    assert official["status"] == "SETTLED_WIN"
    assert official["fees"] == pytest.approx(entry_fee)
    assert official["pnl"] == pytest.approx(shares - 10.0 - entry_fee)
    assert "official endPrice reconciliation" in official["note"]


def test_m_reset_carried_open_settles_outside_new_measurement(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(m_snapshot(market_id=1010), fee_bps=200)
    reset = store.reset_strategy_measurement("M")

    summary = store.dashboard(collector_stub())["summaries"]["M"]
    assert summary["trades"] == 0
    assert summary["carriedOpen"] == 1
    assert summary["totalOpen"] == 1
    assert summary["cutoffTradeId"] == reset["cutoffTradeId"]

    store.settle_market(1010, winner="DOWN", official=True)
    summary = store.dashboard(collector_stub())["summaries"]["M"]
    assert summary["trades"] == 0
    assert summary["wins"] == 0
    assert summary["losses"] == 0
    assert summary["realized_pnl"] == 0
    assert summary["carriedOpen"] == 0
    assert summary["totalOpen"] == 0


def test_m_can_be_disabled_and_has_zero_summary(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.update_config({"strategy_m_enabled": False})
    store.maybe_enter(m_snapshot(market_id=1011), fee_bps=200)
    assert m_trades(store, 1011) == []
    assert store.dashboard(collector_stub())["summaries"]["M"]["trades"] == 0


@pytest.mark.parametrize(
    "values",
    [
        {"strategy_m_entry_window_seconds": 0},
        {"strategy_m_entry_window_seconds": 300.01},
        {"strategy_m_entry_window_seconds": math.nan},
        {"strategy_m_stake": 0},
        {"strategy_m_stake": math.inf},
        {"strategy_m_max_book_skew_ms": 0},
        {"strategy_m_max_book_age_ms": math.nan},
    ],
)
def test_m_config_validation(tmp_path: Path, values: dict):
    store = Store(tmp_path / "sim.db")
    with pytest.raises(ValueError):
        store.update_config(values)
