import time

import pytest

from predict_bot.m_realtime import MSeriesRealtimeEngine
from predict_bot.microstructure import MicrostructureObserver


class FakeStore:
    def __init__(self):
        self.calls = []

    def maybe_enter_m_series(self, snapshot, fee_bps, *, realtime_context=None):
        self.calls.append((snapshot, fee_bps, realtime_context))


class ConfigFakeStore(FakeStore):
    def config(self):
        config = {
            "strategy_m_enabled": False,
            "strategy_m_entry_window_seconds": 10.0,
            "strategy_m01_enabled": False,
            "strategy_m01_entry_window_seconds": 300.0,
            "strategy_m01o_enabled": False,
            "strategy_m01o_f1_enabled": False,
            "strategy_m01o_live_enabled": False,
            "strategy_m01o_entry_window_seconds": 300.0,
            "strategy_m01o_min_observer_samples": 6.0,
            "strategy_m01o_min_current_range_score": 2.0,
            "strategy_m01f_enabled": False,
            "strategy_m01f_entry_window_seconds": 300.0,
            "strategy_m01r_enabled": False,
            "strategy_m01r_entry_window_seconds": 300.0,
            "strategy_m0w_enabled": False,
            "strategy_m0w_entry_window_seconds": 10.0,
            "strategy_m01w_enabled": False,
            "strategy_m01w_entry_window_seconds": 300.0,
            "strategy_m7_entry_window_seconds": 10.0,
            "strategy_m7_execution_grace_seconds": 2.0,
        }
        for suffix in range(7):
            config[f"strategy_m{suffix}_enabled"] = False
            config[f"strategy_m{suffix}_entry_window_seconds"] = 10.0
        for delay in (1, 2, 3, 5):
            config[f"strategy_m7_{delay}_enabled"] = False
        return config


class MxAwareStore(ConfigFakeStore):
    def __init__(self):
        super().__init__()
        self.mx_calls = []
        self.pair_calls = []

    def process_mx_event(self, snapshot, fee_bps, *, realtime_context=None):
        self.mx_calls.append((snapshot, fee_bps, realtime_context))

    def process_pair_arb_snapshot(
        self, snapshot, fee_bps, *, realtime_context=None
    ):
        self.pair_calls.append((snapshot, fee_bps, realtime_context))


def market_reference(**overrides):
    now_ms = time.time_ns() / 1_000_000
    value = {
        "market_id": 101,
        "topic_id": 202,
        "title": "BTC 5m",
        "start_price": 65_000.0,
        "start_ms": now_ms - 1_000,
        "end_ms": now_ms + 299_000,
        "fee_bps": 200,
        "server_clock_offset_ms": 0.0,
    }
    value.update(overrides)
    return value


def event(source, stream, **overrides):
    wall_ns = time.time_ns()
    mono_ns = time.monotonic_ns()
    value = {
        "source": source,
        "stream": stream,
        "received_wall_ns": wall_ns,
        "received_monotonic_ns": mono_ns,
        "session_id": "session-a",
        "exchange_event_ms": wall_ns // 1_000_000,
        "exchange_trade_ms": wall_ns // 1_000_000,
        "update_id": wall_ns // 1_000_000,
    }
    value.update(overrides)
    return value


def prediction_event(**overrides):
    value = event(
        "prediction",
        "orderbook",
        market_id=101,
        feature_eligible=True,
        prediction_orientation="DIRECT_UP_VERIFIED",
        best_bid=0.54,
        best_bid_qty=12.0,
        best_ask=0.56,
        best_ask_qty=8.0,
        prediction_book_version_age_ms=0.0,
        book_skew_ms=0.0,
    )
    value.update(overrides)
    value.setdefault("prediction_book_version_ms", value.get("update_id"))
    return value


def test_realtime_engine_freezes_signal_before_next_prediction_book():
    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)

    engine._handle(prediction_event())
    assert store.calls[-1][2]["execution_eligible"] is True
    assert store.calls[-1][2]["prediction_book_version_ms"] is not None

    store.calls.clear()
    engine._handle(event("spot", "trade", price=65_010.0, trade_id=1))
    signal_snapshot, _, signal_context = store.calls[-1]
    assert signal_snapshot["spot_price"] == pytest.approx(65_010.0)
    assert signal_context["execution_eligible"] is False
    assert signal_context["trigger_source"] == "spot"

    engine._handle(prediction_event(best_bid=0.57, best_ask=0.58, update_id=2))
    fill_snapshot, fee_bps, fill_context = store.calls[-1]
    assert fee_bps == 200
    assert fill_context["execution_eligible"] is True
    assert fill_context["trigger_source"] == "prediction"
    assert fill_snapshot["spot_price"] == pytest.approx(65_010.0)
    assert fill_snapshot["up_ask"] == pytest.approx(0.58)
    assert fill_snapshot["down_ask"] == pytest.approx(0.43)
    assert fill_snapshot["down_ask_size"] == pytest.approx(12.0)
    assert fill_snapshot["book_skew_ms"] == 0
    assert fill_snapshot["spot_age_ms"] is not None
    assert fill_snapshot["spot_age_ms"] >= 0
    assert fill_context["spot_age_ms"] == fill_snapshot["spot_age_ms"]


