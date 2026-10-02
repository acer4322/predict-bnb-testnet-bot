import time
from types import SimpleNamespace

from predict_bot.calibrated_value_confirmation_variants import (
    CALIBRATED_VALUE_CONFIRMATION_VERSION,
    CONFIRM_V2_REVERSE_STRATEGY,
    CONFIRM_V2_STRATEGY,
    IMMEDIATE_CONTROL_STRATEGY,
    CalibratedValueConfirmationTracker,
)
from predict_bot.m_realtime import MSeriesRealtimeEngine
from predict_bot.server import DEFAULT_CONFIG, Store


def _market():
    now_ms = time.time_ns() / 1_000_000
    return {
        "market_id": 8101,
        "topic_id": 8202,
        "title": "BTC Up or Down",
        "start_price": 65_000.0,
        "start_ms": now_ms - 240_000,
        "end_ms": now_ms + 60_000,
        "fee_bps": 200,
        "server_clock_offset_ms": 0.0,
    }


def _direct_event():
    return {
        "source": "prediction",
        "stream": "orderbook",
        "market_id": 8101,
        "prediction_data_source": "dual_token_rest",
        "direct_outcome_books": True,
        "feature_eligible": True,
    }


def _snapshot(
    sequence: int,
    received_ns: int,
    *,
    up_bid: float,
    up_ask: float,
    down_bid: float,
    down_ask: float,
):
    return {
        "timestamp": f"2026-08-05T00:00:0{sequence}+00:00",
        "timestamp_ns": 2_000_000_000 + sequence,
        "topic_id": 8202,
        "market_id": 8101,
        "seconds_left": 60.0 - sequence * 0.2,
        "start_price": 65_000.0,
        "spot_price": 64_999.0,
        "spot_age_ms": 100.0,
        "futures_price": 64_998.0,
        "futures_age_ms": 100.0,
        "up_bid": up_bid,
        "up_ask": up_ask,
        "up_bid_size": 250.0,
        "up_ask_size": 250.0,
        "down_bid": down_bid,
        "down_ask": down_ask,
        "down_bid_size": 250.0,
        "down_ask_size": 250.0,
        "book_age_ms": 100.0,
        "book_skew_ms": 20.0,
        "received_monotonic_ns": received_ns,
        "signal_event_sequence": f"prediction:{sequence}",
    }


def _context(sequence: int, received_ns: int):
    return {
        "trigger_source": "prediction",
        "signal_event_type": "prediction",
        "execution_eligible": True,
        "prediction_data_source": "dual_token_rest",
        "signal_event_sequence": f"prediction:{sequence}",
        "received_monotonic_ns": received_ns,
        "signal_received_monotonic_ns": received_ns,
        "market_data_integrity_ok": True,
    }


def _engine(tmp_path):
    store = Store(tmp_path / "simulation.db")
    disabled = {
        key: False
        for key in DEFAULT_CONFIG
        if key.endswith("_enabled")
    }
    disabled["strategy_r_calibrated_value_enabled"] = True
    store.update_config(disabled)
    engine = MSeriesRealtimeEngine(
        store=store,
        current_market=_market,
    )
    engine.prediction_event = _direct_event()
    tracker = engine.calibrated_value_confirmation_tracker
    assert isinstance(tracker, CalibratedValueConfirmationTracker)
    return store, engine, tracker


