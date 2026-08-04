import json
import time
from types import SimpleNamespace

from predict_bot.calibrated_value_confirmation_filters import (
    FILTER_VERSION,
    LOWTAIL_STRATEGY,
    RANGE12_STRATEGY,
)
from predict_bot.calibrated_value_confirmation_variants import (
    CONFIRM_V2_REVERSE_STRATEGY,
    CONFIRM_V2_STRATEGY,
    IMMEDIATE_CONTROL_STRATEGY,
    CalibratedValueConfirmationTracker,
)
from predict_bot.m_realtime import MSeriesRealtimeEngine
from predict_bot.server import DEFAULT_CONFIG, Store


def _market(market_id=9101):
    now_ms = time.time_ns() / 1_000_000
    return {
        "market_id": market_id,
        "topic_id": 9202,
        "title": "BTC Up or Down",
        "start_price": 65_000.0,
        "start_ms": now_ms - 240_000,
        "end_ms": now_ms + 60_000,
        "fee_bps": 200,
        "server_clock_offset_ms": 0.0,
    }


def _event(market_id=9101):
    return {
        "source": "prediction",
        "stream": "orderbook",
        "market_id": market_id,
        "prediction_data_source": "dual_token_rest",
        "direct_outcome_books": True,
        "feature_eligible": True,
    }


def _snapshot(
    sequence,
    received_ns,
    *,
    market_id=9101,
    up_bid,
    up_ask,
    down_bid,
    down_ask,
    book_age_ms=100.0,
    book_skew_ms=20.0,
):
    return {
        "timestamp": f"2026-08-05T00:00:0{sequence}+00:00",
        "timestamp_ns": 3_000_000_000 + sequence,
        "topic_id": 9202,
        "market_id": market_id,
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
        "book_age_ms": book_age_ms,
        "book_skew_ms": book_skew_ms,
        "received_monotonic_ns": received_ns,
        "signal_event_sequence": f"prediction:{market_id}:{sequence}",
    }


def _context(
    sequence,
    received_ns,
    *,
    market_id=9101,
    range_score=1,
    crossovers=2,
    trend_veto=False,
):
    return {
        "trigger_source": "prediction",
        "signal_event_type": "prediction",
        "execution_eligible": True,
        "prediction_data_source": "dual_token_rest",
        "signal_event_sequence": f"prediction:{market_id}:{sequence}",
        "received_monotonic_ns": received_ns,
        "signal_received_monotonic_ns": received_ns,
        "market_data_integrity_ok": True,
        "m01o_observer_gate": {
            "currentMarketId": market_id,
            "currentRangeScore": range_score,
            "currentEffectiveCrossovers": crossovers,
            "currentTrendVeto": trend_veto,
            "currentMedianEr60s": 0.40,
            "currentBothSidesTouched": False,
            "currentPhase": "MATURE_60S_PLUS",
            "historicalState": "RANGE",
            "historicalSampleCount": 30,
        },
    }


def _engine(tmp_path, market_id=9101):
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
        current_market=lambda: _market(market_id),
    )
    engine.prediction_event = _event(market_id)
    tracker = engine.calibrated_value_confirmation_tracker
    assert isinstance(tracker, CalibratedValueConfirmationTracker)
    return store, engine, tracker


def test_range12_opens_on_confirmed_range_score_one_or_two(tmp_path):
    store, _, tracker = _engine(tmp_path)
    base_ns = 110_000_000_000
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
        _context(1, base_ns, range_score=1, crossovers=2),
    )
    opened = tracker.process(
        _snapshot(
            2,
            base_ns + 200_000_000,
            up_bid=0.32,
            up_ask=0.34,
            down_bid=0.64,
            down_ask=0.66,
        ),
        200,
        _context(2, base_ns + 200_000_000, range_score=2, crossovers=2),
    )
    assert {item["strategy"] for item in opened} == {
        CONFIRM_V2_STRATEGY,
        CONFIRM_V2_REVERSE_STRATEGY,
        RANGE12_STRATEGY,
    }
    strategies = {
        str(row["strategy"])
        for row in store.db.execute("SELECT strategy FROM trades").fetchall()
    }
    assert strategies == {
        IMMEDIATE_CONTROL_STRATEGY,
        CONFIRM_V2_STRATEGY,
        CONFIRM_V2_REVERSE_STRATEGY,
        RANGE12_STRATEGY,
    }