def test_realtime_engine_forwards_all_opened_m_candidates_to_live_filter():
    class CandidateStore(FakeStore):
        def maybe_enter_m_series(
            self, snapshot, fee_bps, *, realtime_context=None
        ):
            super().maybe_enter_m_series(
                snapshot, fee_bps, realtime_context=realtime_context
            )
            return [
                {"strategy": "M0W", "market_id": 101, "side": "UP"},
                {"strategy": "M01T180", "market_id": 101, "side": "UP"},
                {"strategy": "M1", "market_id": 101, "side": "UP"},
            ]

    forwarded = []
    engine = MSeriesRealtimeEngine(
        store=CandidateStore(),
        current_market=market_reference,
        live_signal_sink=forwarded.append,
    )

    engine._handle(prediction_event())

    assert [candidate["strategy"] for candidate in forwarded] == [
        "M0W",
        "M01T180",
        "M1",
    ]
    for candidate in forwarded:
        assert candidate["market_event_received_monotonic_ns"] > 0
        assert candidate["strategy_decision_started_monotonic_ns"] >= (
            candidate["market_event_received_monotonic_ns"]
        )
        assert candidate["strategy_store_started_monotonic_ns"] >= (
            candidate["strategy_decision_started_monotonic_ns"]
        )
        assert candidate["strategy_store_finished_monotonic_ns"] >= (
            candidate["strategy_store_started_monotonic_ns"]
        )
        assert candidate["live_candidate_created_monotonic_ns"] >= (
            candidate["strategy_store_finished_monotonic_ns"]
        )
        assert candidate["signal_prediction_book_age_ms"] >= 0
        assert candidate["signal_prediction_received_monotonic_ns"] > 0
        assert candidate["signal_prediction_ask"] == pytest.approx(0.56)
        assert candidate["signal_prediction_ask_size"] == pytest.approx(8.0)
        assert candidate["signal_prediction_bid"] == pytest.approx(0.54)
        assert (
            candidate["signal_prediction_orientation"]
            == "DIRECT_UP_VERIFIED"
        )
        assert candidate["signal_market_data_integrity_ok"] is True
        assert candidate["signal_event_sequence"].startswith(
            "prediction:orderbook:market:101:update:"
        )


def test_current_verified_prediction_book_uses_monotonic_receipt_age():
    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    received_ns = time.monotonic_ns() - 25_000_000
    engine._handle(
        prediction_event(
            received_monotonic_ns=received_ns,
            bids=[[0.54, 12.0], [0.53, 20.0]],
            asks=[[0.56, 8.0], [0.57, 20.0]],
            updateTimestampMs=0,
        )
    )

    book = engine.current_verified_prediction_book()

    assert book["market_id"] == 101
    assert book["orientation"] == "DIRECT_UP_VERIFIED"
    assert book["received_monotonic_ns"] == received_ns
    assert book["book_age_ms"] >= 25.0
    assert book["up_asks"] == [[0.56, 8.0], [0.57, 20.0]]
    assert book["down_asks"] == [[0.45999999999999996, 12.0], [0.47, 20.0]]


def test_direct_rest_prediction_book_preserves_both_outcome_books():
    from predict_bot.m_realtime import direct_rest_prediction_event

    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    received_wall_ns = time.time_ns()
    received_monotonic_ns = time.monotonic_ns()
    now_ms = received_wall_ns // 1_000_000
    engine._handle(
        direct_rest_prediction_event(
            market_id=101,
            up_book={
                "bids": [["0.06", "20"]],
                "asks": [["0.07", "30"]],
                "updateTimestampMs": now_ms - 100,
            },
            down_book={
                "bids": [["0.93", "40"]],
                "asks": [["0.94", "50"]],
                "updateTimestampMs": now_ms - 120,
            },
            received_wall_ns=received_wall_ns,
            received_monotonic_ns=received_monotonic_ns,
            current_timestamp_ms=now_ms,
        )
    )

    book = engine.current_verified_prediction_book()
    assert book["data_source"] == "dual_token_rest"
    assert book["up_ask"] == pytest.approx(0.07)
    assert book["down_ask"] == pytest.approx(0.94)
    assert book["down_asks"] == [[0.94, 50.0]]
    assert book["book_age_ms"] >= 120.0
    assert store.calls[-1][0]["down_ask"] == pytest.approx(0.94)
    assert store.calls[-1][2]["prediction_sampling_mode"] == "periodic_snapshot"


def test_direct_rest_prediction_event_rejects_stale_content_before_strategy():
    from predict_bot.m_realtime import direct_rest_prediction_event

    now_ms = int(time.time() * 1000)
    built = direct_rest_prediction_event(
        market_id=101,
        up_book={
            "bids": [["0.40", "20"]],
            "asks": [["0.41", "30"]],
            "updateTimestampMs": now_ms - 2_001,
        },
        down_book={
            "bids": [["0.58", "40"]],
            "asks": [["0.59", "50"]],
            "updateTimestampMs": now_ms - 2_001,
        },
        received_wall_ns=time.time_ns(),
        received_monotonic_ns=time.monotonic_ns(),
        current_timestamp_ms=now_ms,
    )

    assert built["book_age_ms"] == pytest.approx(2_001.0)
    assert built["feature_eligible"] is False


