import json
from pathlib import Path

import pytest

from predict_bot.core import taker_fee
from predict_bot.server import Store


def b2_snapshot(*, market_id: int, seconds_left: float, **overrides):
    row = {
        "timestamp": "2026-07-16T00:04:40+00:00",
        "topic_id": 40_000 + market_id,
        "market_id": market_id,
        "title": "BTC Up or Down 5m",
        "start_price": 65_000.0,
        "spot_price": 65_010.0,
        "seconds_left": seconds_left,
        "up_ask": 0.92,
        "up_bid": 0.91,
        "down_ask": 0.08,
        "down_bid": 0.07,
        "up_ask_size": 1_000.0,
        "up_bid_size": 1_000.0,
        "down_ask_size": 1_000.0,
        "down_bid_size": 1_000.0,
        "book_skew_ms": 100.0,
        "book_age_ms": 250.0,
    }
    row.update(overrides)
    return row


def b2_trade(store: Store, market_id: int):
    return store.db.execute(
        "SELECT * FROM trades WHERE strategy='B2' AND market_id=?", (market_id,)
    ).fetchone()


def test_b2_stake_increases_as_settlement_approaches(tmp_path: Path):
    store = Store(tmp_path / "sim.db")

    for market_id, seconds_left in ((501, 20.0), (502, 11.5), (503, 3.0)):
        store.maybe_enter(
            b2_snapshot(market_id=market_id, seconds_left=seconds_left),
            fee_bps=200,
        )

    early = b2_trade(store, 501)
    middle = b2_trade(store, 502)
    latest = b2_trade(store, 503)
    assert early["stake"] == pytest.approx(5.0)
    assert middle["stake"] == pytest.approx(8.75)
    assert latest["stake"] == pytest.approx(20.0)
    assert early["stake"] < middle["stake"] < latest["stake"]

    middle_diagnostics = json.loads(middle["diagnostics_json"])
    assert middle["strategy_version"] == "B2_v1_time_scaled_stop_loss"
    assert middle_diagnostics["progress"] == pytest.approx(0.5)
    assert middle_diagnostics["risk_level"] == pytest.approx(0.5)
    assert middle_diagnostics["size_curve"] == pytest.approx(2.0)
    assert middle_diagnostics["requested_stake"] == pytest.approx(8.75)
    assert middle_diagnostics["filled_stake"] == pytest.approx(8.75)
    assert middle_diagnostics["fill_ratio"] == pytest.approx(1.0)
    assert middle_diagnostics["partial_fill"] is False

    store.maybe_enter(b2_snapshot(market_id=504, seconds_left=2.9), fee_bps=200)
    assert b2_trade(store, 504) is None


def test_b2_stop_loss_executes_at_bid_with_both_taker_fees(tmp_path: Path):
    path = tmp_path / "sim.db"
    store = Store(path)
    entry = b2_snapshot(market_id=510, seconds_left=10.0)
    store.maybe_enter(entry, fee_bps=200)
    opened = b2_trade(store, 510)

    stop = b2_snapshot(
        market_id=510,
        seconds_left=9.0,
        up_ask=0.61,
        up_bid=0.59,
        up_bid_size=1_000.0,
        down_ask=0.41,
        down_bid=0.39,
    )
    store.process_b2_stop_loss(stop, fee_bps=200)
    closed = b2_trade(store, 510)

    entry_fee = taker_fee(opened["shares"], opened["entry_price"], 200)
    exit_fee = taker_fee(opened["shares"], 0.59, 200)
    expected_pnl = opened["shares"] * 0.59 - opened["stake"] - entry_fee - exit_fee
    assert closed["status"] == "STOP_LOSS_EXIT"
    assert closed["exit_price"] == pytest.approx(0.59)
    assert closed["fees"] == pytest.approx(entry_fee + exit_fee)
    assert closed["pnl"] == pytest.approx(expected_pnl)
    assert closed["pnl"] < 0
    stop_diagnostics = json.loads(closed["diagnostics_json"])["stop_loss"]
    assert stop_diagnostics["threshold"] == pytest.approx(0.60)
    assert stop_diagnostics["execution_bid"] == pytest.approx(0.59)

    # An official result must not overwrite a position already closed by B2.
    store.settle_market(510, winner="UP", official=True)
    assert b2_trade(store, 510)["status"] == "STOP_LOSS_EXIT"

    # Startup accounting repair must preserve the two-sided stop-loss fee model.
    reopened = Store(path)
    repaired = b2_trade(reopened, 510)
    assert repaired["fees"] == pytest.approx(entry_fee + exit_fee)
    assert repaired["pnl"] == pytest.approx(expected_pnl)


