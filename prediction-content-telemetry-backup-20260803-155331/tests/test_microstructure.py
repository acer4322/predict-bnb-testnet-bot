import hashlib
import hmac
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from predict_bot.microstructure import (
    FeatureEngine,
    MicrostructureObserver,
    MicrostructureStore,
    build_prediction_ws_url,
    microprice,
    parse_combined_message,
    parse_prediction_message,
    queue_imbalance,
    signed_aggressor_quantity,
)


def combined(stream: str, data: dict) -> str:
    return json.dumps({"stream": stream, "data": data})


def test_microprice_and_queue_imbalance_use_opposite_queue_weights():
    bids = [[100.0, 30.0], [99.0, 10.0]]
    asks = [[101.0, 10.0], [102.0, 10.0]]
    assert queue_imbalance(bids, asks) == pytest.approx(1 / 3)
    assert microprice(100.0, 30.0, 101.0, 10.0) == pytest.approx(100.75)
    assert microprice(None, 1, 101, 1) is None


def test_buyer_maker_is_sell_aggressor_and_futures_uses_visible_nq():
    signed, visible = signed_aggressor_quantity({"q": "5", "m": True}, futures=False)
    assert signed == pytest.approx(-5)
    assert visible == pytest.approx(5)
    signed, visible = signed_aggressor_quantity(
        {"q": "8", "nq": "6", "m": False}, futures=True
    )
    assert signed == pytest.approx(6)
    assert visible == pytest.approx(6)


def test_spot_combined_parsers_preserve_missing_bookticker_event_time():
    trade = parse_combined_message(
        combined(
            "btcusdt@trade",
            {"e": "trade", "E": 1000, "T": 999, "t": 7, "p": "65000", "q": "0.2", "m": False},
        ),
        source="spot", received_wall_ns=2_000_000_000,
        received_monotonic_ns=3_000_000_000, session_id="s",
    )
    assert trade is not None
    assert trade["stream"] == "trade"
    assert trade["aggressor"] == 1
    assert trade["exchange_event_ms"] == 1000

    ticker = parse_combined_message(
        combined(
            "btcusdt@bookTicker",
            {"u": 9, "s": "BTCUSDT", "b": "64999", "B": "2", "a": "65001", "A": "3"},
        ),
        source="spot", received_wall_ns=2_000_000_000,
        received_monotonic_ns=3_000_000_000, session_id="s",
    )
    assert ticker is not None
    assert ticker["exchange_event_ms"] is None
    assert ticker["best_bid"] == pytest.approx(64999)
    assert ticker["best_ask_qty"] == pytest.approx(3)


def test_depth_and_futures_routing_payloads_are_normalized_and_um_only():
    depth = parse_combined_message(
        combined(
            "btcusdt@depth10@100ms",
            {
                "E": 1000, "U": 10, "u": 12, "pu": 9, "st": 1,
                "b": [["100", "2"], ["99", "3"]],
                "a": [["102", "4"], ["101", "1"]],
            },
        ),
        source="futures", received_wall_ns=2_000_000_000,
        received_monotonic_ns=3_000_000_000, session_id="f",
    )
    assert depth is not None
    assert depth["bids"][0] == [100.0, 2.0]
    assert depth["asks"][0] == [101.0, 1.0]
    assert depth["previous_update_id"] == 9

    coin_margined = parse_combined_message(
        combined("btcusdt@aggTrade", {"st": 2, "p": "100", "q": "1", "m": False}),
        source="futures", received_wall_ns=1, received_monotonic_ns=1, session_id="f",
    )
    assert coin_margined is None


def test_prediction_signature_is_sorted_and_matches_exact_url_payload():
    url, canonical = build_prediction_ws_url(
        "secret", timestamp_ms=123, random_value="abc", topic="web3_prediction_orderbook_data"
    )
    assert canonical == (
        "random=abc&recvWindow=30000&timestamp=123&topic=web3_prediction_orderbook_data"
    )
    expected = hmac.new(b"secret", canonical.encode(), hashlib.sha256).hexdigest()
    assert url.endswith(f"{canonical}&signature={expected}")