def test_prediction_age_telemetry_separates_strategy_rest_and_exchange_age():
    from predict_bot.m_realtime import direct_rest_prediction_event

    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    now_ms = int(time.time() * 1000)

    def rest_event(*, content_age_ms: int):
        return direct_rest_prediction_event(
            market_id=101,
            up_book={
                "bids": [["0.40", "20"]],
                "asks": [["0.41", "30"]],
                "updateTimestampMs": now_ms - content_age_ms,
            },
            down_book={
                "bids": [["0.58", "40"]],
                "asks": [["0.59", "50"]],
                "updateTimestampMs": now_ms - content_age_ms,
            },
            received_wall_ns=time.time_ns(),
            received_monotonic_ns=time.monotonic_ns(),
            current_timestamp_ms=now_ms,
        )

    engine._handle(rest_event(content_age_ms=100))
    engine._handle(rest_event(content_age_ms=3_000))
    # A later legacy WSS diagnostic frame must not blank the REST telemetry.
    engine._handle(prediction_event(feature_eligible=False))

    state = engine.state()
    assert state["acceptedPredictionEvents"] == 1
    assert state["rejectedUnverifiedPredictionEvents"] == 2
    assert state["predictionBookAgeMs"] == pytest.approx(
        state["predictionStrategyBookAgeMs"]
    )
    assert 100 <= state["predictionStrategyBookAgeMs"] < 1_000
    assert 0 <= state["predictionRestReceiptAgeMs"] < 1_000
    assert state["predictionExchangeContentAgeMs"] >= 3_000
    assert state["predictionLatestRestEligible"] is False
    strategy_book = engine.current_verified_prediction_book()
    direct_rest_book = engine.current_direct_rest_prediction_book()
    assert strategy_book is not None
    assert direct_rest_book is not None
    assert strategy_book["book_age_ms"] < 1_000
    assert direct_rest_book["book_age_ms"] >= 3_000
    assert direct_rest_book["data_source"] == "dual_token_rest"


def test_realtime_engine_only_forwards_live_supported_paper_candidates():
    class CandidateStore(FakeStore):
        def maybe_enter_m_series(
            self, snapshot, fee_bps, *, realtime_context=None
        ):
            super().maybe_enter_m_series(
                snapshot, fee_bps, realtime_context=realtime_context
            )
            return [
                {"strategy": "M01O", "market_id": 101, "paper_only": True},
                {
                    "strategy": "M01O_F1",
                    "market_id": 101,
                    "paper_only": True,
                    "market_observer_gate": {
                        "profile": "F1",
                        "allowed": True,
                    },
                },
                {"strategy": "M01O_LIVE", "market_id": 101, "paper_only": True},
                {
                    "strategy": "R_FUTURES_LEAD",
                    "market_id": 101,
                    "paper_only": True,
                },
                {
                    "strategy": "R_CALIBRATED_VALUE",
                    "market_id": 101,
                    "paper_only": True,
                },
                {
                    "strategy": "R_OFI_EVENT_CUM",
                    "market_id": 101,
                    "paper_only": True,
                },
                {
                    "strategy": "R_OFI",
                    "market_id": 101,
                    "paper_only": True,
                },
                {"strategy": "M0", "market_id": 101},
            ]

    forwarded = []
    engine = MSeriesRealtimeEngine(
        store=CandidateStore(),
        current_market=market_reference,
        live_signal_sink=forwarded.append,
    )

    engine._handle(prediction_event())

    assert [candidate["strategy"] for candidate in forwarded] == [
        "M01O_F1",
        "R_FUTURES_LEAD",
        "R_CALIBRATED_VALUE",
        "R_OFI_EVENT_CUM",
        "R_OFI",
        "M0",
    ]
    assert forwarded[0]["paper_only"] is False
    assert forwarded[0]["observer_gate_required"] is True
    assert forwarded[0]["market_observer_gate"]["paperOnly"] is False
    assert forwarded[0]["market_observer_gate"]["liveOrdersAffected"] is True
    for candidate in forwarded[1:5]:
        assert candidate["paper_only"] is False
        assert candidate["live_orders_affected"] is True
        assert candidate["live_forwarded_from_paper"] is True
        assert "observer_gate_required" not in candidate
    assert forwarded[1]["futures_lead_observer_gate_required"] is True
    assert "futures_lead_observer_gate" in forwarded[1]
    assert forwarded[2]["strategy_observer_gate_required"] is True
    assert "strategy_observer_gate" in forwarded[2]
    assert "futures_lead_observer_gate_required" not in forwarded[2]
    assert "futures_lead_observer_gate_required" not in forwarded[3]
    assert forwarded[4]["strategy_observer_gate_required"] is True
    assert "strategy_observer_gate" in forwarded[4]
    assert "futures_lead_observer_gate_required" not in forwarded[4]
    for candidate in forwarded:
        assert candidate["drawdown_control_start_price"] == pytest.approx(
            65_000.0
        )
        assert "drawdown_control_spot_price" in candidate
        assert "drawdown_control_spot_age_ms" in candidate


def test_realtime_engine_rejects_unverified_prediction_book():
    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    engine._handle(prediction_event(feature_eligible=False))
    engine._handle(event("spot", "trade", price=65_001.0))
    assert len(store.calls) == 1
    signal, _, signal_context = store.calls[0]
    assert signal_context["execution_eligible"] is False
    assert signal["spot_price"] == pytest.approx(65_001.0)
    assert signal["up_ask"] is None
    state = engine.state()
    assert state["rejectedUnverifiedPredictionEvents"] == 1
    assert state["acceptedPredictionEvents"] == 0
    assert state["predictionBookAgeMs"] is None

    engine._handle(prediction_event(update_id=99))
    assert len(store.calls) == 2
    assert store.calls[-1][2]["execution_eligible"] is True
    state = engine.state()
    assert state["acceptedPredictionEvents"] == 1
    assert state["predictionBookAgeMs"] is not None
    assert state["lastAcceptedPredictionAt"] is not None