def test_range12_fails_closed_when_score_or_crossovers_are_outside_rule(tmp_path):
    store, _, tracker = _engine(tmp_path)
    base_ns = 120_000_000_000
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
        _context(1, base_ns, range_score=3, crossovers=3),
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
        _context(2, base_ns + 200_000_000, range_score=3, crossovers=3),
    )
    assert store.db.execute(
        "SELECT 1 FROM trades WHERE strategy=?",
        (RANGE12_STRATEGY,),
    ).fetchone() is None


def test_lowtail_waits_for_third_strict_event_after_base_v2_opens(tmp_path):
    store, _, tracker = _engine(tmp_path)
    base_ns = 130_000_000_000
    tracker.process(
        _snapshot(
            1,
            base_ns,
            up_bid=0.70,
            up_ask=0.72,
            down_bid=0.18,
            down_ask=0.20,
        ),
        200,
        _context(1, base_ns, range_score=0, crossovers=0, trend_veto=False),
    )
    second = tracker.process(
        _snapshot(
            2,
            base_ns + 150_000_000,
            up_bid=0.694,
            up_ask=0.714,
            down_bid=0.186,
            down_ask=0.206,
        ),
        200,
        _context(
            2,
            base_ns + 150_000_000,
            range_score=0,
            crossovers=0,
            trend_veto=False,
        ),
    )
    assert CONFIRM_V2_STRATEGY in {item["strategy"] for item in second}
    assert LOWTAIL_STRATEGY not in {item["strategy"] for item in second}

    third = tracker.process(
        _snapshot(
            3,
            base_ns + 300_000_000,
            up_bid=0.69,
            up_ask=0.71,
            down_bid=0.19,
            down_ask=0.21,
        ),
        200,
        _context(
            3,
            base_ns + 300_000_000,
            range_score=0,
            crossovers=0,
            trend_veto=False,
        ),
    )
    assert [item["strategy"] for item in third] == [LOWTAIL_STRATEGY]
    row = store.db.execute(
        "SELECT strategy_version, entry_price, diagnostics_json FROM trades WHERE strategy=?",
        (LOWTAIL_STRATEGY,),
    ).fetchone()
    assert row is not None
    assert row["strategy_version"] == FILTER_VERSION
    assert 0.10 <= float(row["entry_price"]) <= 0.221
    diagnostics = json.loads(row["diagnostics_json"])
    assert diagnostics["confirmation_count"] == 3
    assert diagnostics["observer_features"]["currentTrendVeto"] is False
    assert diagnostics["retained_edge_ratio"] >= 0.75


def test_filter_strategies_are_exposed_through_read_only_dashboard_store(tmp_path):
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
        current_market=lambda: _market(),
    )
    engine.prediction_event = _event()
    tracker = engine.calibrated_value_confirmation_tracker
    base_ns = 140_000_000_000
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
        _context(1, base_ns, range_score=1, crossovers=1),
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
        _context(2, base_ns + 200_000_000, range_score=1, crossovers=1),
    )
    collector = SimpleNamespace(
        status="LIVE",
        error=None,
        updated_at=None,
        interval=1.0,
        prediction=None,
    )
    dashboard = dashboard_store.dashboard(collector, include_experiments=False)
    experiment = dashboard["researchForward"][
        "calibratedValueConfirmationExperiment"
    ]
    assert experiment["filterExtensionVersion"] == FILTER_VERSION
    assert experiment["range12Markets"] == 1
    assert RANGE12_STRATEGY in experiment["strategies"]
    assert LOWTAIL_STRATEGY in experiment["strategies"]
    assert dashboard["summaries"][RANGE12_STRATEGY]["trades"] == 1
    assert dashboard["summaries"][LOWTAIL_STRATEGY]["trades"] == 0