def test_prediction_double_json_parse_and_command_ignore():
    raw = json.dumps(
        {
            "type": "TOPIC",
            "topic": "web3_prediction_orderbook_42",
            "data": json.dumps(
                {
                    "msgType": "orderbook", "marketId": 42, "updateTimestampMs": 1234,
                    "asks": [["0.62", "50"]], "bids": [["0.61", "40"]],
                }
            ),
        }
    )
    event = parse_prediction_message(
        raw, received_wall_ns=2_000_000_000,
        received_monotonic_ns=3_000_000_000, session_id="p",
    )
    assert event is not None
    assert event["market_id"] == 42
    assert event["best_ask"] == pytest.approx(0.62)
    assert event["prediction_book_version_ms"] == 1234
    # Transitional storage alias only; local freshness never uses this field.
    assert event["exchange_event_ms"] == 1234
    assert json.loads(event["raw_json"])["type"] == "TOPIC"
    assert parse_prediction_message(
        json.dumps({"type": "COMMAND", "data": "{}"}),
        received_wall_ns=1, received_monotonic_ns=1, session_id="p",
    ) is None

    production_raw = json.loads(raw)
    production_raw["type"] = "DATA"
    production_event = parse_prediction_message(
        json.dumps(production_raw), received_wall_ns=2_000_000_000,
        received_monotonic_ns=3_000_000_000, session_id="p",
    )
    assert production_event is not None
    assert production_event["market_id"] == 42


def prediction_event(*, timestamp: int, ask_size: float, bid_size: float = 40) -> dict:
    return {
        "source": "prediction", "stream": "orderbook", "market_id": 42,
        "exchange_event_ms": timestamp, "received_wall_ns": timestamp * 1_000_000,
        "received_monotonic_ns": timestamp * 1_000_000, "best_ask": 0.62,
        "best_ask_qty": ask_size, "best_bid": 0.61, "best_bid_qty": bid_size,
        "asks": [[0.62, ask_size]], "bids": [[0.61, bid_size]],
    }


def test_feature_engine_labels_liquidity_removed_not_cancelled():
    engine = FeatureEngine()
    assert engine.update(prediction_event(timestamp=1000, ask_size=100)) == []
    events = engine.update(prediction_event(timestamp=1100, ask_size=40))
    assert len(events) == 1
    assert events[0]["side"] == "UP"
    assert events[0]["kind"] == "ASK_LIQUIDITY_REMOVED"
    assert "unknown" in events[0]["detail"]
    assert events[0]["strength"] == pytest.approx(0.6)
    snapshot = engine.snapshot(1_200_000_000, 1_200_000_000)
    assert snapshot["prediction_up_mid"] == pytest.approx(0.615)
    assert snapshot["prediction_removal_direction"] == "UP"


def test_prediction_rollover_first_snapshot_is_baseline_not_removal():
    engine = FeatureEngine()
    assert engine.update(prediction_event(timestamp=1000, ask_size=100)) == []
    rolled = prediction_event(timestamp=1100, ask_size=1, bid_size=1)
    rolled["market_id"] = 43
    rolled.update(best_ask=0.99, best_bid=0.01, asks=[[0.99, 1]], bids=[[0.01, 1]])
    assert engine.update(rolled) == []
    snapshot = engine.snapshot(1_200_000_000, 1_200_000_000)
    assert snapshot["market_id"] == 43
    assert snapshot["prediction_removal_direction"] is None