def test_b2_entry_is_capped_at_visible_top_ask_depth(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    shallow_entry = b2_snapshot(
        market_id=515,
        seconds_left=3.0,
        up_ask_size=10.0,
    )
    store.maybe_enter(shallow_entry, fee_bps=200)
    trade = b2_trade(store, 515)
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["requested_stake"] == pytest.approx(20.0)
    assert trade["shares"] == pytest.approx(10.0)
    assert trade["stake"] == pytest.approx(9.2)
    assert diagnostics["partial_fill"] is True
    assert diagnostics["fill_ratio"] < 1.0


def test_b2_does_not_stop_at_threshold_and_can_settle_normally(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(b2_snapshot(market_id=520, seconds_left=8.0), fee_bps=200)
    at_threshold = b2_snapshot(
        market_id=520,
        seconds_left=7.0,
        up_ask=0.62,
        up_bid=0.60,
        down_ask=0.40,
        down_bid=0.38,
    )
    store.process_b2_stop_loss(at_threshold, fee_bps=200)
    assert b2_trade(store, 520)["status"] == "OPEN"

    store.settle_market(520, winner="UP", official=True)
    assert b2_trade(store, 520)["status"] == "SETTLED_WIN"


def test_b2_does_not_invent_full_stop_fill_when_bid_depth_is_too_small(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(b2_snapshot(market_id=530, seconds_left=8.0), fee_bps=200)
    opened = b2_trade(store, 530)
    assert opened["shares"] > 1.0

    shallow_stop = b2_snapshot(
        market_id=530,
        seconds_left=7.0,
        up_ask=0.61,
        up_bid=0.59,
        up_bid_size=1.0,
        down_ask=0.41,
        down_bid=0.39,
    )
    store.process_b2_stop_loss(shallow_stop, fee_bps=200)
    shallow_trade = b2_trade(store, 530)
    assert shallow_trade["status"] == "OPEN"
    blocked = json.loads(shallow_trade["diagnostics_json"])["stop_loss_blocked"]
    assert blocked["execution_bid"] == pytest.approx(0.59)
    assert blocked["visible_bid_size"] == pytest.approx(1.0)
    assert blocked["required_shares"] == pytest.approx(opened["shares"])
    assert blocked["observations"] == 1

    missing_depth = dict(shallow_stop)
    missing_depth["up_bid_size"] = None
    store.process_b2_stop_loss(missing_depth, fee_bps=200)
    missing_trade = b2_trade(store, 530)
    assert missing_trade["status"] == "OPEN"
    missing = json.loads(missing_trade["diagnostics_json"])["stop_loss_blocked"]
    assert missing["visible_bid_size"] is None
    assert missing["observations"] == 2


def test_b2_does_not_open_when_executable_bid_is_already_below_stop(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    already_stopped = b2_snapshot(
        market_id=535,
        seconds_left=8.0,
        up_ask=0.92,
        up_bid=0.59,
    )
    store.maybe_enter(already_stopped, fee_bps=200)
    assert b2_trade(store, 535) is None


def test_b2_config_rejects_reversed_time_and_stake_ranges(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    with pytest.raises(ValueError, match="minimum time"):
        store.update_config(
            {"strategy_b2_min_seconds_left": 20, "strategy_b2_last_seconds": 20}
        )
    with pytest.raises(ValueError, match="minimum stake"):
        store.update_config(
            {"strategy_b2_min_stake": 21, "strategy_b2_max_stake": 20}
        )
    with pytest.raises(ValueError, match="stop loss"):
        store.update_config(
            {"strategy_b2_stop_loss_price": 0.90, "strategy_b2_min_price": 0.90}
        )
