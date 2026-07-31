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
    assert set(state["streams"]) == {"spot", "futures", "prediction"}
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


def test_prediction_book_mapping_is_verified_and_inverted_to_up(tmp_path: Path):
    direct = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        prediction_reference=lambda: {
            "market_id": 42,
            "up_bid": 0.70,
            "up_ask": 0.71,
            "up_book_timestamp_ms": 1000,
        },
        db_path=tmp_path / "direct.db",
    )
    direct_event = prediction_event(timestamp=1000, ask_size=10)
    direct_event.update(best_bid=0.70, best_ask=0.71, bids=[[0.70, 5]], asks=[[0.71, 10]])
    direct._orient_prediction_event(direct_event)
    assert direct.prediction_orientation == "DIRECT_UP_VERIFIED"
    assert direct_event["feature_eligible"] is True

    inverted = MicrostructureObserver(
        api_key="key", api_secret="secret", current_market_id=lambda: 42,
        prediction_reference=lambda: {
            "market_id": 42,
            "up_bid": 0.70,
            "up_ask": 0.71,
            "up_book_timestamp_ms": 1000,
        },
        db_path=tmp_path / "inverted.db",
    )
    inverted_event = prediction_event(timestamp=1000, ask_size=10)
    inverted_event.update(best_bid=0.29, best_ask=0.30, bids=[[0.29, 5]], asks=[[0.30, 10]])
    inverted._orient_prediction_event(inverted_event)
    assert inverted.prediction_orientation == "INVERTED_TO_UP_VERIFIED"
    assert inverted_event["best_bid"] == pytest.approx(0.70)
    assert inverted_event["best_ask"] == pytest.approx(0.71)


def test_prediction_orientation_uses_same_exchange_timestamp_during_price_cross(
    tmp_path: Path,
):
    """A stale 0.20 REST quote must not invert a newer direct-UP 0.80 book."""
    reference: dict | None = None
    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=lambda: 42,
        prediction_reference=lambda: reference,
        db_path=tmp_path / "timestamp-aligned.db",
    )

    before_cross = prediction_event(timestamp=1000, ask_size=10)
    before_cross.update(
        best_bid=0.20,
        best_ask=0.21,
        bids=[[0.20, 5]],
        asks=[[0.21, 10]],
    )
    observer._orient_prediction_event(before_cross)
    assert observer.prediction_orientation == "UNVERIFIED"

    reference = {
        "market_id": 42,
        "up_bid": 0.20,
        "up_ask": 0.21,
        "up_book_timestamp_ms": 1000,
    }
    after_cross = prediction_event(timestamp=1100, ask_size=10)
    after_cross.update(
        best_bid=0.79,
        best_ask=0.80,
        bids=[[0.79, 5]],
        asks=[[0.80, 10]],
    )
    observer._orient_prediction_event(after_cross)

    assert observer.prediction_orientation == "DIRECT_UP_VERIFIED"
    assert after_cross["feature_eligible"] is True
    assert after_cross["best_bid"] == pytest.approx(0.79)
    assert after_cross["best_ask"] == pytest.approx(0.80)


def test_prediction_orientation_fails_closed_without_matching_timestamp(tmp_path: Path):
    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=lambda: 42,
        prediction_reference=lambda: {
            "market_id": 42,
            "up_bid": 0.20,
            "up_ask": 0.21,
            "up_book_timestamp_ms": 999,
        },
        db_path=tmp_path / "no-timestamp-match.db",
    )
    event = prediction_event(timestamp=1100, ask_size=10)
    event.update(best_bid=0.79, best_ask=0.80)
    observer._orient_prediction_event(event)
    assert observer.prediction_orientation == "UNVERIFIED"
    assert event["feature_eligible"] is False


def test_prediction_orientation_fails_closed_when_rest_timestamp_is_missing(
    tmp_path: Path,
):
    observer = MicrostructureObserver(
        api_key="key",
        api_secret="secret",
        current_market_id=lambda: 42,
        prediction_reference=lambda: {
            "market_id": 42,
            "up_bid": 0.20,
            "up_ask": 0.21,
            "up_book_timestamp_ms": None,
        },
        db_path=tmp_path / "missing-rest-timestamp.db",
    )
    event = prediction_event(timestamp=1100, ask_size=10)
    event.update(best_bid=0.79, best_ask=0.80)
    observer._orient_prediction_event(event)
    assert observer.prediction_orientation == "UNVERIFIED"
    assert event["feature_eligible"] is False


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