def test_stale_books_and_one_sided_prediction_do_not_publish_features():
    engine = FeatureEngine()
    engine.update(
        {
            "source": "spot", "stream": "depth", "received_monotonic_ns": 1_000_000_000,
            "best_bid": 100.0, "best_bid_qty": 5.0, "best_ask": 101.0,
            "best_ask_qty": 5.0, "bids": [[100.0, 5.0]], "asks": [[101.0, 5.0]],
        }
    )
    one_sided = prediction_event(timestamp=1000, ask_size=10)
    one_sided.update(best_bid=None, best_bid_qty=None, bids=[])
    engine.update(one_sided)
    fresh = engine.snapshot(1_100_000_000, 1_100_000_000)
    assert fresh["prediction_up_mid"] is None
    stale = engine.snapshot(4_000_000_000, 4_000_000_000)
    assert stale["spot_microprice"] is None
    assert stale["spot_queue_imbalance"] is None
    assert stale["spot_price"] is None


def test_store_uses_independent_wal_database_and_persists_all_three_tables(tmp_path: Path):
    store = MicrostructureStore(tmp_path / "microstructure.db")
    event = {
        "source": "spot", "stream": "trade", "market_id": None,
        "exchange_event_ms": 1, "exchange_trade_ms": 1,
        "received_wall_ns": 2, "received_monotonic_ns": 3,
        "enqueued_monotonic_ns": 4, "session_id": "s", "price": 100.0,
        "quantity": 1.0, "visible_quantity": 1.0, "aggressor": 1,
        "bids": [], "asks": [], "raw_json": "{}", "parser_version": "test",
    }
    snapshot = FeatureEngine().snapshot(10, 20)
    liquidity = {
        "timestamp_ns": 20, "market_id": 42, "side": "UP",
        "kind": "ASK_LIQUIDITY_REMOVED", "strength": 0.5, "price": 0.6,
        "detail": "test",
    }
    gap = {
        "first_timestamp_ns": 1, "last_timestamp_ns": 2,
        "source": "spot", "reason": "QUEUE_OVERFLOW", "dropped_count": 3,
        "detail": "test",
    }
    with store._connect() as db:
        store.insert_event(db, event)
        store.insert_snapshot(db, snapshot)
        store.insert_liquidity(db, liquidity)
        store.insert_gap(db, gap)
    with sqlite3.connect(store.path) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.execute("SELECT COUNT(*) FROM microstructure_events").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM microstructure_snapshots").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM microstructure_liquidity_events").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM microstructure_gap_events").fetchone()[0] == 1


def test_observer_filters_other_prediction_markets_and_out_of_order(tmp_path: Path):
    observer = MicrostructureObserver(
        api_key=None, api_secret=None, current_market_id=lambda: 42,
        db_path=tmp_path / "micro.db",
    )
    wrong = prediction_event(timestamp=1000, ask_size=10)
    wrong["market_id"] = 41
    observer._accept_event("prediction", wrong)
    assert observer.events.empty()

    first = prediction_event(timestamp=1000, ask_size=10)
    first.update(session_id="p", enqueued_monotonic_ns=time.monotonic_ns(), parser_version="test")
    observer._accept_event("prediction", first)
    assert observer.events.qsize() == 1
    older = prediction_event(timestamp=999, ask_size=5)
    older.update(session_id="p", enqueued_monotonic_ns=time.monotonic_ns(), parser_version="test")
    observer._accept_event("prediction", older)
    assert observer.events.qsize() == 1
    assert observer.out_of_order == 1


def test_futures_partial_depth_sequence_filters_old_and_rebaselines_gap(tmp_path: Path):
    observer = MicrostructureObserver(
        api_key=None, api_secret=None, current_market_id=lambda: 42,
        db_path=tmp_path / "micro.db",
    )

    def depth(update_id: int, previous_id: int) -> dict:
        return {
            "source": "futures", "stream": "depth", "market_id": None,
            "exchange_event_ms": 1, "received_wall_ns": time.time_ns(),
            "received_monotonic_ns": time.monotonic_ns(), "update_id": update_id,
            "previous_update_id": previous_id,
        }

    observer._accept_event("futures_public", depth(10, 9))
    observer._accept_event("futures_public", depth(10, 9))
    assert observer.out_of_order == 1
    observer._accept_event("futures_public", depth(12, 8))
    assert observer.sequence_gaps == 1
    assert observer.events.qsize() == 2
    observer.last_futures_update = None  # Same reset performed by a new WSS session.
    observer._accept_event("futures_public", depth(2, 1))
    assert observer.events.qsize() == 3


