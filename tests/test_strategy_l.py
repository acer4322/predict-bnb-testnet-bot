import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from predict_bot.core import taker_fee
from predict_bot.server import Store


def l_snapshot(*, market_id: int, seconds_left: float = 300.0, **overrides):
    row = {
        "timestamp": "2026-07-16T00:00:00+00:00",
        "topic_id": 40_000 + market_id,
        "market_id": market_id,
        "title": "BTC Up or Down 5m",
        "start_price": 65_000.0,
        "spot_price": 65_000.0,
        "seconds_left": seconds_left,
        "up_ask": 0.50,
        "up_bid": 0.49,
        "down_ask": 0.50,
        "down_bid": 0.49,
        "up_ask_size": 100.0,
        "up_bid_size": 100.0,
        "down_ask_size": 100.0,
        "down_bid_size": 100.0,
        "book_skew_ms": 100.0,
        "book_age_ms": 250.0,
    }
    row.update(overrides)
    return row


def l_trades(store: Store, market_id: int):
    return store.db.execute(
        "SELECT * FROM trades WHERE strategy='L' AND market_id=? ORDER BY id",
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


def test_l_opens_both_half_budget_legs_once_at_start(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    row = l_snapshot(market_id=901)

    store.maybe_enter(row, fee_bps=200)
    store.maybe_enter(row, fee_bps=200)

    trades = l_trades(store, 901)
    assert [trade["side"] for trade in trades] == ["UP", "DOWN"]
    assert len(trades) == 2
    for trade in trades:
        assert trade["strategy_version"] == "L_v1_two_leg_target"
        assert trade["entry_price"] == pytest.approx(0.50)
        assert trade["target_price"] == pytest.approx(0.70)
        assert trade["stake"] == pytest.approx(5.0)
        assert trade["shares"] == pytest.approx(10.0)
        assert trade["fees"] == pytest.approx(taker_fee(10.0, 0.50, 200))
        diagnostics = json.loads(trade["diagnostics_json"])
        assert diagnostics["leg"] == trade["side"]
        assert diagnostics["total_budget"] == pytest.approx(10.0)
        assert diagnostics["leg_budget"] == pytest.approx(5.0)
        assert diagnostics["requested_stake"] == pytest.approx(5.0)
        assert diagnostics["filled_stake"] == pytest.approx(5.0)
        assert diagnostics["filled_shares"] == pytest.approx(10.0)
        assert diagnostics["fill_ratio"] == pytest.approx(1.0)
        assert diagnostics["partial_fill"] is False
        assert diagnostics["config"]["strategy_l_total_stake"] == pytest.approx(10.0)


def test_l_legs_can_enter_on_different_snapshots_without_duplicate_side(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    up_only = l_snapshot(market_id=902, seconds_left=280.0, down_ask=0.51)
    store.maybe_enter(up_only, fee_bps=200)
    store.maybe_enter(up_only, fee_bps=200)
    assert [trade["side"] for trade in l_trades(store, 902)] == ["UP"]

    down_later = l_snapshot(
        market_id=902,
        seconds_left=250.0,
        up_ask=0.48,
        up_bid=0.47,
        down_ask=0.50,
        down_bid=0.49,
    )
    store.maybe_enter(down_later, fee_bps=200)
    store.maybe_enter(down_later, fee_bps=200)

    trades = l_trades(store, 902)
    assert [trade["side"] for trade in trades] == ["UP", "DOWN"]
    assert trades[0]["entry_price"] == pytest.approx(0.50)
    assert trades[1]["entry_price"] == pytest.approx(0.50)


def test_l_entry_boundaries_partial_fill_and_depth_requirement(tmp_path: Path):
    store = Store(tmp_path / "sim.db")

    store.maybe_enter(l_snapshot(market_id=903, seconds_left=240.0), fee_bps=200)
    assert len(l_trades(store, 903)) == 2  # elapsed == 60 is included

    store.maybe_enter(l_snapshot(market_id=904, seconds_left=239.999), fee_bps=200)
    assert l_trades(store, 904) == []

    too_expensive = l_snapshot(
        market_id=905, up_ask=0.5001, up_bid=0.49, down_ask=0.60, down_bid=0.59
    )
    store.maybe_enter(too_expensive, fee_bps=200)
    assert l_trades(store, 905) == []

    no_depth = l_snapshot(
        market_id=906, up_ask_size=None, down_ask=0.60, down_bid=0.59
    )
    store.maybe_enter(no_depth, fee_bps=200)
    assert l_trades(store, 906) == []

    partial = l_snapshot(
        market_id=907,
        up_ask_size=4.0,
        down_ask=0.60,
        down_bid=0.59,
    )
    store.maybe_enter(partial, fee_bps=200)
    trade = l_trades(store, 907)[0]
    diagnostics = json.loads(trade["diagnostics_json"])
    assert trade["side"] == "UP"
    assert trade["shares"] == pytest.approx(4.0)
    assert trade["stake"] == pytest.approx(2.0)
    assert diagnostics["requested_shares"] == pytest.approx(10.0)
    assert diagnostics["filled_shares"] == pytest.approx(4.0)
    assert diagnostics["fill_ratio"] == pytest.approx(0.4)
    assert diagnostics["partial_fill"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"book_age_ms": 2000.001},
        {"book_skew_ms": 500.001},
        {"up_bid": 0.51},
        {"down_bid": None},
        {"up_ask": math.nan},
        {"down_bid": -0.01},
    ],
)
def test_l_rejects_stale_skewed_crossed_or_invalid_books(
    tmp_path: Path, overrides: dict
):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(l_snapshot(market_id=908, **overrides), fee_bps=200)
    assert l_trades(store, 908) == []


def test_l_target_uses_frozen_limit_full_depth_and_entry_fee_rate(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(l_snapshot(market_id=909), fee_bps=200)

    below_target = l_snapshot(
        market_id=909, seconds_left=200.0, up_bid=0.699, up_bid_size=100.0
    )
    store.process_strategy_l_targets(below_target, fee_bps=999)
    assert l_trades(store, 909)[0]["status"] == "OPEN"

    at_better_bid = l_snapshot(
        market_id=909,
        seconds_left=199.0,
        up_ask=0.74,
        up_bid=0.73,
        up_bid_size=10.0,
        down_ask=0.70,
        down_bid=0.69,
    )
    store.process_strategy_l_targets(at_better_bid, fee_bps=999)

    up, down = l_trades(store, 909)
    assert up["status"] == "TARGET_FILLED"
    assert up["exit_price"] == pytest.approx(0.70)
    entry_fee = taker_fee(10.0, 0.50, 200)
    exit_fee = taker_fee(10.0, 0.70, 200)
    assert up["fees"] == pytest.approx(entry_fee + exit_fee)
    assert up["pnl"] == pytest.approx(10.0 * 0.70 - 5.0 - entry_fee - exit_fee)
    assert down["status"] == "OPEN"
    diagnostics = json.loads(up["diagnostics_json"])["target_exit"]
    assert diagnostics["execution_model"] == "observed_bid_full_depth_limit_v1"
    assert diagnostics["target"] == pytest.approx(0.70)
    assert diagnostics["observed_bid"] == pytest.approx(0.73)
    assert diagnostics["visible_bid_size"] == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("raw_size", "reason"),
    [
        (None, "missing_bid_size"),
        (0.0, "invalid_bid_size"),
        (math.nan, "invalid_bid_size"),
        (9.999, "insufficient_bid_size"),
    ],
)
def test_l_target_waits_for_known_complete_bid_depth(
    tmp_path: Path, raw_size: float | None, reason: str
):
    store = Store(tmp_path / "sim.db")
    entry = l_snapshot(
        market_id=910,
        down_ask=0.60,
        down_bid=0.59,
    )
    store.maybe_enter(entry, fee_bps=200)

    target = l_snapshot(
        market_id=910,
        seconds_left=200.0,
        up_ask=0.71,
        up_bid=0.70,
        up_bid_size=raw_size,
        down_ask=0.60,
        down_bid=0.59,
    )
    store.process_strategy_l_targets(target, fee_bps=200)

    trade = l_trades(store, 910)[0]
    assert trade["status"] == "OPEN"
    blocked = json.loads(trade["diagnostics_json"])["target_exit_blocked"]
    assert blocked["reason"] == reason
    assert blocked["target"] == pytest.approx(0.70)
    assert blocked["observed_bid"] == pytest.approx(0.70)
    assert blocked["required_shares"] == pytest.approx(10.0)


def test_l_targeted_leg_is_not_rewritten_when_other_leg_settles(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(l_snapshot(market_id=911), fee_bps=200)
    store.process_strategy_l_targets(
        l_snapshot(
            market_id=911,
            seconds_left=200.0,
            up_ask=0.72,
            up_bid=0.70,
            up_bid_size=100.0,
            down_ask=0.70,
            down_bid=0.69,
        ),
        fee_bps=200,
    )
    targeted_before = dict(l_trades(store, 911)[0])

    store.settle_market(911, winner="DOWN", official=False)
    store.settle_market(911, winner="DOWN", official=True)

    up, down = l_trades(store, 911)
    assert dict(up) == targeted_before
    assert up["status"] == "TARGET_FILLED"
    assert down["status"] == "SETTLED_WIN"


def test_l_reset_keeps_both_carried_legs_outside_new_measurement(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(l_snapshot(market_id=912), fee_bps=200)
    reset = store.reset_strategy_measurement("L")

    summary = store.dashboard(collector_stub())["summaries"]["L"]
    assert summary["trades"] == 0
    assert summary["carriedOpen"] == 2
    assert summary["totalOpen"] == 2
    assert summary["cutoffTradeId"] == reset["cutoffTradeId"]

    store.process_strategy_l_targets(
        l_snapshot(
            market_id=912,
            seconds_left=200.0,
            up_ask=0.72,
            up_bid=0.70,
            up_bid_size=100.0,
            down_ask=0.70,
            down_bid=0.69,
        ),
        fee_bps=200,
    )
    summary = store.dashboard(collector_stub())["summaries"]["L"]
    assert summary["trades"] == 0
    assert summary["wins"] == 0
    assert summary["losses"] == 0
    assert summary["realized_pnl"] == 0
    assert summary["carriedOpen"] == 1

    store.settle_market(912, winner="UP", official=True)
    summary = store.dashboard(collector_stub())["summaries"]["L"]
    assert summary["trades"] == 0
    assert summary["realized_pnl"] == 0
    assert summary["carriedOpen"] == 0
    assert summary["totalOpen"] == 0


@pytest.mark.parametrize(
    "values",
    [
        {"strategy_l_max_entry": 0},
        {"strategy_l_max_entry": 0.50, "strategy_l_target": 0.50},
        {"strategy_l_target": 1.01},
        {"strategy_l_total_stake": 0},
        {"strategy_l_window_seconds": 0},
        {"strategy_l_window_seconds": 300.01},
        {"strategy_l_max_book_skew_ms": 0},
        {"strategy_l_max_book_age_ms": math.nan},
    ],
)
def test_l_config_validation(tmp_path: Path, values: dict):
    store = Store(tmp_path / "sim.db")
    with pytest.raises(ValueError):
        store.update_config(values)

