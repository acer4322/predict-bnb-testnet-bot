import time

from predict_bot.direct_outcome_guard import (
    DIRECT_OUTCOME_DATA_SOURCE,
    DIRECT_OUTCOME_GUARD_VERSION,
    is_direct_outcome_event,
    signal_has_direct_outcome_provenance,
)
from predict_bot.m_realtime import (
    MSeriesRealtimeEngine,
    direct_rest_prediction_event,
)


class _Store:
    def maybe_enter_m_series(
        self,
        snapshot,
        fee_bps,
        *,
        realtime_context=None,
    ):
        return []


class _Observer:
    def __init__(self):
        self.calls = []

    def reset_market(self, *args, **kwargs):
        self.calls.append(("reset_market", args, kwargs))

    def update_tick(self, *args, **kwargs):
        self.calls.append(("update_tick", args, kwargs))


def _market():
    now_ms = time.time_ns() / 1_000_000
    return {
        "market_id": 101,
        "topic_id": 202,
        "title": "BTC 5m",
        "start_price": 65_000.0,
        "start_ms": now_ms - 1_000,
        "end_ms": now_ms + 299_000,
        "fee_bps": 200,
        "server_clock_offset_ms": 0.0,
    }


def _single_outcome_wss():
    wall_ns = time.time_ns()
    return {
        "source": "prediction",
        "stream": "orderbook",
        "market_id": 101,
        "received_wall_ns": wall_ns,
        "received_monotonic_ns": time.monotonic_ns(),
        "feature_eligible": True,
        "prediction_orientation": "DIRECT_UP_VERIFIED",
        "prediction_data_source": "websocket",
        "prediction_sampling_mode": "event_stream",
        "direct_outcome_books": False,
        "best_bid": 0.94,
        "best_bid_qty": 10.0,
        "best_ask": 0.95,
        "best_ask_qty": 12.0,
        "prediction_book_version_ms": wall_ns // 1_000_000,
        "prediction_book_version_age_ms": 0.0,
        "book_skew_ms": 0.0,
    }


def _unmarked_single_outcome_wss():
    event = _single_outcome_wss()
    event.pop("prediction_data_source")
    event.pop("direct_outcome_books")
    return event


def _dual_rest_event():
    now_ms = int(time.time() * 1_000)
    return direct_rest_prediction_event(
        market_id=101,
        up_book={
            "updateTimestampMs": now_ms,
            "bids": [["0.54", "10"]],
            "asks": [["0.56", "12"]],
        },
        down_book={
            "updateTimestampMs": now_ms,
            "bids": [["0.43", "11"]],
            "asks": [["0.45", "13"]],
        },
        received_wall_ns=time.time_ns(),
        received_monotonic_ns=time.monotonic_ns(),
        current_timestamp_ms=now_ms,
    )


def test_explicit_single_outcome_wss_is_not_a_strategy_book():
    engine = MSeriesRealtimeEngine(
        store=_Store(),
        current_market=_market,
    )
    event = _single_outcome_wss()

    assert engine._prediction_values(
        event,
        time.monotonic_ns(),
    ) is None
    assert engine._prediction_book_copy(
        event,
        101,
        time.monotonic_ns(),
    ) is None


def test_explicit_single_outcome_wss_is_rejected_before_evaluation():
    engine = MSeriesRealtimeEngine(
        store=_Store(),
        current_market=_market,
    )
    engine._evaluate = lambda *args, **kwargs: None

    engine._handle(_single_outcome_wss())

    assert engine.prediction_event is None
    assert engine.rejected_non_direct_prediction_events == 1
    state = engine.state()
    assert state["predictionStrategySourcePolicy"] == (
        "DIRECT_DUAL_TOKEN_REST_ONLY"
    )
    assert state["rejectedNonDirectPredictionEvents"] == 1


def test_missing_provenance_is_also_rejected_fail_closed():
    observer = _Observer()
    engine = MSeriesRealtimeEngine(
        store=_Store(),
        current_market=_market,
        market_observer=observer,
    )
    engine._evaluate = lambda *args, **kwargs: None
    event = _unmarked_single_outcome_wss()

    assert is_direct_outcome_event(event) is False
    assert engine._prediction_values(event, time.monotonic_ns()) is None

    engine._update_market_observer(event)
    assert observer.calls == []

    engine._handle(event)
    assert engine.prediction_event is None
    assert engine.rejected_non_direct_prediction_events == 1
    assert engine.state()["predictionSourceGuardVersion"] == (
        DIRECT_OUTCOME_GUARD_VERSION
    )


def test_independent_dual_rest_book_preserves_real_down_prices():
    engine = MSeriesRealtimeEngine(
        store=_Store(),
        current_market=_market,
    )
    event = _dual_rest_event()
    values = engine._prediction_values(
        event,
        time.monotonic_ns(),
    )

    assert is_direct_outcome_event(event) is True
    assert values is not None
    assert values["up_ask"] == 0.56
    assert values["down_ask"] == 0.45
    assert values["down_ask"] != 1.0 - values["up_bid"]


def test_live_sink_marks_only_direct_dual_book_provenance():
    captured = []
    engine = MSeriesRealtimeEngine(
        store=_Store(),
        current_market=_market,
        live_signal_sink=captured.append,
    )

    engine.prediction_event = _dual_rest_event()
    engine.live_signal_sink({"strategy": "R_MICROPRICE"})
    direct_signal = captured.pop()
    assert direct_signal["prediction_source_guard_version"] == (
        DIRECT_OUTCOME_GUARD_VERSION
    )
    assert direct_signal["signal_prediction_data_source"] == (
        DIRECT_OUTCOME_DATA_SOURCE
    )
    assert direct_signal["signal_prediction_direct_outcome_books"] is True
    assert signal_has_direct_outcome_provenance(direct_signal) is True

    engine.prediction_event = _single_outcome_wss()
    engine.live_signal_sink({"strategy": "R_MICROPRICE"})
    non_direct_signal = captured.pop()
    assert non_direct_signal["signal_prediction_direct_outcome_books"] is False
    assert signal_has_direct_outcome_provenance(non_direct_signal) is False