def test_state_contract_is_dashboard_safe_without_starting_sockets(tmp_path: Path):
    observer = MicrostructureObserver(
        api_key=None, api_secret=None, current_market_id=lambda: None,
        db_path=tmp_path / "micro.db",
    )
    state = observer.state()
    assert set(state["streams"]) == {
        "spot_trade", "spot_book", "futures", "prediction"
    }
    assert "spotMicroprice" in state["metrics"]
    assert state["storage"]["rawRetentionHours"] > 0
    assert state["recentLiquidityEvents"] == []


def test_prediction_dynamic_url_tracks_current_market(tmp_path: Path):
    observer = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        db_path=tmp_path / "micro.db",
    )
    url = observer._prediction_url()
    assert "topic=web3_prediction_orderbook_42" in url
    assert observer.prediction_subscription_market_id == 42


def test_prediction_rollover_wakes_backoff_without_active_socket(tmp_path: Path):
    """A rollover during transport backoff must request an immediate retry."""
    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=lambda: 43,
        db_path=tmp_path / "micro.db",
    )
    observer.prediction_subscription_market_id = 42
    thread = threading.Thread(target=observer._prediction_market_loop, daemon=True)
    thread.start()
    try:
        assert observer.prediction_rollover_reconnect.wait(1.0)
        assert observer.engine.prediction_market_id == 43
        assert "prediction" not in observer.active_apps
    finally:
        observer.stop_event.set()
        thread.join(timeout=1.0)
    assert not thread.is_alive()


def test_unverified_prediction_reconnect_is_timeout_gated_and_throttled(tmp_path: Path):
    class App:
        def __init__(self):
            self.closes = 0

        def close(self):
            self.closes += 1

    observer = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        db_path=tmp_path / "throttled-reconnect.db",
    )
    app = App()
    observer.prediction_orientation_market_id = 42
    observer.prediction_orientation_unverified_since_ns = (
        time.monotonic_ns() - 11_000_000_000
    )
    observer.active_apps["prediction"] = app

    observer._reconnect_unverified_prediction_if_timed_out()
    observer._reconnect_unverified_prediction_if_timed_out()

    assert app.closes == 1
    assert observer.orientation_reconnect_requests == 1
    assert observer.orientation_failure_reason == "ORIENTATION_TIMEOUT"
    assert observer.state()["streams"]["prediction"]["orientationStatus"] == "DEGRADED"


def test_spot_trade_watchdog_reconnects_silence_once_with_throttle(tmp_path: Path):
    class App:
        def __init__(self):
            self.closes = 0

        def close(self):
            self.closes += 1

    observer = MicrostructureObserver(
        api_key=None, api_secret=None, current_market_id=lambda: None,
        db_path=tmp_path / "spot-watchdog.db",
    )
    app = App()
    observer.active_apps["spot_trade"] = app
    observer.stream_stats["spot_trade"].update(
        status="LIVE",
        openedMonotonicNs=time.monotonic_ns() - 6_000_000_000,
    )
    thread = threading.Thread(target=observer._spot_trade_reconnect_watchdog, daemon=True)
    thread.start()
    try:
        time.sleep(2.2)
    finally:
        observer.stop_event.set()
        thread.join(timeout=2.0)

    assert app.closes == 1
    assert observer.spot_trade_reconnect_requests == 1