def test_orientation_confirmation_reaches_store_once_for_same_book_version(
    tmp_path,
):
    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    received_wall_ns = time.time_ns()
    reference = {
        "market_id": 101,
        "up_bid": 0.54,
        "up_ask": 0.56,
        "received_wall_ns": received_wall_ns,
    }
    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=lambda: 101,
        prediction_reference=lambda: reference,
        realtime_event_sink=engine._handle,
        db_path=tmp_path / "orientation-integration.db",
    )
    version_ms = received_wall_ns // 1_000_000

    def same_version_event(receipt_offset_ns: int):
        return prediction_event(
            update_id=version_ms,
            exchange_event_ms=version_ms,
            prediction_book_version_ms=version_ms,
            received_wall_ns=received_wall_ns + receipt_offset_ns,
            received_monotonic_ns=time.monotonic_ns() + receipt_offset_ns,
        )

    observer._accept_event("prediction", same_version_event(0))
    observer._accept_event("prediction", same_version_event(1))
    observer._accept_event("prediction", same_version_event(2))

    assert observer.prediction_orientation == "DIRECT_UP_VERIFIED"
    assert observer.eligible_prediction_events == 1
    assert observer.unverified_prediction_events == 1
    assert observer.out_of_order == 1
    assert engine.rejected_unverified_prediction_events == 1
    assert engine.accepted_prediction_events == 1
    assert len(store.calls) == 1
    assert store.calls[0][2]["execution_eligible"] is True


def test_futures_signal_is_forwarded_before_prediction_book_exists():
    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    engine._handle(event("futures", "aggTrade", price=64_999.0, trade_id=77))
    snapshot, _, context = store.calls[-1]
    assert snapshot["futures_price"] == pytest.approx(64_999.0)
    assert snapshot["up_ask"] is None
    assert context["signal_event_type"] == "futures"
    assert context["execution_eligible"] is False


def test_non_prediction_evaluation_cannot_forward_a_live_order():
    class CandidateStore(FakeStore):
        def maybe_enter_m_series(
            self, snapshot, fee_bps, *, realtime_context=None
        ):
            super().maybe_enter_m_series(
                snapshot, fee_bps, realtime_context=realtime_context
            )
            return [{"strategy": "M0W", "market_id": 101}]

    forwarded = []
    engine = MSeriesRealtimeEngine(
        store=CandidateStore(),
        current_market=market_reference,
        live_signal_sink=forwarded.append,
    )
    engine._handle(event("spot", "trade", price=65_001.0, trade_id=1))
    assert forwarded == []


def test_realtime_submit_only_queues_signal_and_execution_streams():
    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    engine.submit(event("spot", "bookTicker"))
    engine.submit(event("spot", "trade", price=65_001.0))
    engine.submit(event("futures", "aggTrade", price=65_002.0))
    engine.submit(prediction_event())
    assert engine.events.qsize() == 4


def test_spot_bookticker_midpoint_is_explicit_fallback_when_trade_is_stale():
    engine = MSeriesRealtimeEngine(store=FakeStore(), current_market=market_reference)
    now_ns = time.monotonic_ns()
    engine.spot_event = event(
        "spot", "trade", price=65_000.0,
        received_monotonic_ns=now_ns - 5_000_000_000,
    )
    engine.spot_book_event = event(
        "spot", "bookTicker", best_bid=64_999.0, best_ask=65_001.0,
        received_monotonic_ns=now_ns - 100_000_000,
    )

    selected, source = engine._spot_signal_event(now_ns)

    assert source == "bookTicker_midpoint"
    assert engine._event_price(selected) == pytest.approx(65_000.0)


def test_spot_trade_age_tracks_ingress_and_processed_separately():
    engine = MSeriesRealtimeEngine(store=FakeStore(), current_market=market_reference)
    now_ns = time.monotonic_ns()
    engine.spot_trade_ingress_received_ns = now_ns - 100_000_000
    engine.spot_trade_processed_received_ns = now_ns - 800_000_000

    state = engine.state()

    assert state["spotTradeIngressAgeMs"] == pytest.approx(100.0, abs=30.0)
    assert state["spotTradeProcessedAgeMs"] == pytest.approx(800.0, abs=30.0)


def test_current_spot_reference_exposes_trade_and_book_microprice():
    engine = MSeriesRealtimeEngine(store=FakeStore(), current_market=market_reference)
    now_ns = time.monotonic_ns()
    engine.spot_event = event(
        "spot", "trade", price=65_010.0,
        received_monotonic_ns=now_ns - 2_383_000_000,
    )
    engine.spot_book_event = event(
        "spot", "bookTicker",
        best_bid=64_999.0,
        best_ask=65_001.0,
        best_bid_qty=3.0,
        best_ask_qty=1.0,
        received_monotonic_ns=now_ns - 30_000_000,
    )

    reference = engine.current_spot_reference()
    state = engine.state()

    assert reference["trade_price"] == pytest.approx(65_010.0)
    assert reference["trade_age_ms"] == pytest.approx(2_383.0, abs=30.0)
    assert reference["book_midpoint"] == pytest.approx(65_000.0)
    assert reference["book_microprice"] == pytest.approx(65_000.5)
    assert reference["book_age_ms"] == pytest.approx(30.0, abs=30.0)
    assert state["spotTradeAgeMs"] == pytest.approx(2_383.0, abs=30.0)
    assert state["spotBookAgeMs"] == pytest.approx(30.0, abs=30.0)


