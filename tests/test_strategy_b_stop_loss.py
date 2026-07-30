import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from predict_bot.core import taker_fee
from predict_bot.server import Store


def b_snapshot(*, market_id: int, seconds_left: float = 19.0, **overrides):
    row = {
        "timestamp": "2026-07-16T00:04:41+00:00",
        "topic_id": 50_000 + market_id,
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


def trade(store: Store, strategy: str, market_id: int):
    return store.db.execute(
        "SELECT * FROM trades WHERE strategy=? AND market_id=? ORDER BY id LIMIT 1",
        (strategy, market_id),
    ).fetchone()


def collector_stub():
    return SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=SimpleNamespace(last_rate_limits={}),
    )


def test_b_keeps_fixed_stake_and_records_stop_loss_model_metadata(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(b_snapshot(market_id=801), fee_bps=200)
    opened = trade(store, "B", 801)
    diagnostics = json.loads(opened["diagnostics_json"])

    assert opened["stake"] == pytest.approx(10.0)
    assert opened["entry_price"] == pytest.approx(0.92)
    assert opened["strategy_version"] == "B_v2_stop_loss"
    assert diagnostics["quoted_ask"] == pytest.approx(0.92)
    assert diagnostics["quoted_bid"] == pytest.approx(0.91)
    assert diagnostics["stop_loss_price"] == pytest.approx(0.60)
    assert diagnostics["seconds_left"] == pytest.approx(19.0)


@pytest.mark.parametrize("bad_bid", [0.59, math.nan, 0.93])
def test_b_rejects_entry_when_held_side_bid_is_already_unsafe(
    tmp_path: Path, bad_bid: float
):
    store = Store(tmp_path / f"sim-{repr(bad_bid)}.db")
    store.maybe_enter(
        b_snapshot(market_id=802, up_bid=bad_bid),
        fee_bps=200,
    )
    assert trade(store, "B", 802) is None


def test_b_stop_executes_at_real_bid_with_both_fees_and_survives_restart(
    tmp_path: Path,
):
    path = tmp_path / "sim.db"
    store = Store(path)
    store.maybe_enter(b_snapshot(market_id=803), fee_bps=200)
    opened = trade(store, "B", 803)

    stop = b_snapshot(
        market_id=803,
        seconds_left=8.0,
        up_ask=0.61,
        up_bid=0.59,
        up_bid_size=1_000.0,
        down_ask=0.41,
        down_bid=0.39,
    )
    store.process_b_stop_loss(stop, fee_bps=200)
    closed = trade(store, "B", 803)
    entry_fee = taker_fee(opened["shares"], 0.92, 200)
    exit_fee = taker_fee(opened["shares"], 0.59, 200)
    expected_pnl = opened["shares"] * 0.59 - opened["stake"] - entry_fee - exit_fee

    assert closed["status"] == "STOP_LOSS_EXIT"
    assert closed["exit_price"] == pytest.approx(0.59)
    assert closed["fees"] == pytest.approx(entry_fee + exit_fee)
    assert closed["pnl"] == pytest.approx(expected_pnl)

    store.settle_market(803, winner="UP", official=True)
    assert trade(store, "B", 803)["status"] == "STOP_LOSS_EXIT"
    reopened = Store(path)
    repaired = trade(reopened, "B", 803)
    assert repaired["status"] == "STOP_LOSS_EXIT"
    assert repaired["fees"] == pytest.approx(entry_fee + exit_fee)
    assert repaired["pnl"] == pytest.approx(expected_pnl)


def test_b_stop_blocks_missing_or_shallow_depth_and_is_strictly_below_threshold(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(b_snapshot(market_id=804), fee_bps=200)

    missing_depth = b_snapshot(
        market_id=804, seconds_left=8.0,
        up_ask=0.61, up_bid=0.59, up_bid_size=None,
        down_ask=0.41, down_bid=0.39,
    )
    store.process_b_stop_loss(missing_depth, fee_bps=200)
    blocked = trade(store, "B", 804)
    blocked_diagnostics = json.loads(blocked["diagnostics_json"])["stop_loss_blocked"]
    assert blocked["status"] == "OPEN"
    assert blocked_diagnostics["reason"] == "missing_bid_size"
    assert blocked_diagnostics["observations"] == 1

    shallow_depth = dict(missing_depth, up_bid_size=1.0)
    store.process_b_stop_loss(shallow_depth, fee_bps=200)
    blocked = trade(store, "B", 804)
    blocked_diagnostics = json.loads(blocked["diagnostics_json"])["stop_loss_blocked"]
    assert blocked["status"] == "OPEN"
    assert blocked_diagnostics["reason"] == "insufficient_bid_size"
    assert blocked_diagnostics["observations"] == 2

    at_threshold = dict(missing_depth, up_bid=0.60, up_bid_size=1_000.0)
    store.process_b_stop_loss(at_threshold, fee_bps=200)
    assert trade(store, "B", 804)["status"] == "OPEN"

    executable = dict(missing_depth, up_bid_size=1_000.0)
    store.process_b_stop_loss(executable, fee_bps=200)
    assert trade(store, "B", 804)["status"] == "STOP_LOSS_EXIT"


def test_shared_processor_uses_each_trade_frozen_threshold_not_current_config(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    for strategy, threshold, diagnostics in (
        ("B", 0.60, {"stop_loss_price": 0.60}),
        ("B2", 0.50, {"config": {"strategy_b2_stop_loss_price": 0.50}}),
    ):
        store.open_trade(
            strategy=strategy,
            topic_id=50_805,
            market_id=805,
            side="UP",
            entry=0.90,
            target=None,
            stake=9.0,
            fee_rate_bps=200,
            note="frozen stop threshold test",
            diagnostics=diagnostics,
        )
    store.update_config(
        {"strategy_b_stop_loss_price": 0.40, "strategy_b2_stop_loss_price": 0.70}
    )
    stop = b_snapshot(
        market_id=805,
        seconds_left=8.0,
        up_ask=0.57,
        up_bid=0.55,
        up_bid_size=1_000.0,
        down_ask=0.45,
        down_bid=0.43,
    )
    store.process_confidence_stop_losses(stop, fee_bps=200)

    assert trade(store, "B", 805)["status"] == "STOP_LOSS_EXIT"
    assert trade(store, "B2", 805)["status"] == "OPEN"


def test_carried_pre_reset_b_stop_remains_outside_new_measurement(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.maybe_enter(b_snapshot(market_id=806), fee_bps=200)
    store.reset_strategy_measurement("B")
    before = store.dashboard(collector_stub())["summaries"]["B"]
    assert before["trades"] == 0
    assert before["carriedOpen"] == 1
    assert before["totalOpen"] == 1

    stop = b_snapshot(
        market_id=806,
        seconds_left=8.0,
        up_ask=0.61,
        up_bid=0.59,
        up_bid_size=1_000.0,
        down_ask=0.41,
        down_bid=0.39,
    )
    store.process_b_stop_loss(stop, fee_bps=200)
    after = store.dashboard(collector_stub())["summaries"]["B"]
    assert trade(store, "B", 806)["status"] == "STOP_LOSS_EXIT"
    assert after["trades"] == 0
    assert after["wins"] == 0
    assert after["losses"] == 0
    assert after["realized_pnl"] == 0
    assert after["carriedOpen"] == 0
    assert after["totalOpen"] == 0


def test_b_config_requires_stop_below_minimum_entry(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    with pytest.raises(ValueError, match="strategy B stop loss"):
        store.update_config(
            {"strategy_b_stop_loss_price": 0.90, "strategy_b_min_price": 0.90}
        )