def orientation_reference(
    *,
    market_id: int = 42,
    bid: float = 0.70,
    ask: float = 0.71,
    received_wall_ns: int = 1_000_000_000,
) -> dict:
    return {
        "market_id": market_id,
        "up_bid": bid,
        "up_ask": ask,
        "up_book_timestamp_ms": 123,
        "received_wall_ns": received_wall_ns,
    }


def oriented_event(
    *,
    market_id: int = 42,
    bid: float = 0.70,
    ask: float = 0.71,
    version_ms: int = 999,
    received_wall_ns: int = 1_000_000_000,
) -> dict:
    event = prediction_event(timestamp=version_ms, ask_size=10)
    event.update(
        market_id=market_id,
        prediction_book_version_ms=version_ms,
        received_wall_ns=received_wall_ns,
        received_monotonic_ns=received_wall_ns,
        best_bid=bid,
        best_ask=ask,
        bids=[[bid, 5]],
        asks=[[ask, 10]],
    )
    return event


@pytest.mark.parametrize(
    ("bid", "ask", "candidate", "verified"),
    [
        (0.70, 0.71, "DIRECT_CANDIDATE", "DIRECT_UP_VERIFIED"),
        (0.29, 0.30, "INVERTED_CANDIDATE", "INVERTED_TO_UP_VERIFIED"),
    ],
)
def test_prediction_orientation_requires_two_matching_candidates(
    tmp_path: Path, bid: float, ask: float, candidate: str, verified: str,
):
    observer = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        prediction_reference=lambda: orientation_reference(),
        db_path=tmp_path / f"{candidate}.db",
    )
    first = oriented_event(bid=bid, ask=ask, version_ms=1000)
    observer._orient_prediction_event(first)
    assert observer.prediction_orientation == candidate
    assert first["feature_eligible"] is False

    second = oriented_event(bid=bid, ask=ask, version_ms=1001)
    observer._orient_prediction_event(second)
    assert observer.prediction_orientation == verified
    assert second["feature_eligible"] is True
    if verified == "INVERTED_TO_UP_VERIFIED":
        assert second["best_bid"] == pytest.approx(0.70)
        assert second["best_ask"] == pytest.approx(0.71)


def test_prediction_orientation_uses_receipt_time_not_equal_book_version(tmp_path: Path):
    reference = orientation_reference(received_wall_ns=2_000_000_000)
    reference["up_book_timestamp_ms"] = None
    observer = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        prediction_reference=lambda: reference,
        db_path=tmp_path / "receipt-time.db",
    )
    first = oriented_event(version_ms=99, received_wall_ns=2_100_000_000)
    second = oriented_event(version_ms=100, received_wall_ns=2_200_000_000)
    observer._orient_prediction_event(first)
    observer._orient_prediction_event(second)
    assert observer.prediction_orientation == "DIRECT_UP_VERIFIED"


@pytest.mark.parametrize(
    ("reference", "event", "reason"),
    [
        (orientation_reference(market_id=41), oriented_event(), "MARKET_ID_MISMATCH"),
        (
            orientation_reference(received_wall_ns=1_000_000_000),
            oriented_event(received_wall_ns=7_000_000_001),
            "RECEIPT_WINDOW_EXCEEDED",
        ),
        (
            orientation_reference(bid=0.50, ask=0.50),
            oriented_event(bid=0.50, ask=0.50),
            "AMBIGUOUS_OR_PRICE_ERROR",
        ),
    ],
)
def test_prediction_orientation_fail_closed_cases(
    tmp_path: Path, reference: dict, event: dict, reason: str,
):
    observer = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        prediction_reference=lambda: reference,
        db_path=tmp_path / f"{reason}.db",
    )
    observer._orient_prediction_event(event)
    assert observer.prediction_orientation == "UNVERIFIED"
    assert event["feature_eligible"] is False
    assert observer.orientation_failure_reason == reason