def test_spot_trade_id_keeps_same_millisecond_events_distinct_for_m4():
    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    engine._handle(prediction_event())
    shared_trade_ms = int(time.time() * 1_000)
    engine._handle(
        event(
            "spot", "trade", price=65_001.0, trade_id=101,
            exchange_trade_ms=shared_trade_ms,
        )
    )
    first_key = store.calls[-1][2]["signal_event_sequence"]
    engine._handle(
        event(
            "spot", "trade", price=65_001.5, trade_id=102,
            exchange_trade_ms=shared_trade_ms,
        )
    )
    second_key = store.calls[-1][2]["signal_event_sequence"]
    assert first_key != second_key
    assert first_key.endswith(":101")
    assert second_key.endswith(":102")


def test_replayed_or_out_of_order_trade_id_is_rejected_across_sessions():
    store = FakeStore()
    engine = MSeriesRealtimeEngine(store=store, current_market=market_reference)
    engine._handle(event("spot", "trade", price=65_001.0, trade_id=200))
    calls_after_first = len(store.calls)
    engine._handle(
        event(
            "spot", "trade", price=64_000.0, trade_id=200,
            session_id="reconnected-session",
        )
    )
    engine._handle(
        event(
            "spot", "trade", price=63_000.0, trade_id=199,
            session_id="reconnected-session",
        )
    )
    assert len(store.calls) == calls_after_first
    assert engine.spot_event["price"] == pytest.approx(65_001.0)
    assert engine.state()["rejectedTradeReplays"] == 2