def test_opens_immediate_then_fixed_confirm_and_reverse_pair(tmp_path):
    store, _, tracker = _engine(tmp_path)
    base_ns = 80_000_000_000

    first = tracker.process(
        _snapshot(
            1,
            base_ns,
            up_bid=0.33,
            up_ask=0.35,
            down_bid=0.63,
            down_ask=0.65,
        ),
        200,
        _context(1, base_ns),
    )
    assert [item["strategy"] for item in first] == [
        IMMEDIATE_CONTROL_STRATEGY
    ]

    confirmed = tracker.process(
        _snapshot(
            2,
            base_ns + 200_000_000,
            up_bid=0.32,
            up_ask=0.34,
            down_bid=0.64,
            down_ask=0.66,
        ),
        200,
        _context(2, base_ns + 200_000_000),
    )
    assert {item["strategy"] for item in confirmed} == {
        CONFIRM_V2_STRATEGY,
        CONFIRM_V2_REVERSE_STRATEGY,
    }

    rows = store.db.execute(
        """SELECT strategy, side, status, strategy_version
             FROM trades
            WHERE strategy IN (?, ?, ?)
            ORDER BY id""",
        (
            IMMEDIATE_CONTROL_STRATEGY,
            CONFIRM_V2_STRATEGY,
            CONFIRM_V2_REVERSE_STRATEGY,
        ),
    ).fetchall()
    assert len(rows) == 3
    by_strategy = {str(row["strategy"]): row for row in rows}
    assert by_strategy[IMMEDIATE_CONTROL_STRATEGY]["side"] == "DOWN"
    assert by_strategy[CONFIRM_V2_STRATEGY]["side"] == "DOWN"
    assert by_strategy[CONFIRM_V2_REVERSE_STRATEGY]["side"] == "UP"
    assert all(str(row["status"]) == "OPEN" for row in rows)
    assert all(
        str(row["strategy_version"])
        == CALIBRATED_VALUE_CONFIRMATION_VERSION
        for row in rows
    )

    store.settle_market(
        8101,
        "DOWN",
        True,
        topic_id=8202,
        start_price=65_000.0,
        end_price=64_999.0,
    )
    settled = store.db.execute(
        """SELECT strategy, status, pnl
             FROM trades
            WHERE strategy IN (?, ?, ?)
            ORDER BY strategy""",
        (
            IMMEDIATE_CONTROL_STRATEGY,
            CONFIRM_V2_STRATEGY,
            CONFIRM_V2_REVERSE_STRATEGY,
        ),
    ).fetchall()
    settled_by_strategy = {
        str(row["strategy"]): row for row in settled
    }
    assert (
        settled_by_strategy[IMMEDIATE_CONTROL_STRATEGY]["status"]
        == "SETTLED_WIN"
    )
    assert (
        settled_by_strategy[CONFIRM_V2_STRATEGY]["status"]
        == "SETTLED_WIN"
    )
    assert (
        settled_by_strategy[CONFIRM_V2_REVERSE_STRATEGY]["status"]
        == "SETTLED_LOSS"
    )


def test_midpoint_confirmation_is_not_replaced_by_edge_alone(tmp_path):
    store, _, tracker = _engine(tmp_path)
    base_ns = 90_000_000_000
    tracker.process(
        _snapshot(
            1,
            base_ns,
            up_bid=0.33,
            up_ask=0.35,
            down_bid=0.63,
            down_ask=0.65,
        ),
        200,
        _context(1, base_ns),
    )
    opened = tracker.process(
        _snapshot(
            2,
            base_ns + 200_000_000,
            up_bid=0.329,
            up_ask=0.349,
            down_bid=0.631,
            down_ask=0.651,
        ),
        200,
        _context(2, base_ns + 200_000_000),
    )
    assert opened == []
    strategies = {
        str(row["strategy"])
        for row in store.db.execute("SELECT strategy FROM trades").fetchall()
    }
    assert strategies == {IMMEDIATE_CONTROL_STRATEGY}
    assert tracker.last_decision["reason"] == (
        "chosen midpoint has not repriced enough"
    )


def test_read_only_dashboard_store_receives_experiment_state(tmp_path):
    db_path = tmp_path / "simulation.db"
    execution_store = Store(db_path)
    dashboard_store = Store.open_read_only(db_path)
    disabled = {
        key: False
        for key in DEFAULT_CONFIG
        if key.endswith("_enabled")
    }
    disabled["strategy_r_calibrated_value_enabled"] = True
    execution_store.update_config(disabled)
    engine = MSeriesRealtimeEngine(
        store=execution_store,
        current_market=_market,
    )
    engine.prediction_event = _direct_event()
    tracker = engine.calibrated_value_confirmation_tracker
    assert isinstance(tracker, CalibratedValueConfirmationTracker)

    base_ns = 100_000_000_000
    tracker.process(
        _snapshot(
            1,
            base_ns,
            up_bid=0.33,
            up_ask=0.35,
            down_bid=0.63,
            down_ask=0.65,
        ),
        200,
        _context(1, base_ns),
    )
    tracker.process(
        _snapshot(
            2,
            base_ns + 200_000_000,
            up_bid=0.32,
            up_ask=0.34,
            down_bid=0.64,
            down_ask=0.66,
        ),
        200,
        _context(2, base_ns + 200_000_000),
    )

    collector = SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=None,
    )
    dashboard = dashboard_store.dashboard(
        collector,
        include_experiments=False,
    )
    experiment = dashboard["researchForward"][
        "calibratedValueConfirmationExperiment"
    ]
    assert experiment["immediateMarkets"] == 1
    assert experiment["pairedMarkets"] == 1
    assert experiment["completeCohorts"] == 1
    assert experiment["runtime"]["currentConfirmations"] == 2
    assert dashboard["summaries"][IMMEDIATE_CONTROL_STRATEGY][
        "trades"
    ] == 1
    assert dashboard["summaries"][CONFIRM_V2_STRATEGY]["trades"] == 1
    assert dashboard["summaries"][CONFIRM_V2_REVERSE_STRATEGY][
        "trades"
    ] == 1