def test_prediction_orientation_rollover_and_reconnect_clear_candidate(tmp_path: Path):
    reference = orientation_reference()
    observer = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: reference["market_id"],
        prediction_reference=lambda: reference,
        db_path=tmp_path / "reset-candidate.db",
    )
    observer._orient_prediction_event(oriented_event())
    assert observer.prediction_orientation_candidate_count == 1

    reference = orientation_reference(market_id=43)
    observer._orient_prediction_event(oriented_event(market_id=43))
    assert observer.prediction_orientation == "DIRECT_CANDIDATE"
    assert observer.prediction_orientation_candidate_count == 1

    observer._invalidate_prediction_state(43)
    assert observer.prediction_orientation == "UNVERIFIED"
    assert observer.prediction_orientation_candidate_count == 0
    observer._orient_prediction_event(oriented_event(market_id=43))
    assert observer.prediction_orientation == "DIRECT_CANDIDATE"


def test_prediction_version_age_is_not_transport_latency_but_blocks_features(
    tmp_path: Path,
):
    now_wall_ns = time.time_ns()
    now_mono_ns = time.monotonic_ns()
    reference = orientation_reference(received_wall_ns=now_wall_ns)
    observer = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        prediction_reference=lambda: reference,
        db_path=tmp_path / "prediction-latency.db",
    )
    for index in range(2):
        event = oriented_event(
            version_ms=int(now_wall_ns / 1_000_000) - 55_000 + index,
            received_wall_ns=now_wall_ns + index,
        )
        event["received_monotonic_ns"] = now_mono_ns + index
        observer._accept_event("prediction", event)
    state = observer.state()["streams"]["prediction"]
    assert state["transportLatencyMs"] is None
    assert state["bookVersionAgeMs"] >= 54_000
    assert state["localReceiptAgeMs"] < 1_000
    assert state["orientationStatus"] == "DEGRADED"
    assert state["orientationHealthy"] is False
    assert state["bookVersionHealthy"] is False
    assert state["orientationFailureReason"] == "STALE_BOOK_VERSION"
    assert state["stalePredictionEvents"] == 2
    assert state["eligiblePredictionEvents"] == 0


@pytest.mark.parametrize("stream_name", ["spot_trade", "futures_public"])
def test_spot_and_futures_transport_latency_remains_available(
    tmp_path: Path, stream_name: str,
):
    observer = MicrostructureObserver(
        api_key=None, api_secret=None, current_market_id=lambda: None,
        db_path=tmp_path / f"{stream_name}.db",
    )
    source = "spot" if stream_name == "spot_trade" else "futures"
    event = {
        "source": source,
        "stream": "trade" if source == "spot" else "aggTrade",
        "exchange_event_ms": 1_000,
        "received_wall_ns": 2_000_000_000,
        "received_monotonic_ns": time.monotonic_ns(),
    }
    observer._accept_event(stream_name, event)
    assert observer.stream_stats[stream_name]["latencies"][-1] == pytest.approx(1_000)


def test_connected_but_silent_stream_becomes_stale(tmp_path: Path):
    observer = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        db_path=tmp_path / "micro.db",
    )
    observer.stream_stats["prediction"].update(
        status="LIVE", openedMonotonicNs=time.monotonic_ns() - 11_000_000_000,
    )
    prediction = observer.state()["streams"]["prediction"]
    assert prediction["status"] == "STALE"
    assert "no accepted events" in prediction["error"]


def test_prediction_rollover_request_records_and_throttles(
    tmp_path: Path,
):
    class App:
        def __init__(self):
            self.closes = 0

        def close(self):
            self.closes += 1

    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=lambda: 43,
        db_path=tmp_path / "rollover-request.db",
    )
    app = App()
    observer.prediction_subscription_market_id = 42
    observer.active_apps["prediction"] = app

    first = observer._request_prediction_rollover_reconnect(
        wanted_market_id=43,
        subscribed_market_id=42,
        reason="TEST_MARKET_ID_MISMATCH",
    )
    second = observer._request_prediction_rollover_reconnect(
        wanted_market_id=43,
        subscribed_market_id=42,
        reason="TEST_MARKET_ID_MISMATCH",
    )

    assert first is True
    assert second is False
    assert app.closes == 1
    assert observer.engine.prediction_market_id == 43
    assert observer.prediction_rollover_reconnect_requests == 1
    assert observer.last_rollover_wanted_market_id == 43
    assert observer.last_rollover_subscribed_market_id == 42
    assert observer.last_rollover_reconnect_reason == (
        "TEST_MARKET_ID_MISMATCH"
    )