def test_cached_prediction_received_after_spot_is_replayed_causally():
    store = FakeStore()
    base_wall = time.time_ns()
    base_mono = time.monotonic_ns()
    reference = market_reference(
        start_ms=base_wall / 1_000_000 - 100,
        end_ms=base_wall / 1_000_000 + 299_900,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    later_book = prediction_event(
        received_wall_ns=base_wall + 20_000_000,
        received_monotonic_ns=base_mono + 20_000_000,
        update_id=400,
    )
    older_spot = event(
        "spot", "trade", price=65_010.0, trade_id=300,
        received_wall_ns=base_wall + 10_000_000,
        received_monotonic_ns=base_mono + 10_000_000,
    )
    # Simulate cross-socket enqueue order: the newer book is processed first.
    engine._handle(later_book)
    store.calls.clear()
    engine._handle(older_spot)
    assert [call[2]["trigger_source"] for call in store.calls] == [
        "spot", "prediction"
    ]
    assert store.calls[-1][2]["execution_eligible"] is True
    assert store.calls[-1][0]["spot_price"] == pytest.approx(65_010.0)


def test_m7_deadlines_use_last_fresh_spot_at_each_absolute_market_time():
    store = FakeStore()
    base_wall = time.time_ns()
    base_mono = time.monotonic_ns()
    reference = market_reference(
        start_ms=base_wall / 1_000_000,
        end_ms=base_wall / 1_000_000 + 300_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    samples = (
        (0.8, 65_010.0, 501),
        (1.8, 64_990.0, 502),
        (2.8, 65_020.0, 503),
        (4.8, 64_980.0, 504),
    )
    for elapsed, price, trade_id in samples:
        engine._handle(
            event(
                "spot", "trade", price=price, trade_id=trade_id,
                received_wall_ns=base_wall + int(elapsed * 1_000_000_000),
                received_monotonic_ns=base_mono + int(elapsed * 1_000_000_000),
            )
        )
    store.calls.clear()
    engine._emit_due_m7_deadlines(base_mono + 5_100_000_000)
    scheduler_calls = [
        call for call in store.calls if call[2]["trigger_source"] == "scheduler"
    ]
    assert [call[2]["m7_deadline_seconds"] for call in scheduler_calls] == [
        1.0, 2.0, 3.0, 5.0
    ]
    assert [call[0]["spot_price"] for call in scheduler_calls] == pytest.approx(
        [65_010.0, 64_990.0, 65_020.0, 64_980.0]
    )
    assert all(call[2]["m7_asof_spot_age_ms"] == pytest.approx(200.0) for call in scheduler_calls)
    assert all(call[0]["market_elapsed_seconds"] in {1.0, 2.0, 3.0, 5.0} for call in scheduler_calls)


def test_m7_reorder_grace_waits_for_spot_received_just_before_deadline():
    store = FakeStore()
    base_wall = time.time_ns()
    base_mono = time.monotonic_ns()
    reference = market_reference(
        start_ms=base_wall / 1_000_000,
        end_ms=base_wall / 1_000_000 + 300_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(
        event(
            "spot",
            "trade",
            price=65_001.0,
            trade_id=510,
            received_wall_ns=base_wall + 800_000_000,
            received_monotonic_ns=base_mono + 800_000_000,
        )
    )
    store.calls.clear()

    # The scheduler is already past +1s, but remains inside the bounded local
    # reorder window, so it must not permanently freeze the older sample.
    engine._emit_due_m7_deadlines(base_mono + 1_005_000_000)
    assert not [
        call for call in store.calls if call[2]["trigger_source"] == "scheduler"
    ]

    # Simulate a callback timestamped before the deadline but processed a few
    # milliseconds later because another socket won the enqueue race.
    engine._handle(
        event(
            "spot",
            "trade",
            price=65_009.0,
            trade_id=511,
            received_wall_ns=base_wall + 999_000_000,
            received_monotonic_ns=base_mono + 999_000_000,
        )
    )
    engine._emit_due_m7_deadlines(base_mono + 1_011_000_000)
    scheduler_calls = [
        call for call in store.calls if call[2]["trigger_source"] == "scheduler"
    ]
    assert len(scheduler_calls) == 1
    assert scheduler_calls[0][2]["m7_deadline_seconds"] == pytest.approx(1.0)
    assert scheduler_calls[0][0]["spot_price"] == pytest.approx(65_009.0)
    assert scheduler_calls[0][2]["m7_asof_spot_age_ms"] == pytest.approx(1.0)


def test_m7_replays_first_post_deadline_book_not_latest_book():
    store = FakeStore()
    base_wall = time.time_ns()
    base_mono = time.monotonic_ns()
    reference = market_reference(
        start_ms=base_wall / 1_000_000,
        end_ms=base_wall / 1_000_000 + 300_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(
        event(
            "spot",
            "trade",
            price=65_010.0,
            trade_id=520,
            received_wall_ns=base_wall + 999_000_000,
            received_monotonic_ns=base_mono + 999_000_000,
        )
    )
    engine._handle(
        prediction_event(
            update_id=521,
            best_ask=0.55,
            received_wall_ns=base_wall + 1_001_000_000,
            received_monotonic_ns=base_mono + 1_001_000_000,
        )
    )
    engine._handle(
        prediction_event(
            update_id=522,
            best_ask=0.60,
            received_wall_ns=base_wall + 1_005_000_000,
            received_monotonic_ns=base_mono + 1_005_000_000,
        )
    )
    store.calls.clear()
    engine._emit_due_m7_deadlines(base_mono + 1_011_000_000)
    prediction_calls = [
        call for call in store.calls if call[2]["trigger_source"] == "prediction"
    ]
    assert len(prediction_calls) == 1
    assert prediction_calls[0][0]["up_ask"] == pytest.approx(0.55)
    assert prediction_calls[0][2]["signal_event_sequence"].endswith(":521")


def test_m7_rollover_reuses_spot_history_received_before_market_switch():
    """Late market discovery must not discard already timestamped Spot ticks."""
    store = FakeStore()
    base_wall = time.time_ns()
    base_mono = time.monotonic_ns()
    old_market = market_reference(
        market_id=101,
        topic_id=201,
        start_ms=base_wall / 1_000_000 - 300_000,
        end_ms=base_wall / 1_000_000,
    )
    new_market = market_reference(
        market_id=102,
        topic_id=202,
        start_ms=base_wall / 1_000_000,
        end_ms=base_wall / 1_000_000 + 300_000,
    )
    active_market = {"value": old_market}
    engine = MSeriesRealtimeEngine(
        store=store, current_market=lambda: active_market["value"]
    )

    # Anchor the old market, then simulate the Collector clearing its expired
    # market while the public Spot socket continues normally.
    engine._handle(
        event(
            "spot",
            "trade",
            price=65_000.0,
            trade_id=500,
            received_wall_ns=base_wall - 100_000_000,
            received_monotonic_ns=base_mono - 100_000_000,
        )
    )
    active_market["value"] = None
    samples = (
        (0.8, 65_010.0, 501),
        (1.8, 64_990.0, 502),
        (2.8, 65_020.0, 503),
        (4.8, 64_980.0, 504),
    )
    for elapsed, price, trade_id in samples:
        engine._handle(
            event(
                "spot",
                "trade",
                price=price,
                trade_id=trade_id,
                received_wall_ns=base_wall + int(elapsed * 1_000_000_000),
                received_monotonic_ns=base_mono + int(elapsed * 1_000_000_000),
            )
        )

    # The first new-market book arrives after all four absolute deadlines.
    # Discovering it must reuse Spot history captured during the market=None
    # rollover gap.
    active_market["value"] = new_market
    engine._handle(
        prediction_event(
            market_id=102,
            update_id=600,
            received_wall_ns=base_wall + 5_100_000_000,
            received_monotonic_ns=base_mono + 5_100_000_000,
        )
    )
    store.calls.clear()
    engine._emit_due_m7_deadlines(base_mono + 5_100_000_000)

    scheduler_calls = [
        call for call in store.calls if call[2]["trigger_source"] == "scheduler"
    ]
    assert [call[2]["m7_deadline_seconds"] for call in scheduler_calls] == [
        1.0,
        2.0,
        3.0,
        5.0,
    ]
    assert [call[0]["spot_price"] for call in scheduler_calls] == pytest.approx(
        [65_010.0, 64_990.0, 65_020.0, 64_980.0]
    )
    assert all(
        call[2]["m7_asof_spot_age_ms"] == pytest.approx(200.0)
        for call in scheduler_calls
    )


def test_queue_overflow_invalidates_only_current_market():
    store = FakeStore()
    reference = market_reference()
    engine = MSeriesRealtimeEngine(
        store=store, current_market=lambda: reference, queue_max=1
    )
    engine._handle(event("spot", "trade", price=65_001.0, trade_id=601))
    engine.submit(event("spot", "trade", price=65_002.0, trade_id=602))
    engine.submit(event("spot", "trade", price=65_003.0, trade_id=603))
    assert engine.state()["marketDataIntegrityOk"] is False
    calls_before = len(store.calls)
    engine._handle(event("spot", "trade", price=65_004.0, trade_id=604))
    assert len(store.calls) == calls_before
    assert engine.state()["integritySkippedEvaluations"] >= 1

    reference = market_reference(market_id=102, topic_id=203)
    engine._handle(event("spot", "trade", price=65_005.0, trade_id=605))
    assert engine.state()["marketDataIntegrityOk"] is True
    assert len(store.calls) == calls_before + 1


def test_events_after_active_horizon_do_not_take_store_lock():
    store = ConfigFakeStore()
    now_ms = time.time_ns() / 1_000_000
    reference = market_reference(
        start_ms=now_ms - 20_000,
        end_ms=now_ms + 280_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(event("spot", "trade", price=65_001.0, trade_id=700))
    assert store.calls == []
    state = engine.state()
    assert state["evaluationHorizonSeconds"] == pytest.approx(10.0)
    assert state["outOfWindowSkips"] == 1


def test_m01_extends_realtime_horizon_for_late_prediction_books():
    class M01Store(ConfigFakeStore):
        def config(self):
            config = super().config()
            config["strategy_m01_enabled"] = True
            return config

    store = M01Store()
    now_ms = time.time_ns() / 1_000_000
    reference = market_reference(
        start_ms=now_ms - 100_000,
        end_ms=now_ms + 200_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(prediction_event())

    assert len(store.calls) == 1
    assert store.calls[0][2]["trigger_source"] == "prediction"
    state = engine.state()
    assert state["evaluationHorizonSeconds"] == pytest.approx(300.0)
    assert state["outOfWindowSkips"] == 0


def test_m01_long_horizon_does_not_send_late_spot_event_to_store():
    class M01Store(ConfigFakeStore):
        def config(self):
            config = super().config()
            config["strategy_m01_enabled"] = True
            return config

    store = M01Store()
    now_ms = time.time_ns() / 1_000_000
    reference = market_reference(
        start_ms=now_ms - 100_000,
        end_ms=now_ms + 200_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(event("spot", "trade", price=65_001.0, trade_id=701))

    assert store.calls == []
    state = engine.state()
    assert state["evaluationHorizonSeconds"] == pytest.approx(300.0)
    assert state["directionEvaluationHorizonSeconds"] == pytest.approx(10.0)
    assert state["directionOutOfWindowSkips"] == 1


def test_m01o_extends_horizon_and_attaches_observer_gate_to_prediction_event():
    class M01OStore(ConfigFakeStore):
        def config(self):
            config = super().config()
            config["strategy_m01o_enabled"] = True
            return config

    class Observer:
        def __init__(self):
            self.calls = []

        def reset_market(self, *args, **kwargs):
            return None

        def m01o_entry_gate(self, **kwargs):
            self.calls.append(kwargs)
            return {"allowed": True, "status": "ALLOW"}

    store = M01OStore()
    observer = Observer()
    now_ms = time.time_ns() / 1_000_000
    reference = market_reference(
        start_ms=now_ms - 100_000,
        end_ms=now_ms + 200_000,
    )
    engine = MSeriesRealtimeEngine(
        store=store,
        current_market=lambda: reference,
        market_observer=observer,
    )
    engine._handle(prediction_event())

    assert len(store.calls) == 1
    assert store.calls[0][2]["m01o_observer_gate"]["allowed"] is True
    assert observer.calls == [
        {"min_settled_samples": 6, "min_current_range_score": 2}
    ]
    state = engine.state()
    assert state["evaluationHorizonSeconds"] == pytest.approx(300.0)
    assert state["m01oGateEnabled"] is True


def test_m01o_realtime_attaches_all_three_profile_gates_to_same_event():
    class ProfilesStore(ConfigFakeStore):
        def config(self):
            config = super().config()
            config["strategy_m01o_enabled"] = True
            config["strategy_m01o_f1_enabled"] = True
            config["strategy_m01o_live_enabled"] = True
            return config

    class Observer:
        def __init__(self):
            self.calls = []

        def reset_market(self, *args, **kwargs):
            return None

        def m01o_entry_gates(self, **kwargs):
            self.calls.append(kwargs)
            return {
                "F2": {"profile": "F2", "allowed": False},
                "F1": {"profile": "F1", "allowed": True},
                "LIVE": {"profile": "LIVE", "allowed": True},
            }

    store = ProfilesStore()
    observer = Observer()
    engine = MSeriesRealtimeEngine(
        store=store,
        current_market=market_reference,
        market_observer=observer,
    )
    engine._handle(prediction_event())

    context = store.calls[0][2]
    assert set(context["m01o_observer_gates"]) == {"F2", "F1", "LIVE"}
    assert context["m01o_observer_gates"]["F2"]["allowed"] is False
    assert context["m01o_observer_gates"]["F1"]["allowed"] is True
    assert context["m01o_observer_gates"]["LIVE"]["allowed"] is True
    assert context["m01o_observer_gate"] is context["m01o_observer_gates"]["F2"]
    assert observer.calls == [
        {"min_settled_samples": 6, "min_current_range_score": 2}
    ]
    state = engine.state()
    assert state["m01oF2Enabled"] is True
    assert state["m01oF1Enabled"] is True
    assert state["m01oLiveEnabled"] is True


def test_m01_floor_extends_realtime_horizon_for_late_prediction_books():
    class M01FloorStore(ConfigFakeStore):
        def config(self):
            config = super().config()
            config["strategy_m01f_enabled"] = True
            return config

    store = M01FloorStore()
    now_ms = time.time_ns() / 1_000_000
    reference = market_reference(
        start_ms=now_ms - 100_000,
        end_ms=now_ms + 200_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(prediction_event())

    assert len(store.calls) == 1
    assert store.calls[0][2]["trigger_source"] == "prediction"
    state = engine.state()
    assert state["evaluationHorizonSeconds"] == pytest.approx(300.0)
    assert state["outOfWindowSkips"] == 0


def test_m01_rebound_extends_realtime_horizon_for_late_prediction_books():
    class M01ReboundStore(ConfigFakeStore):
        def config(self):
            config = super().config()
            config["strategy_m01r_enabled"] = True
            return config

    store = M01ReboundStore()
    now_ms = time.time_ns() / 1_000_000
    reference = market_reference(
        start_ms=now_ms - 200_000,
        end_ms=now_ms + 100_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(prediction_event())

    assert len(store.calls) == 1
    assert store.calls[0][2]["trigger_source"] == "prediction"
    state = engine.state()
    assert state["evaluationHorizonSeconds"] == pytest.approx(300.0)
    assert state["outOfWindowSkips"] == 0


def test_m01w_extends_realtime_horizon_for_late_prediction_books():
    class M01WStore(ConfigFakeStore):
        def config(self):
            config = super().config()
            config["strategy_m01w_enabled"] = True
            return config

    store = M01WStore()
    now_ms = time.time_ns() / 1_000_000
    reference = market_reference(
        start_ms=now_ms - 150_000,
        end_ms=now_ms + 150_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(prediction_event())

    assert len(store.calls) == 1
    assert engine.state()["evaluationHorizonSeconds"] == pytest.approx(300.0)


def test_mx_exit_engine_still_receives_events_after_m_opening_horizon():
    store = MxAwareStore()
    now_ms = time.time_ns() / 1_000_000
    reference = market_reference(
        start_ms=now_ms - 100_000,
        end_ms=now_ms + 200_000,
    )
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(prediction_event())
    assert len(store.mx_calls) == 1
    mx_snapshot, fee_bps, mx_context = store.mx_calls[0]
    assert mx_snapshot["market_elapsed_seconds"] == pytest.approx(100.0, abs=0.1)
    assert fee_bps == 200
    assert mx_context["trigger_source"] == "prediction"
    # Pair arbitrage is intentionally excluded here: this stream carries one
    # normalized UP book and derives DOWN as its complement.  The independent
    # dual-token REST collector owns complementary-pair evaluation.
    assert store.pair_calls == []
    # The original M opening strategies remain protected by the 10 s horizon.
    assert store.calls == []


def test_server_clock_offset_is_applied_to_seconds_left():
    store = FakeStore()
    reference = market_reference(server_clock_offset_ms=500.0)
    engine = MSeriesRealtimeEngine(store=store, current_market=lambda: reference)
    engine._handle(prediction_event())
    seconds_left = store.calls[-1][0]["seconds_left"]
    assert seconds_left == pytest.approx(298.5, abs=0.1)
    assert store.calls[-1][2]["server_clock_offset_ms"] == pytest.approx(500.0)


def test_observer_forwards_accepted_event_to_nonblocking_realtime_sink(tmp_path):
    accepted = []
    observer = MicrostructureObserver(
        api_key=None,
        api_secret=None,
        current_market_id=lambda: None,
        realtime_event_sink=accepted.append,
        db_path=tmp_path / "micro.db",
    )
    item = event("spot", "trade", price=65_001.0)
    observer._accept_event("spot", item)
    assert accepted == [item]
    assert observer.events.qsize() == 1


def test_observer_isolates_realtime_sink_failure_from_market_socket(tmp_path):
    def broken_sink(_):
        raise RuntimeError("local paper engine failed")

    observer = MicrostructureObserver(
        api_key=None,
        api_secret=None,
        current_market_id=lambda: None,
        realtime_event_sink=broken_sink,
        db_path=tmp_path / "micro.db",
    )
    observer._accept_event("spot", event("spot", "trade", price=65_001.0))
    assert observer.events.qsize() == 1
    state = observer.state()
    assert state["storage"]["realtimeSinkErrors"] == 1
    assert "local paper engine failed" in state["storage"]["realtimeSinkLastError"]


def test_realtime_submit_refreshes_observer_before_queued_store_work():
    class Observer:
        def __init__(self):
            self.resets = []
            self.updates = []

        def reset_market(self, *args, **kwargs):
            self.resets.append((args, kwargs))

        def update_tick(self, **kwargs):
            self.updates.append(kwargs)

    observer = Observer()
    reference = market_reference()
    engine = MSeriesRealtimeEngine(
        store=FakeStore(),
        current_market=lambda: reference,
        market_observer=observer,
    )
    spot = event("spot", "trade", price=65_001.0, trade_id=900)
    prediction = prediction_event(best_ask=0.29)

    engine.submit(spot)
    engine.submit(prediction)

    assert engine.events.qsize() == 2
    assert len(observer.resets) == 2
    assert observer.updates[0]["spot_price"] == pytest.approx(65_001.0)
    assert observer.updates[1]["require_verified_book_freshness"] is True
    assert observer.updates[1]["book_age_seconds"] is not None
    assert observer.updates[0]["up_ask"] is None
    assert observer.updates[1]["spot_price"] is None
    assert observer.updates[1]["up_ask"] == pytest.approx(0.29)
    assert "down_ask" not in observer.updates[1]