def test_prediction_market_watch_survives_callback_exception(
    tmp_path: Path,
):
    calls = 0

    def current_market_id():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary current-market failure")
        return 43

    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=current_market_id,
        db_path=tmp_path / "market-watch-survives.db",
    )
    observer.prediction_subscription_market_id = 42
    worker = threading.Thread(
        target=observer._prediction_market_loop,
        daemon=True,
    )
    observer.market_watch_thread = worker
    worker.start()
    try:
        deadline = time.monotonic() + 2.0
        while (
            observer.prediction_rollover_reconnect_requests < 1
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        assert worker.is_alive()
        assert observer.market_watch_failures == 1
        assert "temporary current-market failure" in str(
            observer.market_watch_last_error
        )
        assert observer.prediction_rollover_reconnect_requests == 1
        assert observer.market_watch_heartbeat_ns > 0
    finally:
        observer.stop_event.set()
        observer.prediction_rollover_reconnect.set()
        worker.join(timeout=1.0)

    assert not worker.is_alive()


def test_prediction_supervisor_restarts_dead_market_watch_worker(
    tmp_path: Path,
):
    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=lambda: 43,
        db_path=tmp_path / "market-watch-supervisor.db",
    )
    observer.prediction_subscription_market_id = 42

    dead_worker = threading.Thread(target=lambda: None)
    dead_worker.start()
    dead_worker.join(timeout=1.0)
    assert not dead_worker.is_alive()
    observer.market_watch_thread = dead_worker

    supervisor = threading.Thread(
        target=observer._prediction_market_watch_supervisor_loop,
        daemon=True,
    )
    observer.prediction_supervisor_thread = supervisor
    supervisor.start()
    try:
        deadline = time.monotonic() + 3.0
        while (
            (
                observer.market_watch_restarts < 1
                or observer.market_watch_thread is dead_worker
                or not observer.market_watch_thread.is_alive()
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.02)

        assert observer.market_watch_restarts >= 1
        assert observer.market_watch_thread is not dead_worker
        assert observer.market_watch_thread.is_alive()
    finally:
        observer.stop_event.set()
        observer.prediction_rollover_reconnect.set()
        supervisor.join(timeout=1.0)
        if observer.market_watch_thread is not None:
            observer.market_watch_thread.join(timeout=1.0)

    assert not supervisor.is_alive()


def test_prediction_state_exposes_market_watch_health(
    tmp_path: Path,
):
    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=lambda: 43,
        db_path=tmp_path / "market-watch-health.db",
    )
    observer.prediction_subscription_market_id = 42
    observer.market_watch_thread = threading.current_thread()
    observer.prediction_supervisor_thread = threading.current_thread()
    observer.market_watch_heartbeat_ns = time.monotonic_ns()

    prediction = observer.state()["streams"]["prediction"]

    assert prediction["marketWatchThreadAlive"] is True
    assert prediction["predictionSupervisorThreadAlive"] is True
    assert prediction["marketWatchHealthy"] is False
    assert prediction["wantedMarketId"] == 43
    assert prediction["subscriptionMarketId"] == 42
    assert prediction["marketIdMismatch"] is True
    assert prediction["orientationHealthy"] is False
    assert prediction["orientationFailureReason"] == (
        "PREDICTION_MARKET_ID_MISMATCH"
    )
