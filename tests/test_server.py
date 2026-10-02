from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

import predict_bot.server as server_module

from predict_bot.server import (
    Collector,
    Store,
    allowed_dashboard_origin,
    allowed_live_rules_request,
    allowed_manual_sell_request,
    normal_cdf,
    rolling_volatility_per_sqrt_second,
    temperature_calibrate_probability,
)


def collector_market(
    *, topic_id: int, market_id: int, start_ms: int, end_ms: int
) -> dict:
    return {
        "marketTopicId": topic_id,
        "startDate": start_ms,
        "endDate": end_ms,
        "title": f"BTC 5m {market_id}",
        "feeRateBps": 200,
        "variantData": {"startPrice": "65000"},
        "_selectedMarket": {
            "market": {"marketId": market_id},
            "up": {"tokenId": f"up-{market_id}"},
            "down": {"tokenId": f"down-{market_id}"},
        },
    }


def snapshot(**overrides):
    row = {
        "timestamp": "2026-07-16T00:00:00+00:00",
        "topic_id": 1,
        "market_id": 2,
        "title": "BTC Up or Down 5m",
        "start_price": 65000.0,
        "spot_price": 64990.0,
        "seconds_left": 250.0,
        "up_ask": 0.15,
        "up_bid": 0.14,
        "down_ask": 0.85,
        "down_bid": 0.84,
    }
    row.update(overrides)
    return row


def test_confirmation_add_shadow_only_adds_after_price_confirms_direction(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.open_trade(
        strategy="R_MICROPRICE", topic_id=701, market_id=702, side="UP",
        entry=0.20, target=None, stake=5.0, fee_rate_bps=200,
        note="confirmation-add source",
    )
    first = snapshot(
        topic_id=701, market_id=702,
        timestamp="2026-08-02T00:00:01+00:00", seconds_left=100.0,
        up_ask=0.20, up_bid=0.19, up_ask_size=100.0,
        book_age_ms=10.0, book_skew_ms=0.0, up_book_timestamp_ms=1,
    )
    created = store.process_confirmation_add_shadows(first)
    assert created["created"] == 1
    assert created["filledStakeUsdt"] == 0.0
    assert created["paperOnly"] is True
    assert created["liveOrdersAffected"] is False
    assert float(store.db.execute(
        "SELECT stake FROM trades WHERE strategy='R_CONFIRM_ADD_10'"
    ).fetchone()["stake"]) == pytest.approx(1.0)

    confirmed_once = {
        **first, "timestamp": "2026-08-02T00:00:02+00:00",
        "up_ask": 0.225, "up_bid": 0.215, "up_book_timestamp_ms": 2,
    }
    result = store.process_confirmation_add_shadows(confirmed_once)
    assert result["filledStakeUsdt"] == pytest.approx(1.0)
    assert result["completedTranches"] == 1
    assert float(store.db.execute(
        "SELECT stake FROM trades WHERE strategy='R_CONFIRM_ADD_10'"
    ).fetchone()["stake"]) == pytest.approx(2.0)
    assert store.process_confirmation_add_shadows(confirmed_once)["filledStakeUsdt"] == 0.0

    all_confirmed = {
        **first, "timestamp": "2026-08-02T00:00:03+00:00",
        "up_ask": 0.285, "up_bid": 0.275, "up_book_timestamp_ms": 3,
    }
    result = store.process_confirmation_add_shadows(all_confirmed)
    assert result["filledStakeUsdt"] == pytest.approx(3.0)
    assert float(store.db.execute(
        "SELECT stake FROM trades WHERE strategy='R_CONFIRM_ADD_10'"
    ).fetchone()["stake"]) == pytest.approx(5.0)

    store.settle_market(702, winner="UP", official=True)
    summary = store.confirmation_add_shadow_summary()
    assert summary["paperOnly"] is True
    assert summary["liveOrdersAffected"] is False
    assert summary["overall"]["officialSamples"] == 1
    assert summary["overall"]["pnlUsdt"] > 0


def test_realtime_dashboard_state_uses_in_memory_snapshots(monkeypatch):
    now_ms = int(time.time() * 1000)
    collector = SimpleNamespace(
        market=collector_market(
            topic_id=11,
            market_id=22,
            start_ms=now_ms - 10_000,
            end_ms=now_ms + 5_000,
        ),
        latest_snapshot=snapshot(market_id=22, seconds_left=99.0),
        status="LIVE",
        error=None,
        updated_at="2026-07-22T00:00:00+00:00",
        interval=1.0,
        prediction=SimpleNamespace(
            _time_offset_ms=0.0,
            last_rate_limits={"x-mbx-used-weight-1m": "7"},
        ),
    )

    class StateStub:
        def __init__(self, value):
            self.value = value

        def state(self):
            return dict(self.value)

    observer_arguments = {}

    class ObserverStub:
        def state(self, **kwargs):
            observer_arguments.update(kwargs)
            return {"status": "LIVE", "currentRound": {"marketId": 22}}

    monkeypatch.setattr(server_module, "COLLECTOR", collector)
    monkeypatch.setattr(
        server_module,
        "MICROSTRUCTURE",
        StateStub({"status": "LIVE", "metrics": {"spotPrice": 65000.0}}),
    )
    monkeypatch.setattr(
        server_module,
        "M_REALTIME",
        StateStub(
            {
                "status": "LIVE",
                "m01oMinObserverSamples": 9,
                "m01oMinCurrentRangeScore": 3,
            }
        ),
    )
    monkeypatch.setattr(server_module, "MARKET_OBSERVER", ObserverStub())

    result = server_module.realtime_dashboard_state()

    assert result["connection"]["status"] == "LIVE"
    assert result["latest"]["market_id"] == 22
    assert 3.0 <= result["latest"]["seconds_left"] <= 5.0
    assert result["microstructure"]["metrics"]["spotPrice"] == 65000.0
    assert result["mRealtime"]["status"] == "LIVE"
    assert result["marketObserver"]["currentRound"]["marketId"] == 22
    assert observer_arguments == {
        "m01o_min_settled_samples": 9,
        "m01o_min_current_range_score": 3,
    }


def health_micro(
    *,
    mapping: str,
    market_id: int = 22,
    orientation_timed_out: bool = False,
    micro_status: str = "LIVE",
) -> dict:
    return {
        "status": micro_status,
        "streams": {
            "spot_trade": {"status": "LIVE"},
            "spot_book": {"status": "LIVE"},
            "futures": {"status": "LIVE"},
            "prediction": {
                "status": "LIVE",
                "bookMapping": mapping,
                "marketId": market_id,
                "orientationTimedOut": orientation_timed_out,
                "orientationFailureReason": (
                    "ORIENTATION_TIMEOUT"
                    if orientation_timed_out
                    else "AWAITING_SECOND_CONFIRMATION"
                ),
                "bookVersionAgeMs": 55_000.0,
                "localReceiptAgeMs": 250.0,
            },
        },
        "storage": {"writerStatus": "RUNNING"},
    }


def health_m_realtime(
    *,
    market_id: int = 22,
    book_age_ms: float | None = 250.0,
    data_source: str | None = None,
    orientation: str | None = None,
):
    payload = {
        "status": "LIVE",
        "marketId": market_id,
        "marketDataIntegrityOk": True,
        "droppedEvents": 0,
        "error": None,
        "predictionBookAgeMs": book_age_ms,
    }
    if data_source is not None:
        payload["predictionDataSource"] = data_source
    if orientation is not None:
        payload["predictionOrientation"] = orientation
    return payload


def test_health_is_false_when_prediction_transport_live_but_unverified():
    result = server_module.build_health_payload(
        collector_status="LIVE",
        micro=health_micro(mapping="UNVERIFIED"),
        m_realtime=health_m_realtime(book_age_ms=None),
    )
    assert result["ok"] is False
    assert result["streams"]["prediction"] == "LIVE"
    assert result["predictionOrientationHealthy"] is False
    assert result["predictionOrientationStatus"] == "PENDING"


def test_health_is_false_when_prediction_and_m_realtime_markets_differ():
    result = server_module.build_health_payload(
        collector_status="LIVE",
        micro=health_micro(mapping="DIRECT_UP_VERIFIED", market_id=22),
        m_realtime=health_m_realtime(market_id=23),
    )
    assert result["ok"] is False
    assert result["predictionOrientationStatus"] == "DEGRADED"
    assert result["predictionOrientationReason"] == "PREDICTION_MARKET_ID_MISMATCH"


def test_health_is_true_for_verified_fresh_matching_prediction_book():
    result = server_module.build_health_payload(
        collector_status="LIVE",
        micro=health_micro(mapping="DIRECT_UP_VERIFIED"),
        m_realtime=health_m_realtime(),
    )
    assert result["ok"] is True
    assert result["predictionOrientationHealthy"] is True
    assert result["predictionBookAgeMs"] == pytest.approx(250.0)
    assert result["predictionBookVersionAgeMs"] == pytest.approx(55_000.0)
    assert result["predictionLocalReceiptAgeMs"] == pytest.approx(250.0)


def test_health_uses_fresh_direct_rest_prediction_when_wss_content_is_stale():
    micro = health_micro(
        mapping="UNVERIFIED",
        micro_status="PARTIAL",
    )
    micro["streams"]["prediction"].update(
        {
            "status": "CONNECTING",
            "orientationFailureReason": "STALE_BOOK_VERSION",
        }
    )
    result = server_module.build_health_payload(
        collector_status="LIVE",
        micro=micro,
        m_realtime=health_m_realtime(
            data_source="dual_token_rest",
            orientation="DIRECT_UP_VERIFIED",
        ),
    )

    assert result["ok"] is True
    assert result["streams"]["prediction"] == "CONNECTING"
    assert result["predictionDataSource"] == "dual_token_rest"
    assert result["effectivePredictionBookMapping"] == "DIRECT_UP_VERIFIED"
    assert result["predictionOrientationHealthy"] is True


def test_health_is_false_for_verified_but_stale_prediction_book():
    result = server_module.build_health_payload(
        collector_status="LIVE",
        micro=health_micro(mapping="DIRECT_UP_VERIFIED"),
        m_realtime=health_m_realtime(book_age_ms=10_001.0),
    )
    assert result["ok"] is False
    assert result["predictionOrientationStatus"] == "DEGRADED"
    assert result["predictionOrientationReason"] == "PREDICTION_BOOK_STALE"


def test_health_reports_rollover_confirmation_as_pending_not_healthy():
    result = server_module.build_health_payload(
        collector_status="LIVE",
        micro=health_micro(
            mapping="DIRECT_CANDIDATE",
            micro_status="DEGRADED",
        ),
        m_realtime=health_m_realtime(book_age_ms=None),
    )
    assert result["ok"] is False
    assert result["predictionBookMapping"] == "DIRECT_CANDIDATE"
    assert result["predictionOrientationStatus"] == "PENDING"
    assert result["predictionOrientationReason"] == "AWAITING_SECOND_CONFIRMATION"


def test_health_reports_orientation_timeout_as_degraded():
    result = server_module.build_health_payload(
        collector_status="LIVE",
        micro=health_micro(
            mapping="UNVERIFIED",
            orientation_timed_out=True,
            micro_status="DEGRADED",
        ),
        m_realtime=health_m_realtime(book_age_ms=None),
    )
    assert result["ok"] is False
    assert result["predictionOrientationStatus"] == "DEGRADED"
    assert result["predictionOrientationReason"] == "ORIENTATION_TIMEOUT"


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:4310",
        "http://127.0.0.1:4310",
        "http://192.168.1.25:4310",
        "http://10.0.0.8:4310",
        "http://[fd00::25]:4310",
    ],
)
def test_dashboard_cors_allows_loopback_and_private_lan(origin: str):
    assert allowed_dashboard_origin(origin)


@pytest.mark.parametrize(
    "origin",
    [
        "https://192.168.1.25:4310",
        "http://192.168.1.25:9999",
        "http://8.8.8.8:4310",
        "not-an-origin",
    ],
)
def test_dashboard_cors_rejects_public_or_malformed_origins(origin: str):
    assert not allowed_dashboard_origin(origin)


@pytest.mark.parametrize(
    ("client_host", "origin", "api_host"),
    [
        ("127.0.0.1", "", "127.0.0.1:8766"),
        ("::1", "", "[::1]:8766"),
        ("192.168.1.88", "http://192.168.1.25:4310", "192.168.1.25:8766"),
        ("10.0.0.8", "http://10.0.0.4:4310", "10.0.0.4:8766"),
        ("fd00::88", "http://[fd00::25]:4310", "[fd00::25]:8766"),
    ],
)
def test_live_rule_writes_allow_loopback_or_matching_private_lan_dashboard(
    client_host: str, origin: str, api_host: str
):
    assert allowed_live_rules_request(client_host, origin, api_host)


@pytest.mark.parametrize(
    ("client_host", "origin", "api_host"),
    [
        ("8.8.8.8", "http://192.168.1.25:4310", "192.168.1.25:8766"),
        ("192.168.1.88", "https://192.168.1.25:4310", "192.168.1.25:8766"),
        ("192.168.1.88", "http://192.168.1.25:9999", "192.168.1.25:8766"),
        ("192.168.1.88", "http://8.8.8.8:4310", "192.168.1.25:8766"),
        ("192.168.1.88", "http://192.168.1.99:4310", "192.168.1.25:8766"),
        ("192.168.1.88", "", "192.168.1.25:8766"),
        ("not-an-ip", "http://192.168.1.25:4310", "192.168.1.25:8766"),
    ],
)
def test_live_rule_writes_reject_public_or_non_dashboard_requests(
    client_host: str, origin: str, api_host: str
):
    assert not allowed_live_rules_request(client_host, origin, api_host)


@pytest.mark.parametrize(
    ("client_host", "origin", "api_host"),
    [
        ("127.0.0.1", "", "127.0.0.1:8766"),
        ("::1", "", "[::1]:8766"),
        ("192.168.1.88", "http://192.168.1.25:4310", "192.168.1.25:8766"),
        ("10.0.0.8", "http://10.0.0.4:4310", "10.0.0.4:8766"),
        ("fd00::88", "http://[fd00::25]:4310", "[fd00::25]:8766"),
    ],
)
def test_manual_sell_allows_confirmed_loopback_or_matching_private_lan_dashboard(
    client_host: str, origin: str, api_host: str
):
    assert allowed_manual_sell_request(
        client_host, origin, api_host, "confirmed"
    )


@pytest.mark.parametrize(
    ("client_host", "origin", "api_host", "confirmation"),
    [
        ("192.168.1.88", "http://192.168.1.25:4310", "192.168.1.25:8766", ""),
        ("192.168.1.88", "http://192.168.1.25:4310", "192.168.1.25:8766", "no"),
        ("192.168.1.88", "http://192.168.1.25:4310", "192.168.1.25:9999", "confirmed"),
        ("192.168.1.88", "http://192.168.1.99:4310", "192.168.1.25:8766", "confirmed"),
        ("8.8.8.8", "http://192.168.1.25:4310", "192.168.1.25:8766", "confirmed"),
    ],
)
def test_manual_sell_rejects_missing_confirmation_or_untrusted_source(
    client_host: str, origin: str, api_host: str, confirmation: str
):
    assert not allowed_manual_sell_request(
        client_host, origin, api_host, confirmation
    )


def test_collector_rollover_publishes_new_market_while_old_rest_is_blocked(
    tmp_path: Path,
):
    old_market = collector_market(
        topic_id=101, market_id=201, start_ms=0, end_ms=2_000
    )
    new_market = collector_market(
        topic_id=102, market_id=202, start_ms=2_000, end_ms=500_000
    )

    class PredictionStub:
        timeout = 5.0
        retry_after_seconds = None
        last_rate_limits = {}

        def __init__(self):
            self.now_ms = 1_000

        def server_timestamp_ms(self):
            return self.now_ms

        def find_market_summary(self, *_args, **_kwargs):
            return new_market

        def orderbook(self, _market_id, _token_id):
            return {
                "asks": [["0.52", "10"]],
                "bids": [["0.51", "10"]],
                "updateTimestampMs": self.now_ms,
            }

    class BlockingSpot:
        def __init__(self):
            self.started = threading.Event()
            self.release = threading.Event()

        def price(self, _symbol):
            self.started.set()
            if not self.release.wait(2.0):
                raise TimeoutError("test did not release the old Spot request")
            return 65_001.0

    store = Store(tmp_path / "collector-rollover.db")
    prediction = PredictionStub()
    spot = BlockingSpot()
    collector = Collector(store, None, None)
    collector.prediction = prediction
    collector.spot = spot
    collector.market = old_market
    collector.interval = 0.01
    collector.start()
    try:
        assert spot.started.wait(1.0), "old-market REST cycle did not start"
        prediction.now_ms = 2_001
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with collector.market_lock:
                current = collector.market
            if current and int(current["marketTopicId"]) == 102:
                break
            time.sleep(0.005)
        else:
            pytest.fail("rollover supervisor did not publish the new market")

        # The worker is still blocked on the captured old-market request, so
        # publication above proves discovery is independent of that REST call.
        assert not spot.release.is_set()
    finally:
        collector.stop_event.set()
        spot.release.set()
        collector.join(timeout=2.0)
        if collector.market_watch_thread is not None:
            collector.market_watch_thread.join(timeout=1.0)

    assert not collector.is_alive()
    assert store.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert collector.latest_snapshot is None


def seed_spot_history(
    store: Store,
    *,
    market_id: int,
    end_seconds_left: float,
    total_log_move: float,
    final_overrides: dict | None = None,
):
    """Write 121 irregular-price observations with an exact start/end log move."""
    started_at = datetime(2026, 7, 16, tzinfo=timezone.utc)
    count = 121
    final = None
    for index in range(count):
        fraction = index / (count - 1)
        elapsed = (295.0 - end_seconds_left) * fraction
        log_price = total_log_move * fraction + 0.00001 * math.sin(2 * math.pi * index / 5)
        row = snapshot(
            market_id=market_id,
            timestamp=(started_at + timedelta(seconds=elapsed)).isoformat(),
            seconds_left=295.0 - elapsed,
            spot_price=65_000 * math.exp(log_price),
            up_ask=0.51,
            up_bid=0.50,
            down_ask=0.51,
            down_bid=0.50,
            up_ask_size=100.0,
            down_ask_size=100.0,
            book_skew_ms=100.0,
            up_book_timestamp_ms=1_000_000.0,
            down_book_timestamp_ms=1_000_100.0,
            book_age_ms=250.0,
        )
        if index == count - 1 and final_overrides:
            row.update(final_overrides)
        store.observe(row)
        final = row
    return final


def h_snapshot(
    *,
    market_id: int,
    seconds_left: float,
    up_ask: float,
    down_ask: float,
    **overrides,
):
    row = snapshot(
        market_id=market_id,
        topic_id=10_000 + market_id,
        seconds_left=seconds_left,
        up_ask=up_ask,
        up_bid=max(0.0, up_ask - 0.01),
        down_ask=down_ask,
        down_bid=max(0.0, down_ask - 0.01),
        up_ask_size=5_000.0,
        down_ask_size=5_000.0,
        book_skew_ms=100.0,
        book_age_ms=250.0,
    )
    row.update(overrides)
    return row


def i_snapshot(*, market_id: int, seconds_left: float = 150.0, **overrides):
    row = snapshot(
        market_id=market_id,
        topic_id=20_000 + market_id,
        seconds_left=seconds_left,
        up_ask=0.01,
        up_bid=0.0,
        down_ask=0.99,
        down_bid=0.98,
        up_ask_size=100.0,
        down_ask_size=100.0,
        book_skew_ms=100.0,
        book_age_ms=250.0,
    )
    row.update(overrides)
    return row


def j_snapshot(*, market_id: int, seconds_left: float = 60.0, **overrides):
    row = snapshot(
        market_id=market_id,
        topic_id=30_000 + market_id,
        seconds_left=seconds_left,
        up_ask=0.15,
        up_bid=0.14,
        down_ask=0.85,
        down_bid=0.84,
        up_ask_size=100.0,
        down_ask_size=100.0,
        book_skew_ms=100.0,
        book_age_ms=250.0,
    )
    row.update(overrides)
    return row


def settle_h_reversal(store: Store, market_id: int, leader: str = "UP") -> None:
    if leader == "UP":
        row = h_snapshot(
            market_id=market_id, seconds_left=5.5, up_ask=0.91, down_ask=0.10
        )
        winner = "DOWN"
    else:
        row = h_snapshot(
            market_id=market_id, seconds_left=5.5, up_ask=0.10, down_ask=0.91
        )
        winner = "UP"
    store.observe(row)
    store.settle_market(market_id, winner=winner, official=True)


def arm_strategy_h(store: Store, first_market_id: int) -> None:
    settle_h_reversal(store, first_market_id, "UP")
    settle_h_reversal(store, first_market_id + 1, "DOWN")
    assert store.strategy_h_state()["mode"] == "ARMED"


def test_strategy_a_enters_once_and_exits_at_target(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    row = snapshot()
    store.observe(row)
    store.maybe_enter(row, fee_bps=200)
    store.maybe_enter(row, fee_bps=200)
    trades = store.db.execute("SELECT * FROM trades WHERE strategy='A'").fetchall()
    assert len(trades) == 1
    assert trades[0]["strategy"] == "A"
    assert trades[0]["side"] == "UP"
    store.process_intraday_targets(2, up_bid=0.40, down_bid=0.60, fee_bps=200)
    closed = store.db.execute("SELECT * FROM trades WHERE strategy='A'").fetchone()
    assert closed["status"] == "TARGET_FILLED"
    assert closed["pnl"] > 0


def test_strategy_b_only_enters_in_last_window_and_settles(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    too_early = snapshot(
        market_id=3, seconds_left=21,
        up_ask=0.92, up_bid=0.91, down_ask=0.08, down_bid=0.07,
    )
    store.maybe_enter(too_early, fee_bps=200)
    assert store.db.execute("SELECT COUNT(*) FROM trades WHERE strategy='B'").fetchone()[0] == 0
    late = snapshot(
        market_id=3, seconds_left=19,
        up_ask=0.92, up_bid=0.91, down_ask=0.08, down_bid=0.07,
    )
    store.maybe_enter(late, fee_bps=200)
    store.settle_market(3, winner="UP", official=True)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='B'").fetchone()
    assert trade["status"] == "SETTLED_WIN"
    assert trade["pnl"] > 0


def test_strategy_b_rejects_price_above_configured_range(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    too_expensive = snapshot(market_id=4, seconds_left=10, up_ask=0.96, down_ask=0.04)
    store.maybe_enter(too_expensive, fee_bps=200)
    count = store.db.execute("SELECT COUNT(*) FROM trades WHERE strategy='B'").fetchone()[0]
    assert count == 0


def test_strategy_c_targets_twice_the_actual_entry(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    row = snapshot(market_id=5, seconds_left=250, up_ask=0.15, down_ask=0.85)
    store.maybe_enter(row, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='C'").fetchone()
    assert trade["entry_price"] == 0.15
    assert trade["target_price"] == 0.30
    store.process_intraday_targets(5, up_bid=0.30, down_bid=0.70, fee_bps=200)
    closed = store.db.execute("SELECT * FROM trades WHERE strategy='C'").fetchone()
    assert closed["status"] == "TARGET_FILLED"
    assert closed["pnl"] > 0


def test_strategy_d_pairs_opposite_side_below_net_cost_cap(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    first = snapshot(market_id=6, seconds_left=250, up_ask=0.25, up_bid=0.24, down_ask=0.75, down_bid=0.74)
    store.maybe_enter(first, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='D'").fetchone()
    assert trade["side"] == "UP"
    assert trade["status"] == "OPEN"

    later = snapshot(market_id=6, seconds_left=180, up_ask=0.52, up_bid=0.51, down_ask=0.50, down_bid=0.49)
    store.process_time_arbitrage(later, fee_bps=200)
    paired = store.db.execute("SELECT * FROM trades WHERE strategy='D'").fetchone()
    assert paired["status"] == "PAIRED_LOCKED"
    assert paired["exit_price"] == 0.50
    assert paired["pnl"] > 0


def test_pair_arb_records_independent_fee_aware_ledgers(tmp_path: Path):
    store = Store(tmp_path / "pair-arb.db")
    context = {
        "trigger_source": "dual_token_rest",
        "pair_book_source": "independent_outcome_books",
        "execution_eligible": True,
        "market_data_integrity_ok": True,
        "trigger_event_sequence": "dual-rest:6001:1:1",
    }
    strong = snapshot(
        market_id=6001,
        up_ask=0.47,
        down_ask=0.49,
        up_ask_size=20.0,
        down_ask_size=8.0,
        book_skew_ms=12.0,
        book_age_ms=30.0,
    )
    store.process_pair_arb_snapshot(strong, 200, realtime_context=context)
    store.process_pair_arb_snapshot(strong, 200, realtime_context=context)

    rows = store.db.execute(
        "SELECT * FROM strategy_pair_arb_trades ORDER BY strategy"
    ).fetchall()
    assert [row["strategy"] for row in rows] == [
        "PAIR_ARB_010", "PAIR_ARB_020", "PAIR_ARB_QC_015",
        "PAIR_ARB_RISK_020",
    ]
    assert all(float(row["shares"]) == pytest.approx(8.0) for row in rows)
    assert all(float(row["locked_pnl"]) > 0 for row in rows)
    assert all(
        float(row["stressed_pnl_005"])
        == pytest.approx(float(row["locked_pnl"]) - float(row["shares"]) * .01)
        for row in rows
    )

    narrow = snapshot(
        market_id=6002,
        up_ask=0.48,
        down_ask=0.49,
        up_ask_size=20.0,
        down_ask_size=20.0,
        book_skew_ms=10.0,
        book_age_ms=20.0,
    )
    store.process_pair_arb_snapshot(narrow, 200, realtime_context=context)
    second_market = store.db.execute(
        "SELECT strategy FROM strategy_pair_arb_trades WHERE market_id=6002"
    ).fetchall()
    assert [row["strategy"] for row in second_market] == [
        "PAIR_ARB_010", "PAIR_ARB_RISK_020"
    ]

    state = store.pair_arb_state()
    assert state["paperOnly"] is True
    assert state["atomicExecutionAssumed"] is False
    assert state["summaries"]["PAIR_ARB_010"]["trades"] == 2
    assert state["summaries"]["PAIR_ARB_QC_015"]["trades"] == 1
    assert state["summaries"]["PAIR_ARB_020"]["trades"] == 1
    assert state["summaries"]["PAIR_ARB_RISK_020"]["trades"] == 2
    assert state["summaries"]["PAIR_ARB_010"]["maxDrawdown"] == 0
    assert state["fillModel"] == "dual_token_orderbook_depth_vwap_partial_fill_v1"
    assert state["diagnostics"]["source"] == "independent_outcome_books"
    assert state["diagnostics"]["evaluations"] == 3
    assert state["diagnostics"]["marketsEvaluated"] == 2
    assert state["diagnostics"]["eligibleSnapshots"]["PAIR_ARB_010"] == 3
    assert state["diagnostics"]["eligibleSnapshots"]["PAIR_ARB_QC_015"] == 2
    assert state["diagnostics"]["eligibleSnapshots"]["PAIR_ARB_020"] == 2


def test_pair_arb_walks_visible_levels_and_stops_when_edge_disappears(
    tmp_path: Path,
):
    store = Store(tmp_path / "pair-arb-depth.db")
    row = snapshot(
        market_id=6010,
        up_ask=0.47,
        down_ask=0.49,
        up_ask_size=2.0,
        down_ask_size=3.0,
        book_skew_ms=10.0,
        book_age_ms=20.0,
        _up_asks=[[0.47, 2.0], [0.48, 4.0]],
        _down_asks=[[0.49, 3.0], [0.50, 10.0]],
    )
    store.process_pair_arb_snapshot(
        row,
        200,
        realtime_context={
            "trigger_source": "dual_token_rest",
            "pair_book_source": "independent_outcome_books",
            "execution_eligible": True,
            "market_data_integrity_ok": True,
        },
    )

    rows = {
        item["strategy"]: item
        for item in store.db.execute(
            "SELECT * FROM strategy_pair_arb_trades WHERE market_id=6010"
        ).fetchall()
    }
    assert float(rows["PAIR_ARB_020"]["shares"]) == pytest.approx(2.0)
    assert float(rows["PAIR_ARB_010"]["shares"]) == pytest.approx(3.0)
    assert int(rows["PAIR_ARB_010"]["partial_fill"]) == 1
    assert float(rows["PAIR_ARB_010"]["fill_ratio"]) < 1
    assert float(rows["PAIR_ARB_010"]["up_fill_vwap"]) == pytest.approx(
        (0.47 * 2 + 0.48) / 3
    )


def test_pair_arb_risk_variant_accepts_bounded_negative_edge(tmp_path: Path):
    store = Store(tmp_path / "pair-arb-risk.db")
    store.process_pair_arb_snapshot(
        snapshot(
            market_id=6011,
            up_ask=0.50,
            down_ask=0.50,
            up_ask_size=10.0,
            down_ask_size=10.0,
            book_skew_ms=10.0,
            book_age_ms=20.0,
        ),
        200,
        realtime_context={
            "trigger_source": "dual_token_rest",
            "pair_book_source": "independent_outcome_books",
            "execution_eligible": True,
            "market_data_integrity_ok": True,
        },
    )

    rows = store.db.execute(
        "SELECT strategy, net_edge_per_share FROM strategy_pair_arb_trades"
    ).fetchall()
    assert [row["strategy"] for row in rows] == ["PAIR_ARB_RISK_020"]
    assert float(rows[0]["net_edge_per_share"]) == pytest.approx(-0.02)


def test_pair_arb_rejects_single_book_complement_feed(tmp_path: Path):
    store = Store(tmp_path / "pair-arb-source.db")
    synthetic = snapshot(
        market_id=6003,
        up_ask=0.47,
        down_ask=0.49,
        up_ask_size=20.0,
        down_ask_size=20.0,
        book_skew_ms=0.0,
        book_age_ms=10.0,
    )
    store.process_pair_arb_snapshot(
        synthetic,
        200,
        realtime_context={
            "trigger_source": "prediction",
            "pair_book_source": "derived_complement",
            "execution_eligible": True,
            "market_data_integrity_ok": True,
        },
    )

    assert store.db.execute(
        "SELECT COUNT(*) FROM strategy_pair_arb_trades"
    ).fetchone()[0] == 0
    assert store.pair_arb_state()["diagnostics"]["evaluations"] == 0


def test_pair_arb_exposes_rejection_diagnostics(tmp_path: Path):
    store = Store(tmp_path / "pair-arb-diagnostics.db")
    context = {
        "trigger_source": "dual_token_rest",
        "pair_book_source": "independent_outcome_books",
        "execution_eligible": True,
        "market_data_integrity_ok": True,
    }
    stale = snapshot(
        market_id=6004,
        up_ask=0.48,
        down_ask=0.49,
        up_ask_size=20.0,
        down_ask_size=20.0,
        book_skew_ms=10.0,
        book_age_ms=2501.0,
    )
    no_edge = snapshot(
        market_id=6005,
        up_ask=0.50,
        down_ask=0.51,
        up_ask_size=20.0,
        down_ask_size=20.0,
        book_skew_ms=10.0,
        book_age_ms=20.0,
    )
    store.process_pair_arb_snapshot(stale, 200, realtime_context=context)
    store.process_pair_arb_snapshot(no_edge, 200, realtime_context=context)

    diagnostics = store.pair_arb_state()["diagnostics"]
    assert diagnostics["evaluations"] == 2
    assert diagnostics["validBookEvaluations"] == 1
    assert diagnostics["rejectionCounts"]["bookAgeExceeded"] == 1
    assert diagnostics["rejectionCounts"]["edgeBelow010"] == 1


def test_strategy_d_does_not_pair_above_net_cost_cap(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    first = snapshot(market_id=7, seconds_left=250, up_ask=0.25, down_ask=0.75)
    store.maybe_enter(first, fee_bps=200)
    expensive_pair = snapshot(market_id=7, seconds_left=180, up_ask=0.34, down_ask=0.70)
    store.process_time_arbitrage(expensive_pair, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='D'").fetchone()
    assert trade["status"] == "OPEN"


def test_strategy_d_exits_first_leg_after_timeout(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    first = snapshot(market_id=8, seconds_left=250, up_ask=0.25, up_bid=0.24, down_ask=0.75)
    store.maybe_enter(first, fee_bps=200)
    old = (datetime.now(timezone.utc) - timedelta(seconds=121)).isoformat()
    store.db.execute("UPDATE trades SET opened_at=? WHERE strategy='D'", (old,))
    store.db.commit()
    no_pair = snapshot(market_id=8, seconds_left=100, up_ask=0.26, up_bid=0.20, down_ask=0.80, down_bid=0.79)
    store.process_time_arbitrage(no_pair, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='D'").fetchone()
    assert trade["status"] == "TIMEOUT_EXIT"
    assert trade["exit_price"] == 0.20
    assert trade["pnl"] < 0


def test_strategy_e_requires_spot_and_market_direction_confirmation(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    baseline = snapshot(
        market_id=20, seconds_left=295, spot_price=65_000,
        up_ask=0.51, up_bid=0.49, down_ask=0.51, down_bid=0.49,
    )
    store.observe(baseline)
    confirmed = snapshot(
        market_id=20, timestamp="2026-07-16T00:04:00+00:00", seconds_left=60,
        spot_price=65_000 * 1.0004,
        up_ask=0.70, up_bid=0.68, down_ask=0.32, down_bid=0.30,
    )
    store.observe(confirmed)
    store.maybe_enter(confirmed, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='E'").fetchone()
    assert trade["side"] == "UP"
    assert trade["entry_price"] == 0.70
    assert "same-source move=+4.00bps" in trade["note"]


def test_strategy_e_rejects_prediction_market_disagreement(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    baseline = snapshot(market_id=21, seconds_left=295, spot_price=65_000)
    store.observe(baseline)
    disagreement = snapshot(
        market_id=21, timestamp="2026-07-16T00:04:00+00:00", seconds_left=60,
        spot_price=65_000 * 1.0004,
        up_ask=0.45, up_bid=0.43, down_ask=0.57, down_bid=0.55,
    )
    store.observe(disagreement)
    store.maybe_enter(disagreement, fee_bps=200)
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='E'"
    ).fetchone()[0] == 0


def test_strategy_e_rejects_insufficient_visible_depth(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    store.observe(snapshot(market_id=23, seconds_left=295, spot_price=65_000))
    shallow = snapshot(
        market_id=23, timestamp="2026-07-16T00:04:00+00:00", seconds_left=60,
        spot_price=65_000 * 1.0004,
        up_ask=0.70, up_bid=0.68, down_ask=0.32, down_bid=0.30,
        up_ask_size=1.0,
    )
    store.observe(shallow)
    store.maybe_enter(shallow, fee_bps=200)
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='E'"
    ).fetchone()[0] == 0


def test_strategy_f_is_stricter_than_strategy_d(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    first = snapshot(
        market_id=22, seconds_left=250,
        up_ask=0.25, up_bid=0.24, down_ask=0.75, down_bid=0.74,
    )
    store.maybe_enter(first, fee_bps=200)
    f_trade = store.db.execute("SELECT * FROM trades WHERE strategy='F'").fetchone()
    assert f_trade["stake"] == 5.0

    d_only_pair = snapshot(
        market_id=22, seconds_left=180,
        up_ask=0.34, up_bid=0.33, down_ask=0.68, down_bid=0.67,
    )
    store.process_time_arbitrage(d_only_pair, fee_bps=200)
    assert store.db.execute(
        "SELECT status FROM trades WHERE strategy='D'"
    ).fetchone()[0] == "PAIRED_LOCKED"
    assert store.db.execute(
        "SELECT status FROM trades WHERE strategy='F'"
    ).fetchone()[0] == "OPEN"

    strict_pair = snapshot(
        market_id=22, seconds_left=170,
        up_ask=0.39, up_bid=0.38, down_ask=0.63, down_bid=0.62,
    )
    store.process_time_arbitrage(strict_pair, fee_bps=200)
    paired = store.db.execute("SELECT * FROM trades WHERE strategy='F'").fetchone()
    assert paired["status"] == "PAIRED_LOCKED"
    assert paired["pnl"] > 0


def test_strategy_f_waits_when_second_leg_depth_is_too_small(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    first = snapshot(market_id=24, seconds_left=250, up_ask=0.25, down_ask=0.75)
    store.maybe_enter(first, fee_bps=200)
    shallow_pair = snapshot(
        market_id=24, seconds_left=180,
        up_ask=0.39, down_ask=0.63, down_ask_size=1.0,
    )
    store.process_time_arbitrage(shallow_pair, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='F'").fetchone()
    assert trade["status"] == "OPEN"


def test_fees_use_binary_price_formula_and_old_database_is_repaired(tmp_path: Path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT NOT NULL,
            topic_id INTEGER NOT NULL, market_id INTEGER NOT NULL, side TEXT NOT NULL,
            status TEXT NOT NULL, entry_price REAL NOT NULL, target_price REAL,
            exit_price REAL, stake REAL NOT NULL, shares REAL NOT NULL,
            fees REAL NOT NULL DEFAULT 0, pnl REAL, opened_at TEXT NOT NULL,
            closed_at TEXT, note TEXT
        )"""
    )
    db.execute(
        """INSERT INTO trades(
            strategy, topic_id, market_id, side, status, entry_price, exit_price,
            stake, shares, fees, pnl, opened_at
        ) VALUES ('B', 1, 99, 'UP', 'SETTLED_WIN', .9, 1, 9, 10, .18, .82, ?)""",
        (datetime.now(timezone.utc).isoformat(),),
    )
    db.commit()
    db.close()

    store = Store(path)
    trade = store.db.execute("SELECT * FROM trades").fetchone()
    assert trade["fee_rate_bps"] == 200
    assert trade["fees"] == pytest.approx(0.02)
    assert trade["pnl"] == pytest.approx(0.98)


def test_empty_strategy_summaries_are_numeric_zeroes(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    collector = SimpleNamespace(
        status="LIVE", error=None, updated_at=None, interval=1.0,
        prediction=SimpleNamespace(last_rate_limits={}),
    )
    summary = store.dashboard(collector)["summaries"]["E"]
    assert summary == {
        "trades": 0, "open": 0, "wins": 0, "losses": 0,
        "realized_pnl": 0, "resetAt": None, "cutoffTradeId": None,
        "carriedOpen": 0, "totalOpen": 0,
    }


def test_futures_lead_observer_trade_page_reads_all_observer_history(
    tmp_path: Path,
):
    path = tmp_path / "sim.db"
    store = Store(path)
    observer_strategies = (
        "R_FUTURES_LEAD_OBSERVER_F1",
        "R_FUTURES_LEAD_OBSERVER_V2",
        "R_FUTURES_LEAD_OBSERVER_V3",
        "R_FUTURES_LEAD_OBSERVER_V4",
        "R_FUTURES_LEAD_OBSERVER_V6",
    )
    for index in range(13):
        store.open_trade(
            strategy=observer_strategies[index % len(observer_strategies)],
            topic_id=100 + index,
            market_id=200 + index,
            side="UP" if index % 2 == 0 else "DOWN",
            entry=0.5,
            target=None,
            stake=5.0,
            fee_rate_bps=200,
            note="observer page fixture",
        )
    combination_strategies = (
        "R_OFI_OBSERVER_V3",
        "R_MICROPRICE_OBSERVER_V3",
        "R_MICROPRICE_OBSERVER_V6",
        "R_CALIBRATED_VALUE_OBSERVER_V6",
    )
    for offset, strategy in enumerate((*combination_strategies, "M0")):
        store.open_trade(
            strategy=strategy,
            topic_id=999 + offset,
            market_id=999 + offset,
            side="UP",
            entry=0.5,
            target=None,
            stake=5.0,
            fee_rate_bps=200,
            note="observer combination or excluded fixture",
        )

    expected_ids = [
        int(row["id"])
        for row in store.db.execute(
            """SELECT id FROM trades
               WHERE strategy IN (
                   'R_FUTURES_LEAD_OBSERVER_F1',
                   'R_FUTURES_LEAD_OBSERVER_V2',
                   'R_FUTURES_LEAD_OBSERVER_V3',
                   'R_FUTURES_LEAD_OBSERVER_V4',
                   'R_FUTURES_LEAD_OBSERVER_V6',
                   'R_OFI_OBSERVER_V3',
                   'R_MICROPRICE_OBSERVER_V3',
                   'R_MICROPRICE_OBSERVER_V6',
                   'R_CALIBRATED_VALUE_OBSERVER_V6'
               )
               ORDER BY id DESC"""
        ).fetchall()
    ]
    reader = Store.open_read_only(path)

    first = reader.futures_lead_observer_trade_page(page=1)
    assert first["storage"] == "SQLITE_FULL_HISTORY"
    assert first["scope"] == "OBSERVER_STRATEGIES"
    assert first["total"] == 17
    assert first["pageSize"] == 10
    assert first["totalPages"] == 2
    assert [row["id"] for row in first["trades"]] == expected_ids[:10]

    second = reader.futures_lead_observer_trade_page(page=99)
    assert second["page"] == 2
    assert [row["id"] for row in second["trades"]] == expected_ids[10:]
    assert {
        row["strategy"] for row in first["trades"] + second["trades"]
    } <= set((*observer_strategies, *combination_strategies))


def test_m0_hourly_guard_snapshot_reads_only_required_settlements(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    opened = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
    for index in range(3):
        store.open_trade(
            strategy="M0",
            topic_id=100 + index,
            market_id=200 + index,
            side="UP",
            entry=0.5,
            target=None,
            stake=1.0,
            fee_rate_bps=200,
            note="hourly guard fixture",
        )
    with store.lock:
        rows = store.db.execute(
            "SELECT id FROM trades WHERE strategy='M0' ORDER BY id"
        ).fetchall()
        for index, (row, status) in enumerate(
            zip(rows, ("SETTLED_WIN", "SETTLED_LOSS", "SETTLED_WIN"))
        ):
            store.db.execute(
                "UPDATE trades SET status=?, opened_at=? WHERE id=?",
                (
                    status,
                    (opened + timedelta(minutes=5 * index)).isoformat(),
                    int(row["id"]),
                ),
            )
        store.db.commit()

    result = store.m0_hourly_guard_snapshot()
    bucket = result["hours"][16]

    assert result["settledTrades"] == 3
    assert bucket["settledTrades"] == 3
    assert bucket["wins"] == 2
    assert bucket["losses"] == 1
    assert bucket["winRatePct"] == pytest.approx(200 / 3)
    assert bucket["winThenLossCount"] == 1
    assert bucket["winThenLossOpportunities"] == 1
    assert bucket["winThenLossRatePct"] == pytest.approx(100.0)


def test_rolling_volatility_requires_enough_valid_returns_and_applies_floor():
    samples = [(0.0, 100.0), (1.0, 100.01), (3.0, 100.02), (6.0, 100.03)]
    assert rolling_volatility_per_sqrt_second(
        samples, min_return_samples=4, sigma_floor=0.00002
    ) is None
    result = rolling_volatility_per_sqrt_second(
        samples, min_return_samples=3, sigma_floor=0.00002
    )
    assert result is not None
    sigma, return_count, floor_applied = result
    expected = math.sqrt(
        sum(
            math.log(p1 / p0) ** 2
            for (_, p0), (_, p1) in zip(samples, samples[1:])
        ) / 6.0
    )
    assert sigma == pytest.approx(expected)
    assert return_count == 3
    assert floor_applied is False


def test_strategy_e2_enters_on_volatility_normalized_confirmed_momentum(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=30,
        end_seconds_left=60,
        total_log_move=0.002,
        final_overrides={
            "up_ask": 0.70, "up_bid": 0.69, "down_ask": 0.31, "down_bid": 0.30,
            "up_ask_size": 100.0,
        },
    )
    store.maybe_enter(final, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='E2'").fetchone()
    assert trade is not None
    assert trade["side"] == "UP"
    assert trade["entry_price"] == pytest.approx(0.70)
    assert trade["strategy_version"] == "E2_v1_full_interval"
    assert trade["model_sigma"] >= 0.00002
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["move_z"] >= 0.75
    assert diagnostics["return_samples"] >= 60
    assert diagnostics["config"]["strategy_e2_lookback_observations"] == 300
    assert diagnostics["signal_timestamp"] == final["timestamp"]
    assert diagnostics["quoted_ask"] == pytest.approx(0.70)
    assert diagnostics["quoted_bid"] == pytest.approx(0.69)
    assert diagnostics["visible_ask_size"] == pytest.approx(100.0)


@pytest.mark.parametrize(
    "total_move, final_overrides",
    [
        (0.00005, {"up_ask": 0.70, "up_bid": 0.69, "down_ask": 0.31, "up_ask_size": 100.0}),
        (0.002, {"up_ask": 0.49, "up_bid": 0.48, "down_ask": 0.52, "up_ask_size": 100.0}),
        (0.002, {"up_ask": 0.70, "up_bid": 0.67, "down_ask": 0.31, "up_ask_size": 100.0}),
        (0.002, {"up_ask": 0.70, "up_bid": 0.69, "down_ask": 0.31, "up_ask_size": 1.0}),
    ],
)
def test_strategy_e2_rejects_weak_move_disagreement_wide_spread_or_shallow_depth(
    tmp_path: Path, total_move: float, final_overrides: dict
):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=31,
        end_seconds_left=60,
        total_log_move=total_move,
        final_overrides=final_overrides,
    )
    store.maybe_enter(final, fee_bps=200)
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='E2'"
    ).fetchone()[0] == 0


def test_strategy_g_uses_shrunk_probability_and_full_conservative_cost(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=40,
        end_seconds_left=100,
        total_log_move=0.002,
        final_overrides={
            "up_ask": 0.60, "up_bid": 0.59, "down_ask": 0.42, "down_bid": 0.41,
            "up_ask_size": 100.0, "down_ask_size": 100.0, "book_skew_ms": 100.0,
        },
    )
    store.maybe_enter(final, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='G'").fetchone()
    assert trade is not None
    assert trade["side"] == "UP"
    assert trade["strategy_version"] == "G_v1_official_strike"
    diagnostics = json.loads(trade["diagnostics_json"])
    execution_entry = 0.60 * (1 + 50 / 10_000)
    expected_cost = execution_entry + min(execution_entry, 1 - execution_entry) * 0.02
    assert trade["entry_price"] == pytest.approx(execution_entry)
    assert diagnostics["effective_cost"] == pytest.approx(expected_cost)
    assert trade["model_probability"] == pytest.approx(
        0.5 + 0.5 * (diagnostics["q_raw_up"] - 0.5)
    )
    assert trade["model_edge"] == pytest.approx(
        trade["model_probability"] - 0.03 - expected_cost
    )
    assert trade["model_edge"] >= max(0.05, 3 * 0.01)
    assert normal_cdf(diagnostics["z_score"]) == pytest.approx(diagnostics["q_raw_up"])
    assert diagnostics["quoted_ask"] == pytest.approx(0.60)
    assert diagnostics["execution_entry"] == pytest.approx(execution_entry)
    assert diagnostics["slippage_per_share"] == pytest.approx(execution_entry - 0.60)
    assert diagnostics["config"]["strategy_g_probability_shrinkage"] == pytest.approx(0.5)

    shares = trade["shares"]
    entry_fee = shares * min(execution_entry, 1 - execution_entry) * 0.02
    assert trade["fees"] == pytest.approx(entry_fee)
    store.settle_market(40, winner="UP", official=True)
    settled = store.db.execute("SELECT * FROM trades WHERE strategy='G'").fetchone()
    assert settled["pnl"] == pytest.approx(shares - 10.0 - entry_fee)


@pytest.mark.parametrize(
    "final_overrides",
    [
        {"up_ask": 0.60, "up_bid": 0.59, "up_ask_size": 100.0, "book_skew_ms": 501.0},
        {"up_ask": 0.60, "up_bid": 0.59, "up_ask_size": 100.0, "book_skew_ms": 0.0,
         "book_age_ms": 2001.0, "up_book_timestamp_ms": 1000.0,
         "down_book_timestamp_ms": 1000.0},
        {"up_ask": 0.60, "up_bid": 0.61, "up_ask_size": 100.0, "book_skew_ms": 100.0},
        {"up_ask": 0.60, "up_bid": 0.57, "up_ask_size": 100.0, "book_skew_ms": 100.0},
        {"up_ask": 0.60, "up_bid": 0.59, "up_ask_size": 1.0, "book_skew_ms": 100.0},
        {"up_ask": 0.75, "up_bid": 0.74, "up_ask_size": 100.0, "book_skew_ms": 100.0},
    ],
)
def test_strategy_g_rejects_stale_wide_shallow_or_insufficient_edge(
    tmp_path: Path, final_overrides: dict
):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=41,
        end_seconds_left=100,
        total_log_move=0.002,
        final_overrides={
            "down_ask": 0.42, "down_bid": 0.41, "down_ask_size": 100.0,
            **final_overrides,
        },
    )
    store.maybe_enter(final, fee_bps=200)
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='G'"
    ).fetchone()[0] == 0


def test_strategy_g_probability_uses_official_strike_not_first_spot(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=42,
        end_seconds_left=100,
        total_log_move=0.002,
        final_overrides={
            "start_price": 65_200.0,
            "up_ask": 0.42, "up_bid": 0.41, "down_ask": 0.60, "down_bid": 0.59,
            "up_ask_size": 100.0, "down_ask_size": 100.0,
        },
    )
    assert final["spot_price"] > 65_000.0
    assert final["spot_price"] < final["start_price"]
    store.maybe_enter(final, fee_bps=200)
    trade = store.db.execute("SELECT * FROM trades WHERE strategy='G'").fetchone()
    assert trade is not None
    assert trade["side"] == "DOWN"
    diagnostics = json.loads(trade["diagnostics_json"])
    expected_z = math.log(final["spot_price"] / final["start_price"]) / (
        trade["model_sigma"] * math.sqrt(final["seconds_left"] + 2.0)
    )
    assert diagnostics["z_score"] == pytest.approx(expected_z)
    assert diagnostics["official_strike"] == pytest.approx(65_200.0)
    assert diagnostics["first_observed_spot"] == pytest.approx(65_000.0)
    assert diagnostics["initial_basis_bps"] == pytest.approx(
        (65_000.0 / 65_200.0 - 1.0) * 10_000
    )


def test_full_interval_context_keeps_anchor_and_volatility_window_coherent(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store, market_id=43, end_seconds_left=60, total_log_move=0.002
    )
    context = store.spot_model_context(
        final,
        lookback_observations=10,
        min_return_samples=5,
        sigma_floor=0.00002,
        min_baseline_seconds_left=290,
        full_interval=True,
    )
    assert context is not None
    recent = store.db.execute(
        "SELECT timestamp, spot_price FROM observations WHERE market_id=43 "
        "ORDER BY id DESC LIMIT 10"
    ).fetchall()[::-1]
    assert context["anchor"] == pytest.approx(recent[0]["spot_price"])
    expected_elapsed = (
        datetime.fromisoformat(final["timestamp"])
        - datetime.fromisoformat(recent[0]["timestamp"])
    ).total_seconds()
    assert context["elapsed"] == pytest.approx(expected_elapsed)


@pytest.mark.parametrize("key", ["strategy_e2_sigma_floor", "strategy_g_sigma_floor"])
def test_model_config_rejects_zero_sigma_floor(tmp_path: Path, key: str):
    store = Store(tmp_path / "sim.db")
    with pytest.raises(ValueError, match="must be positive"):
        store.update_config({key: 0})


def test_pre_release_e2_lookback_default_is_upgraded_before_first_trade(tmp_path: Path):
    path = tmp_path / "sim.db"
    store = Store(path)
    store.db.execute(
        "UPDATE config SET value=120 WHERE key='strategy_e2_lookback_observations'"
    )
    store.db.commit()
    store.db.close()
    reopened = Store(path)
    assert reopened.config()["strategy_e2_lookback_observations"] == 300


def test_strategy_e2_rejects_crossed_or_stale_books(tmp_path: Path):
    for market_id, overrides in (
        (50, {"up_ask": 0.70, "up_bid": 0.71}),
        (51, {"book_skew_ms": 0.0, "book_age_ms": 2001.0,
              "up_book_timestamp_ms": 1000.0, "down_book_timestamp_ms": 1000.0}),
    ):
        store = Store(tmp_path / f"sim-{market_id}.db")
        final = seed_spot_history(
            store,
            market_id=market_id,
            end_seconds_left=60,
            total_log_move=0.002,
            final_overrides={
                "up_ask": 0.70, "up_bid": 0.69, "down_ask": 0.31, "down_bid": 0.30,
                **overrides,
            },
        )
        store.maybe_enter(final, fee_bps=200)
        assert store.db.execute(
            "SELECT COUNT(*) FROM trades WHERE strategy='E2'"
        ).fetchone()[0] == 0


def test_model_metadata_columns_are_migrated_and_new_summaries_are_zero(tmp_path: Path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT NOT NULL,
            topic_id INTEGER NOT NULL, market_id INTEGER NOT NULL, side TEXT NOT NULL,
            status TEXT NOT NULL, entry_price REAL NOT NULL, target_price REAL,
            exit_price REAL, stake REAL NOT NULL, shares REAL NOT NULL,
            fees REAL NOT NULL DEFAULT 0, pnl REAL, opened_at TEXT NOT NULL,
            closed_at TEXT, note TEXT
        )"""
    )
    db.commit()
    db.close()
    store = Store(path)
    columns = {row["name"] for row in store.db.execute("PRAGMA table_info(trades)")}
    assert {
        "strategy_version", "model_probability", "model_edge", "model_sigma",
        "diagnostics_json",
    } <= columns
    observation_columns = {
        row["name"] for row in store.db.execute("PRAGMA table_info(observations)")
    }
    assert {
        "up_book_timestamp_ms", "down_book_timestamp_ms", "book_age_ms",
        "spot_age_ms",
    } <= observation_columns
    observed = snapshot(market_id=999)
    observed["spot_age_ms"] = 123.0
    store.observe(observed)
    assert store.db.execute(
        "SELECT spot_age_ms FROM observations WHERE market_id=999"
    ).fetchone()[0] == pytest.approx(123.0)
    collector = SimpleNamespace(
        status="LIVE", error=None, updated_at=None, interval=1.0,
        prediction=SimpleNamespace(last_rate_limits={}),
    )
    summaries = store.dashboard(collector)["summaries"]
    zero = {
        "trades": 0, "open": 0, "wins": 0, "losses": 0,
        "realized_pnl": 0, "resetAt": None, "cutoffTradeId": None,
        "carriedOpen": 0, "totalOpen": 0,
    }
    assert summaries["E2"] == zero
    assert summaries["G"] == zero
    assert summaries["H"] == zero
    assert summaries["I"] == zero
    assert summaries["J"] == zero


def test_strategy_h_arms_after_two_consecutive_official_reversals_and_persists(
    tmp_path: Path,
):
    path = tmp_path / "sim.db"
    store = Store(path)
    initial = store.strategy_h_state()
    assert initial["mode"] == "DETECTING"
    assert initial["reversalStreak"] == 0

    settle_h_reversal(store, 100, "UP")
    first = store.strategy_h_state()
    assert first["mode"] == "DETECTING"
    assert first["reversalStreak"] == 1
    assert first["recentEvents"][0]["event_type"] == "REVERSAL_DETECTED"

    settle_h_reversal(store, 101, "DOWN")
    armed = store.strategy_h_state()
    assert armed["mode"] == "ARMED"
    assert armed["reversalStreak"] == 2
    assert armed["lossStreak"] == 0
    assert armed["lastProcessedMarketId"] == 101
    assert armed["armedAt"] is not None
    event = armed["recentEvents"][0]
    assert event["event_type"] == "REVERSAL_ARMED"
    assert event["candidate_side"] == "DOWN"
    assert event["candidate_price"] == pytest.approx(0.91)
    assert event["official"] is True

    store.db.close()
    reopened = Store(path)
    restored = reopened.strategy_h_state()
    assert restored["mode"] == "ARMED"
    assert restored["reversalStreak"] == 2
    assert len(restored["recentEvents"]) == 2


def test_strategy_h_reversal_streak_resets_on_an_official_non_reversal(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    settle_h_reversal(store, 110, "UP")
    assert store.strategy_h_state()["reversalStreak"] == 1

    no_reversal = h_snapshot(
        market_id=111, seconds_left=4.0, up_ask=0.91, down_ask=0.10
    )
    store.observe(no_reversal)
    store.settle_market(111, winner="UP", official=True)
    state = store.strategy_h_state()
    assert state["mode"] == "DETECTING"
    assert state["reversalStreak"] == 0
    assert state["recentEvents"][0]["event_type"] == "REVERSAL_STREAK_RESET"


def test_strategy_h_proxy_settlement_is_persisted_then_reconciled_once(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    row = h_snapshot(
        market_id=120, seconds_left=5.5, up_ask=0.91, down_ask=0.10,
        start_price=65_000.0,
    )
    store.observe(row)
    store.settle_market(
        120,
        winner="DOWN",
        official=False,
        topic_id=row["topic_id"],
        start_price=row["start_price"],
    )
    pending_state = store.strategy_h_state()
    assert pending_state["reversalStreak"] == 0
    assert pending_state["lastProcessedMarketId"] is None
    assert pending_state["recentEvents"][0]["event_type"] == "PENDING_OFFICIAL"
    pending = store.next_pending_settlement()
    assert pending is not None
    assert pending["market_id"] == 120

    collector = Collector(store, None, None)
    collector.prediction = SimpleNamespace(
        market_detail=lambda topic_id: {"variantData": {"endPrice": "64990"}}
    )
    collector.reconcile_pending_settlement()
    reconciled = store.strategy_h_state()
    assert reconciled["reversalStreak"] == 1
    assert reconciled["lastProcessedMarketId"] == 120
    assert reconciled["recentEvents"][0]["event_type"] == "REVERSAL_DETECTED"
    assert reconciled["recentEvents"][0]["details"]["proxyWinner"] == "DOWN"
    assert store.next_pending_settlement() is None

    collector.reconcile_pending_settlement()
    store.settle_market(120, winner="DOWN", official=True)
    assert store.strategy_h_state()["reversalStreak"] == 1
    count = store.db.execute(
        "SELECT COUNT(*) FROM strategy_h_events WHERE market_id=120"
    ).fetchone()[0]
    assert count == 1


def test_restart_reconciliation_settles_open_market_without_settlement_row(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    row = snapshot(market_id=121, topic_id=10_121, start_price=65_000.0)
    store.observe(row)
    store.open_trade(
        strategy="R_OFI",
        topic_id=row["topic_id"],
        market_id=row["market_id"],
        side="UP",
        entry=0.42,
        target=None,
        stake=5.0,
        fee_rate_bps=200,
        note="orphaned across restart",
    )
    assert store.next_pending_settlement() is None
    assert store.next_untracked_open_settlement(999)["market_id"] == 121
    assert store.next_untracked_open_settlement(121) is None

    collector = Collector(store, None, None)
    collector.prediction = SimpleNamespace(
        market_detail=lambda topic_id: {"variantData": {"endPrice": "65010"}}
    )
    assert collector.reconcile_untracked_open_settlement(999) is True
    trade = store.db.execute(
        "SELECT status, pnl FROM trades WHERE market_id=121"
    ).fetchone()
    settlement = store.db.execute(
        "SELECT status, official_winner FROM market_settlements WHERE market_id=121"
    ).fetchone()
    assert trade["status"] == "SETTLED_WIN"
    assert trade["pnl"] > 0
    assert tuple(settlement) == ("OFFICIAL", "UP")
    assert collector.reconcile_untracked_open_settlement(999) is False


def test_strategy_h_official_outcomes_wait_for_older_pending_market(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    older = h_snapshot(
        market_id=130, seconds_left=5.5, up_ask=0.91, down_ask=0.10
    )
    newer = h_snapshot(
        market_id=131, seconds_left=5.5, up_ask=0.10, down_ask=0.91
    )
    store.observe(older)
    store.settle_market(
        130, winner="DOWN", official=False,
        topic_id=older["topic_id"], start_price=older["start_price"],
    )
    store.observe(newer)
    store.settle_market(131, winner="UP", official=True)
    waiting = store.strategy_h_state()
    assert waiting["mode"] == "DETECTING"
    assert waiting["reversalStreak"] == 0
    queued = store.db.execute(
        "SELECT status, h_processed FROM market_settlements WHERE market_id=131"
    ).fetchone()
    assert tuple(queued) == ("OFFICIAL", 0)

    store.settle_market(130, winner="DOWN", official=True, end_price=64_990.0)
    armed = store.strategy_h_state()
    assert armed["mode"] == "ARMED"
    assert armed["reversalStreak"] == 2
    processed = store.db.execute(
        "SELECT market_id, h_processed FROM market_settlements ORDER BY market_id"
    ).fetchall()
    assert [tuple(row) for row in processed] == [(130, 1), (131, 1)]


def test_strategy_h_detection_uses_fresh_anchor_just_before_final_five_seconds(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    already_inside_window = h_snapshot(
        market_id=140, seconds_left=4.5, up_ask=0.91, down_ask=0.10
    )
    store.observe(already_inside_window)
    store.settle_market(140, winner="DOWN", official=True)
    assert store.strategy_h_state()["reversalStreak"] == 0

    stale_anchor = h_snapshot(
        market_id=141, seconds_left=5.5, up_ask=0.91, down_ask=0.10,
        book_age_ms=2001.0,
    )
    store.observe(stale_anchor)
    store.settle_market(141, winner="DOWN", official=True)
    assert store.strategy_h_state()["reversalStreak"] == 0

    fresh_anchor = h_snapshot(
        market_id=142, seconds_left=5.5, up_ask=0.91, down_ask=0.10
    )
    store.observe(fresh_anchor)
    store.settle_market(142, winner="DOWN", official=True)
    state = store.strategy_h_state()
    assert state["reversalStreak"] == 1
    assert state["recentEvents"][0]["candidate_seconds_left"] == pytest.approx(5.5)


def test_strategy_h_enters_fillable_fresh_penny_ask_only_in_last_window(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    arm_strategy_h(store, 200)

    too_early = h_snapshot(
        market_id=202, seconds_left=20.1, up_ask=0.01, down_ask=0.99
    )
    store.observe(too_early)
    store.maybe_enter(too_early, fee_bps=200)
    assert not store.has_trade("H", 202)

    no_visible_quantity = h_snapshot(
        market_id=202, seconds_left=19.0, up_ask=0.01, down_ask=0.99,
        up_ask_size=0.0,
    )
    store.observe(no_visible_quantity)
    store.maybe_enter(no_visible_quantity, fee_bps=200)
    assert not store.has_trade("H", 202)

    fillable = h_snapshot(
        market_id=202, seconds_left=18.0, up_ask=0.01, down_ask=0.99,
        up_ask_size=5_000.0,
    )
    store.observe(fillable)
    store.maybe_enter(fillable, fee_bps=200)
    store.maybe_enter(fillable, fee_bps=200)
    trade = store.db.execute(
        "SELECT * FROM trades WHERE strategy='H' AND market_id=202"
    ).fetchone()
    assert trade is not None
    assert trade["side"] == "UP"
    assert trade["entry_price"] == pytest.approx(0.01)
    assert trade["shares"] == pytest.approx(1_000.0)
    assert trade["stake"] == pytest.approx(10.0)
    assert trade["strategy_version"] == "H_v2_partial_fill"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["execution_model"] == "top_ask_partial_fill_v1"
    assert diagnostics["required_shares"] == pytest.approx(1_000.0)
    assert diagnostics["visible_ask_size"] == pytest.approx(5_000.0)
    assert diagnostics["requested_stake"] == pytest.approx(10.0)
    assert diagnostics["requested_shares"] == pytest.approx(1_000.0)
    assert diagnostics["filled_stake"] == pytest.approx(10.0)
    assert diagnostics["filled_shares"] == pytest.approx(1_000.0)
    assert diagnostics["fill_ratio"] == pytest.approx(1.0)
    assert diagnostics["partial_fill"] is False
    assert diagnostics["arming_reversal_streak"] == 2
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='H' AND market_id=202"
    ).fetchone()[0] == 1

    store.settle_market(202, winner="UP", official=True)
    settled = store.db.execute(
        "SELECT * FROM trades WHERE strategy='H' AND market_id=202"
    ).fetchone()
    assert settled["status"] == "SETTLED_WIN"
    assert settled["pnl"] > 0
    assert store.strategy_h_state()["lossStreak"] == 0


def test_strategy_h_partially_fills_positive_shallow_depth_and_settles(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    arm_strategy_h(store, 220)
    row = h_snapshot(
        market_id=222, seconds_left=10.0, up_ask=0.01, down_ask=0.99,
        up_ask_size=12.5,
    )
    store.maybe_enter(row, fee_bps=200)
    trade = store.db.execute(
        "SELECT * FROM trades WHERE strategy='H' AND market_id=222"
    ).fetchone()
    assert trade["stake"] == pytest.approx(0.125)
    assert trade["shares"] == pytest.approx(12.5)
    assert trade["fees"] == pytest.approx(0.0025)
    assert trade["strategy_version"] == "H_v2_partial_fill"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["requested_stake"] == pytest.approx(10.0)
    assert diagnostics["requested_shares"] == pytest.approx(1_000.0)
    assert diagnostics["filled_stake"] == pytest.approx(0.125)
    assert diagnostics["filled_shares"] == pytest.approx(12.5)
    assert diagnostics["fill_ratio"] == pytest.approx(0.0125)
    assert diagnostics["partial_fill"] is True

    store.settle_market(222, winner="UP", official=True)
    settled = store.db.execute(
        "SELECT * FROM trades WHERE strategy='H' AND market_id=222"
    ).fetchone()
    assert settled["status"] == "SETTLED_WIN"
    assert settled["pnl"] == pytest.approx(12.3725)


@pytest.mark.parametrize(
    "overrides",
    [
        {"up_ask": 0.0},
        {"down_ask": 0.01, "down_bid": 0.005},
        {"up_ask_size": 0.0},
        {"up_ask_size": -1.0},
        {"up_ask_size": None},
        {"up_bid": 0.02},
        {"book_age_ms": 2001.0},
        {"book_skew_ms": 501.0},
    ],
)
def test_strategy_h_rejects_zero_ambiguous_shallow_crossed_or_stale_books(
    tmp_path: Path, overrides: dict,
):
    store = Store(tmp_path / "sim.db")
    prices = {"up_ask": 0.01, "down_ask": 0.99, **overrides}
    row = h_snapshot(
        market_id=210, seconds_left=10.0, **prices,
    )
    assert store.strategy_h_entry_candidate(row, store.config()) is None


def test_strategy_h_two_official_trade_losses_disarm_and_new_reversals_rearm(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    arm_strategy_h(store, 300)

    first_loss = h_snapshot(
        market_id=302, seconds_left=10.0, up_ask=0.01, down_ask=0.99
    )
    store.observe(first_loss)
    store.maybe_enter(first_loss, fee_bps=200)
    store.settle_market(302, winner="DOWN", official=True)
    assert store.strategy_h_state()["lossStreak"] == 1

    no_entry = h_snapshot(
        market_id=303, seconds_left=10.0, up_ask=0.50, down_ask=0.51
    )
    store.observe(no_entry)
    store.maybe_enter(no_entry, fee_bps=200)
    store.settle_market(303, winner="UP", official=True)
    no_trade_state = store.strategy_h_state()
    assert no_trade_state["mode"] == "ARMED"
    assert no_trade_state["lossStreak"] == 1
    assert no_trade_state["recentEvents"][0]["event_type"] == "ARMED_NO_TRADE"

    second_loss = h_snapshot(
        market_id=304, seconds_left=10.0, up_ask=0.99, down_ask=0.01
    )
    store.observe(second_loss)
    store.maybe_enter(second_loss, fee_bps=200)
    store.settle_market(304, winner="UP", official=True)
    disarmed = store.strategy_h_state()
    assert disarmed["mode"] == "DETECTING"
    assert disarmed["lossStreak"] == 0
    assert disarmed["reversalStreak"] == 0
    assert disarmed["recentEvents"][0]["event_type"] == "H_LOSS_DISARMED"
    assert disarmed["recentEvents"][0]["details"]["consecutiveLosses"] == 2

    while_disarmed = h_snapshot(
        market_id=305, seconds_left=10.0, up_ask=0.01, down_ask=0.99
    )
    store.observe(while_disarmed)
    store.maybe_enter(while_disarmed, fee_bps=200)
    assert not store.has_trade("H", 305)
    store.settle_market(305, winner="DOWN", official=True)

    settle_h_reversal(store, 306, "UP")
    settle_h_reversal(store, 307, "DOWN")
    assert store.strategy_h_state()["mode"] == "ARMED"
    after_rearm = h_snapshot(
        market_id=308, seconds_left=10.0, up_ask=0.01, down_ask=0.99
    )
    store.observe(after_rearm)
    store.maybe_enter(after_rearm, fee_bps=200)
    assert store.has_trade("H", 308)


def test_strategy_h_only_official_losses_count_and_win_resets_streak(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    arm_strategy_h(store, 400)

    proxy_loss = h_snapshot(
        market_id=402, seconds_left=10.0, up_ask=0.01, down_ask=0.99
    )
    store.observe(proxy_loss)
    store.maybe_enter(proxy_loss, fee_bps=200)
    store.settle_market(
        402, winner="DOWN", official=False,
        topic_id=proxy_loss["topic_id"], start_price=proxy_loss["start_price"],
    )
    assert store.strategy_h_state()["lossStreak"] == 0
    store.settle_market(402, winner="DOWN", official=True, end_price=64_990.0)
    assert store.strategy_h_state()["lossStreak"] == 1

    win = h_snapshot(
        market_id=403, seconds_left=10.0, up_ask=0.99, down_ask=0.01
    )
    store.observe(win)
    store.maybe_enter(win, fee_bps=200)
    store.settle_market(403, winner="DOWN", official=True)
    won_state = store.strategy_h_state()
    assert won_state["mode"] == "ARMED"
    assert won_state["lossStreak"] == 0
    assert won_state["recentEvents"][0]["event_type"] == "H_WIN"


def test_strategy_h_config_validates_counts_prices_and_freshness(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    with pytest.raises(ValueError, match="whole number"):
        store.update_config({"strategy_h_required_reversals": 1.5})
    with pytest.raises(ValueError, match="must be positive"):
        store.update_config({"strategy_h_max_entry": 0})
    with pytest.raises(ValueError, match="cannot exceed 1"):
        store.update_config({"strategy_h_leader_min_price": 1.01})
    with pytest.raises(ValueError, match="must be positive"):
        store.update_config({"strategy_h_max_book_age_ms": 0})
    config = store.update_config(
        {
            "strategy_h_required_reversals": 3,
            "strategy_h_max_consecutive_losses": 4,
        }
    )
    assert config["strategy_h_required_reversals"] == 3
    assert config["strategy_h_max_consecutive_losses"] == 4


@pytest.mark.parametrize("seconds_left", [299.0, 150.0, 3.001])
def test_strategy_i_enters_at_any_market_time_and_exact_price_boundary(
    tmp_path: Path, seconds_left: float,
):
    store = Store(tmp_path / f"sim-{seconds_left}.db")
    row = i_snapshot(market_id=500, seconds_left=seconds_left, up_ask_size=1_000.0)
    store.observe(row)
    store.maybe_enter(row, fee_bps=200)
    store.maybe_enter(row, fee_bps=200)

    trades = store.db.execute(
        "SELECT * FROM trades WHERE strategy='I' AND market_id=500"
    ).fetchall()
    assert len(trades) == 1
    trade = trades[0]
    assert trade["side"] == "UP"
    assert trade["entry_price"] == pytest.approx(0.01)
    assert trade["stake"] == pytest.approx(1.0)
    assert trade["shares"] == pytest.approx(100.0)
    assert trade["strategy_version"] == "I_v2_partial_fill"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["execution_model"] == "top_ask_partial_fill_v1"
    assert diagnostics["seconds_left"] == pytest.approx(seconds_left)
    assert diagnostics["quoted_ask"] == pytest.approx(0.01)
    assert diagnostics["visible_ask_size"] == pytest.approx(1_000.0)
    assert diagnostics["required_shares"] == pytest.approx(100.0)
    assert diagnostics["requested_stake"] == pytest.approx(1.0)
    assert diagnostics["requested_shares"] == pytest.approx(100.0)
    assert diagnostics["filled_stake"] == pytest.approx(1.0)
    assert diagnostics["filled_shares"] == pytest.approx(100.0)
    assert diagnostics["fill_ratio"] == pytest.approx(1.0)
    assert diagnostics["partial_fill"] is False
    assert diagnostics["config"] == {
        "strategy_i_enabled": True,
        "strategy_i_max_book_age_ms": 2000.0,
        "strategy_i_max_book_skew_ms": 500.0,
        "strategy_i_max_entry": 0.01,
        "strategy_i_min_seconds_left": 3.0,
        "strategy_i_stake": 1.0,
    }


def test_strategy_i_does_not_reenter_same_market_or_accept_above_boundary(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    above = i_snapshot(market_id=510, up_ask=0.010001)
    assert store.strategy_i_entry_candidate(above, store.config()) is None
    store.maybe_enter(above, fee_bps=200)
    assert not store.has_trade("I", 510)

    first = i_snapshot(market_id=510)
    store.maybe_enter(first, fee_bps=200)
    opposite = i_snapshot(
        market_id=510,
        up_ask=0.99,
        up_bid=0.98,
        down_ask=0.01,
        down_bid=0.0,
    )
    store.maybe_enter(opposite, fee_bps=200)
    trades = store.db.execute(
        "SELECT * FROM trades WHERE strategy='I' AND market_id=510"
    ).fetchall()
    assert len(trades) == 1
    assert trades[0]["side"] == "UP"


@pytest.mark.parametrize("seconds_left", [3.0, 2.999, 0.0])
def test_strategy_i_rejects_final_three_seconds(
    tmp_path: Path, seconds_left: float,
):
    store = Store(tmp_path / f"sim-{seconds_left}.db")
    row = i_snapshot(market_id=515, seconds_left=seconds_left)
    assert store.strategy_i_entry_candidate(row, store.config()) is None
    store.maybe_enter(row, fee_bps=200)
    assert not store.has_trade("I", 515)


@pytest.mark.parametrize(
    "overrides",
    [
        {"up_ask_size": 0.0},
        {"up_ask_size": -1.0},
        {"up_ask_size": None},
        {"down_ask": 0.01, "down_bid": 0.0},
        {"down_ask": 0.01, "down_bid": 0.0, "down_ask_size": 1.0},
        {"up_bid": 0.02},
        {"book_age_ms": 2000.001},
        {"book_skew_ms": 500.001},
        {"book_age_ms": None},
        {"book_skew_ms": None},
    ],
)
def test_strategy_i_rejects_shallow_ambiguous_crossed_or_stale_books(
    tmp_path: Path, overrides: dict,
):
    store = Store(tmp_path / "sim.db")
    row = i_snapshot(market_id=520, **overrides)
    assert store.strategy_i_entry_candidate(row, store.config()) is None


def test_strategy_i_accepts_freshness_boundaries_and_settles_with_fees(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    winner = i_snapshot(
        market_id=530, book_age_ms=2000.0, book_skew_ms=500.0,
    )
    assert store.strategy_i_entry_candidate(winner, store.config()) is not None
    store.maybe_enter(winner, fee_bps=200)
    opened = store.db.execute(
        "SELECT * FROM trades WHERE strategy='I' AND market_id=530"
    ).fetchone()
    assert opened["status"] == "OPEN"
    assert opened["fees"] == pytest.approx(0.02)

    store.settle_market(530, winner="UP", official=True)
    won = store.db.execute(
        "SELECT * FROM trades WHERE strategy='I' AND market_id=530"
    ).fetchone()
    assert won["status"] == "SETTLED_WIN"
    assert won["exit_price"] == pytest.approx(1.0)
    assert won["pnl"] == pytest.approx(98.98)

    loser = i_snapshot(market_id=531)
    store.maybe_enter(loser, fee_bps=200)
    store.settle_market(531, winner="DOWN", official=True)
    lost = store.db.execute(
        "SELECT * FROM trades WHERE strategy='I' AND market_id=531"
    ).fetchone()
    assert lost["status"] == "SETTLED_LOSS"
    assert lost["fees"] == pytest.approx(0.02)
    assert lost["pnl"] == pytest.approx(-1.02)

    collector = SimpleNamespace(
        status="LIVE", error=None, updated_at=None, interval=1.0,
        prediction=SimpleNamespace(last_rate_limits={}),
    )
    summary = store.dashboard(collector)["summaries"]["I"]
    assert summary == {
        "trades": 2,
        "open": 0,
        "wins": 1,
        "losses": 1,
        "realized_pnl": pytest.approx(97.96),
        "resetAt": None,
        "cutoffTradeId": None,
        "carriedOpen": 0,
        "totalOpen": 0,
    }


def test_strategy_i_partially_fills_positive_shallow_depth_with_exact_accounting(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    row = i_snapshot(market_id=540, up_ask_size=12.5)
    store.maybe_enter(row, fee_bps=200)
    trade = store.db.execute(
        "SELECT * FROM trades WHERE strategy='I' AND market_id=540"
    ).fetchone()
    assert trade["stake"] == pytest.approx(0.125)
    assert trade["shares"] == pytest.approx(12.5)
    assert trade["fees"] == pytest.approx(0.0025)
    assert trade["strategy_version"] == "I_v2_partial_fill"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["execution_model"] == "top_ask_partial_fill_v1"
    assert diagnostics["requested_stake"] == pytest.approx(1.0)
    assert diagnostics["requested_shares"] == pytest.approx(100.0)
    assert diagnostics["filled_stake"] == pytest.approx(0.125)
    assert diagnostics["filled_shares"] == pytest.approx(12.5)
    assert diagnostics["fill_ratio"] == pytest.approx(0.125)
    assert diagnostics["partial_fill"] is True

    store.settle_market(540, winner="DOWN", official=True)
    settled = store.db.execute(
        "SELECT * FROM trades WHERE strategy='I' AND market_id=540"
    ).fetchone()
    assert settled["status"] == "SETTLED_LOSS"
    assert settled["pnl"] == pytest.approx(-0.1275)


def test_strategy_i_config_defaults_and_validation(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    config = store.config()
    assert config["strategy_i_enabled"] is True
    assert config["strategy_i_max_entry"] == pytest.approx(0.01)
    assert config["strategy_i_min_seconds_left"] == pytest.approx(3.0)
    assert config["strategy_i_stake"] == pytest.approx(1.0)
    assert config["strategy_i_max_book_skew_ms"] == pytest.approx(500.0)
    assert config["strategy_i_max_book_age_ms"] == pytest.approx(2000.0)

    for key in (
        "strategy_i_max_entry", "strategy_i_stake",
        "strategy_i_max_book_skew_ms", "strategy_i_max_book_age_ms",
    ):
        with pytest.raises(ValueError, match="must be positive"):
            store.update_config({key: 0})
    with pytest.raises(ValueError, match="cannot exceed 1"):
        store.update_config({"strategy_i_max_entry": 1.01})
    with pytest.raises(ValueError, match="cannot be negative"):
        store.update_config({"strategy_i_min_seconds_left": -0.001})
    assert store.update_config({"strategy_i_min_seconds_left": 0})[
        "strategy_i_min_seconds_left"
    ] == pytest.approx(0.0)

    updated = store.update_config(
        {
            "strategy_i_enabled": False,
            "strategy_i_max_entry": 0.005,
            "strategy_i_min_seconds_left": 1.5,
            "strategy_i_stake": 2.0,
            "strategy_i_max_book_skew_ms": 250.0,
            "strategy_i_max_book_age_ms": 1000.0,
        }
    )
    assert updated["strategy_i_enabled"] is False
    assert updated["strategy_i_max_entry"] == pytest.approx(0.005)
    assert updated["strategy_i_min_seconds_left"] == pytest.approx(1.5)
    assert updated["strategy_i_stake"] == pytest.approx(2.0)


@pytest.mark.parametrize("seconds_left", [120.0, 3.001])
def test_strategy_j_enters_once_at_inclusive_late_window_boundaries(
    tmp_path: Path, seconds_left: float,
):
    store = Store(tmp_path / f"sim-{seconds_left}.db")
    row = j_snapshot(market_id=600, seconds_left=seconds_left)
    store.observe(row)
    store.maybe_enter(row, fee_bps=200)
    store.maybe_enter(row, fee_bps=200)
    reversed_row = j_snapshot(
        market_id=600,
        seconds_left=max(seconds_left - 0.001, 3.0001),
        up_ask=0.85,
        up_bid=0.84,
        down_ask=0.15,
        down_bid=0.14,
    )
    store.maybe_enter(reversed_row, fee_bps=200)

    trades = store.db.execute(
        "SELECT * FROM trades WHERE strategy='J' AND market_id=600"
    ).fetchall()
    assert len(trades) == 1
    trade = trades[0]
    assert trade["side"] == "UP"
    assert trade["entry_price"] == pytest.approx(0.15)
    assert trade["target_price"] == pytest.approx(0.30)
    assert trade["stake"] == pytest.approx(10.0)
    assert trade["shares"] == pytest.approx(10 / 0.15)
    assert trade["strategy_version"] == "J_v2_partial_fill"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["execution_model"] == "top_ask_partial_fill_v1"
    assert diagnostics["seconds_left"] == pytest.approx(seconds_left)
    assert diagnostics["ask_gap"] == pytest.approx(0.70)
    assert diagnostics["target_price"] == pytest.approx(0.30)
    assert diagnostics["required_shares"] == pytest.approx(10 / 0.15)
    assert diagnostics["requested_stake"] == pytest.approx(10.0)
    assert diagnostics["requested_shares"] == pytest.approx(10 / 0.15)
    assert diagnostics["filled_stake"] == pytest.approx(10.0)
    assert diagnostics["filled_shares"] == pytest.approx(10 / 0.15)
    assert diagnostics["fill_ratio"] == pytest.approx(1.0)
    assert diagnostics["partial_fill"] is False
    assert diagnostics["config"]["strategy_j_window_seconds"] == pytest.approx(120.0)
    assert diagnostics["config"]["strategy_j_min_seconds_left"] == pytest.approx(3.0)


@pytest.mark.parametrize("seconds_left", [120.001, 3.0, 2.999, 0.0])
def test_strategy_j_rejects_outside_late_window(
    tmp_path: Path, seconds_left: float,
):
    store = Store(tmp_path / f"sim-{seconds_left}.db")
    row = j_snapshot(market_id=610, seconds_left=seconds_left)
    assert store.strategy_j_entry_candidate(row, store.config()) is None
    store.maybe_enter(row, fee_bps=200)
    assert not store.has_trade("J", 610)


@pytest.mark.parametrize(
    "overrides",
    [
        {"up_ask_size": 0.0},
        {"up_ask_size": -1.0},
        {"up_ask_size": None},
        {"book_age_ms": 2000.001},
        {"book_skew_ms": 500.001},
        {"book_age_ms": None},
        {"book_skew_ms": None},
        {"up_bid": 0.16},
        {"down_bid": 0.86},
        {"up_ask": None},
        {"down_ask": 0.0},
        {"down_ask": 0.349, "down_bid": 0.34},
        {"up_ask": 0.200001, "up_bid": 0.19, "down_ask": 0.80, "down_bid": 0.79},
    ],
)
def test_strategy_j_rejects_shallow_stale_crossed_or_invalid_books(
    tmp_path: Path, overrides: dict,
):
    store = Store(tmp_path / "sim.db")
    row = j_snapshot(market_id=620, **overrides)
    assert store.strategy_j_entry_candidate(row, store.config()) is None


def test_strategy_j_hits_double_target_with_realistic_fees_or_settles(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    target_trade = j_snapshot(
        market_id=630,
        up_ask_size=10 / 0.15,
        book_age_ms=2000.0,
        book_skew_ms=500.0,
    )
    assert store.strategy_j_entry_candidate(target_trade, store.config()) is not None
    store.maybe_enter(target_trade, fee_bps=200)
    store.process_intraday_targets(630, up_bid=0.299, down_bid=0.70, fee_bps=200)
    still_open = store.db.execute(
        "SELECT * FROM trades WHERE strategy='J' AND market_id=630"
    ).fetchone()
    assert still_open["status"] == "OPEN"

    store.process_intraday_targets(630, up_bid=0.30, down_bid=0.70, fee_bps=200)
    closed = store.db.execute(
        "SELECT * FROM trades WHERE strategy='J' AND market_id=630"
    ).fetchone()
    assert closed["status"] == "TARGET_FILLED"
    assert closed["exit_price"] == pytest.approx(0.30)
    assert closed["fees"] == pytest.approx(0.60)
    assert closed["pnl"] == pytest.approx(9.40)

    settlement_trade = j_snapshot(market_id=631)
    store.maybe_enter(settlement_trade, fee_bps=200)
    store.settle_market(631, winner="DOWN", official=True)
    settled = store.db.execute(
        "SELECT * FROM trades WHERE strategy='J' AND market_id=631"
    ).fetchone()
    assert settled["status"] == "SETTLED_LOSS"
    assert settled["fees"] == pytest.approx(0.20)
    assert settled["pnl"] == pytest.approx(-10.20)

    collector = SimpleNamespace(
        status="LIVE", error=None, updated_at=None, interval=1.0,
        prediction=SimpleNamespace(last_rate_limits={}),
    )
    summary = store.dashboard(collector)["summaries"]["J"]
    assert summary == {
        "trades": 2,
        "open": 0,
        "wins": 1,
        "losses": 1,
        "realized_pnl": pytest.approx(-0.80),
        "resetAt": None,
        "cutoffTradeId": None,
        "carriedOpen": 0,
        "totalOpen": 0,
    }


def test_strategy_j_partially_fills_and_targets_only_the_filled_quantity(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    row = j_snapshot(market_id=640, up_ask_size=10.0)
    store.maybe_enter(row, fee_bps=200)
    trade = store.db.execute(
        "SELECT * FROM trades WHERE strategy='J' AND market_id=640"
    ).fetchone()
    assert trade["stake"] == pytest.approx(1.5)
    assert trade["shares"] == pytest.approx(10.0)
    assert trade["fees"] == pytest.approx(0.03)
    assert trade["strategy_version"] == "J_v2_partial_fill"
    diagnostics = json.loads(trade["diagnostics_json"])
    assert diagnostics["execution_model"] == "top_ask_partial_fill_v1"
    assert diagnostics["requested_stake"] == pytest.approx(10.0)
    assert diagnostics["requested_shares"] == pytest.approx(10 / 0.15)
    assert diagnostics["filled_stake"] == pytest.approx(1.5)
    assert diagnostics["filled_shares"] == pytest.approx(10.0)
    assert diagnostics["fill_ratio"] == pytest.approx(0.15)
    assert diagnostics["partial_fill"] is True

    store.process_intraday_targets(640, up_bid=0.30, down_bid=0.70, fee_bps=200)
    closed = store.db.execute(
        "SELECT * FROM trades WHERE strategy='J' AND market_id=640"
    ).fetchone()
    assert closed["status"] == "TARGET_FILLED"
    assert closed["fees"] == pytest.approx(0.09)
    assert closed["pnl"] == pytest.approx(1.41)


def test_strategy_j_config_defaults_and_validation(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    config = store.config()
    assert config["strategy_j_enabled"] is True
    assert config["strategy_j_window_seconds"] == pytest.approx(120.0)
    assert config["strategy_j_min_seconds_left"] == pytest.approx(3.0)
    assert config["strategy_j_min_gap"] == pytest.approx(0.20)
    assert config["strategy_j_max_entry"] == pytest.approx(0.20)
    assert config["strategy_j_target_multiplier"] == pytest.approx(2.0)
    assert config["strategy_j_max_book_skew_ms"] == pytest.approx(500.0)
    assert config["strategy_j_max_book_age_ms"] == pytest.approx(2000.0)
    assert config["strategy_j_stake"] == pytest.approx(10.0)

    for key in (
        "strategy_j_window_seconds", "strategy_j_max_entry",
        "strategy_j_target_multiplier", "strategy_j_max_book_skew_ms",
        "strategy_j_max_book_age_ms", "strategy_j_stake",
    ):
        with pytest.raises(ValueError, match="must be positive"):
            store.update_config({key: 0})
    with pytest.raises(ValueError, match="cannot be negative"):
        store.update_config({"strategy_j_min_seconds_left": -0.001})
    with pytest.raises(ValueError, match="cannot exceed 1"):
        store.update_config({"strategy_j_min_gap": 1.001})
    with pytest.raises(ValueError, match="cannot exceed 1"):
        store.update_config({"strategy_j_max_entry": 1.001})
    with pytest.raises(ValueError, match="less than its window"):
        store.update_config({"strategy_j_min_seconds_left": 120.0})

    updated = store.update_config(
        {
            "strategy_j_enabled": False,
            "strategy_j_window_seconds": 90.0,
            "strategy_j_min_seconds_left": 2.5,
            "strategy_j_min_gap": 0.25,
            "strategy_j_max_entry": 0.15,
            "strategy_j_target_multiplier": 2.5,
            "strategy_j_max_book_skew_ms": 250.0,
            "strategy_j_max_book_age_ms": 1000.0,
            "strategy_j_stake": 5.0,
        }
    )
    assert updated["strategy_j_enabled"] is False
    assert updated["strategy_j_window_seconds"] == pytest.approx(90.0)
    assert updated["strategy_j_min_seconds_left"] == pytest.approx(2.5)
    assert updated["strategy_j_target_multiplier"] == pytest.approx(2.5)
    assert updated["strategy_j_stake"] == pytest.approx(5.0)


def test_strategy_k_basis_adjusts_direction_and_probability_cost_formula(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=700,
        end_seconds_left=100,
        total_log_move=0.002,
        final_overrides={
            # Binance spot is below the official strike but above its same-feed
            # opening anchor. K must follow the latter after basis adjustment.
            "start_price": 65_200.0,
            "up_ask": 0.50, "up_bid": 0.49,
            "down_ask": 0.10, "down_bid": 0.09,
            "up_ask_size": 100.0, "down_ask_size": 100.0,
        },
    )
    assert 65_000 < final["spot_price"] < final["start_price"]
    store.maybe_enter(final, fee_bps=200)

    trade = store.db.execute("SELECT * FROM trades WHERE strategy='K'").fetchone()
    assert trade is not None
    assert trade["side"] == "UP"
    assert trade["strategy_version"] == "K_v1_basis_adjusted_terminal_prob"
    diagnostics = json.loads(trade["diagnostics_json"])
    expected_adjusted_spot = final["spot_price"] * 65_200.0 / 65_000.0
    assert diagnostics["basis_adjusted_spot"] == pytest.approx(expected_adjusted_spot)
    assert expected_adjusted_spot > final["start_price"]
    assert diagnostics["direction_uses_order_price"] is False
    assert diagnostics["quoted_ask"] == pytest.approx(0.50)
    assert diagnostics["config"]["strategy_k_calibration_slope"] == pytest.approx(0.5)

    signed_distance = math.log(final["spot_price"] / 65_000.0)
    variance = trade["model_sigma"] ** 2 * (100.0 + 2.0) + (1.5 / 10_000) ** 2
    z_score = abs(signed_distance) / math.sqrt(variance)
    raw_probability = normal_cdf(z_score)
    calibrated = temperature_calibrate_probability(raw_probability, 0.5)
    execution_entry = 0.50 * (1 + 50 / 10_000)
    effective_cost = execution_entry + min(execution_entry, 1 - execution_entry) * 0.02
    assert diagnostics["variance"] == pytest.approx(variance)
    assert diagnostics["z_score"] == pytest.approx(z_score)
    assert diagnostics["q_raw"] == pytest.approx(raw_probability)
    assert trade["model_probability"] == pytest.approx(calibrated)
    assert trade["entry_price"] == pytest.approx(execution_entry)
    assert diagnostics["effective_cost"] == pytest.approx(effective_cost)
    assert trade["model_edge"] == pytest.approx(calibrated - effective_cost)

    store.settle_market(700, winner="UP", official=True)
    settled = store.db.execute("SELECT * FROM trades WHERE strategy='K'").fetchone()
    expected_entry_fee = settled["shares"] * min(execution_entry, 1 - execution_entry) * 0.02
    assert settled["status"] == "SETTLED_WIN"
    assert settled["pnl"] == pytest.approx(settled["shares"] - 10.0 - expected_entry_fee)


@pytest.mark.parametrize(
    "final_overrides",
    [
        {"book_skew_ms": 501.0},
        {"book_age_ms": 2001.0},
        {"up_ask": 0.50, "up_bid": 0.51},
        {"up_ask": 0.50, "up_bid": 0.47},
        {"up_ask_size": 1.0},
        {"up_ask": 0.98, "up_bid": 0.97},
    ],
)
def test_strategy_k_rejects_stale_crossed_wide_shallow_or_expensive_execution(
    tmp_path: Path, final_overrides: dict
):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=701,
        end_seconds_left=100,
        total_log_move=0.002,
        final_overrides={
            "up_ask": 0.50, "up_bid": 0.49,
            "down_ask": 0.10, "down_bid": 0.09,
            "up_ask_size": 100.0, "down_ask_size": 100.0,
            **final_overrides,
        },
    )
    model = store.strategy_k_probability_model(final, store.config())
    assert model is not None and model["side"] == "UP"
    assert store.strategy_k_entry_candidate(final, store.config(), 200) is None


def test_strategy_k_never_flips_to_cheaper_opposite_side(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=702,
        end_seconds_left=100,
        total_log_move=0.002,
        final_overrides={
            "up_ask": 0.98, "up_bid": 0.97,
            "down_ask": 0.05, "down_bid": 0.04,
            "up_ask_size": 100.0, "down_ask_size": 1000.0,
        },
    )
    model = store.strategy_k_probability_model(final, store.config())
    assert model is not None and model["side"] == "UP"
    assert store.strategy_k_entry_candidate(final, store.config(), 200) is None
    store.maybe_enter(final, fee_bps=200)
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='K'"
    ).fetchone()[0] == 0


@pytest.mark.parametrize("seconds_left", [181.0, 19.0])
def test_strategy_k_rejects_outside_entry_window(tmp_path: Path, seconds_left: float):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=703,
        end_seconds_left=seconds_left,
        total_log_move=0.002,
        final_overrides={
            "up_ask": 0.50, "up_bid": 0.49, "up_ask_size": 100.0,
            "down_ask": 0.50, "down_bid": 0.49, "down_ask_size": 100.0,
        },
    )
    assert store.strategy_k_entry_candidate(final, store.config(), 200) is None


def test_strategy_k_requires_early_baseline_and_minimum_returns(tmp_path: Path):
    store = Store(tmp_path / "late.db")
    final = seed_spot_history(
        store,
        market_id=704,
        end_seconds_left=100,
        total_log_move=0.002,
    )
    store.db.execute(
        "UPDATE observations SET seconds_left=289 WHERE id=(SELECT MIN(id) FROM observations)"
    )
    store.db.commit()
    assert store.strategy_k_probability_model(final, store.config()) is None

    sparse = Store(tmp_path / "sparse.db")
    started_at = datetime(2026, 7, 16, tzinfo=timezone.utc)
    sparse_final = None
    for index in range(20):
        sparse_final = snapshot(
            market_id=705,
            timestamp=(started_at + timedelta(seconds=index)).isoformat(),
            seconds_left=295 - index,
            spot_price=65_000 + index,
        )
        sparse.observe(sparse_final)
    assert sparse_final is not None
    assert sparse.strategy_k_probability_model(sparse_final, sparse.config()) is None


def test_strategy_k_historical_model_excludes_future_observations(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store, market_id=706, end_seconds_left=100, total_log_move=0.002
    )
    before = store.strategy_k_probability_model(final, store.config())
    assert before is not None
    final_time = datetime.fromisoformat(final["timestamp"])
    for index in range(1, 21):
        future = {
            **final,
            "timestamp": (final_time + timedelta(seconds=index)).isoformat(),
            "seconds_left": 100 - index,
            "spot_price": final["spot_price"] * (1.02 if index % 2 else 0.98),
        }
        store.observe(future)
    after = store.strategy_k_probability_model(final, store.config())
    assert after is not None
    assert after["return_samples"] == before["return_samples"]
    assert after["sigma"] == pytest.approx(before["sigma"])
    assert after["raw_probability"] == pytest.approx(before["raw_probability"])
    assert after["probability"] == pytest.approx(before["probability"])


def test_strategy_k_forecast_checkpoint_is_not_conditioned_on_trade_and_gets_outcome(
    tmp_path: Path,
):
    store = Store(tmp_path / "sim.db")
    final = seed_spot_history(
        store,
        market_id=707,
        end_seconds_left=180,
        total_log_move=0.002,
        final_overrides={
            "up_ask": 0.99, "up_bid": 0.98,
            "down_ask": 0.02, "down_bid": 0.01,
            "up_ask_size": 100.0, "down_ask_size": 100.0,
        },
    )
    store.maybe_enter(final, fee_bps=200)
    assert store.db.execute(
        "SELECT COUNT(*) FROM trades WHERE strategy='K'"
    ).fetchone()[0] == 0
    forecast = store.db.execute(
        "SELECT * FROM strategy_k_forecasts WHERE market_id=707"
    ).fetchone()
    assert forecast is not None
    assert forecast["checkpoint_seconds"] == 180
    assert forecast["signal_side"] == "UP"
    assert forecast["official_winner"] is None
    assert forecast["forecast_correct"] is None

    store.settle_market(707, winner="DOWN", official=False)
    pending = store.db.execute(
        "SELECT * FROM strategy_k_forecasts WHERE market_id=707"
    ).fetchone()
    assert pending["settled_official"] == 0
    store.settle_market(707, winner="UP", official=True, end_price=65_200.0)
    official = store.db.execute(
        "SELECT * FROM strategy_k_forecasts WHERE market_id=707"
    ).fetchone()
    assert official["official_winner"] == "UP"
    assert official["forecast_correct"] == 1
    assert official["settled_official"] == 1


def test_drawdown_control_history_is_official_causal_and_ordered(tmp_path: Path):
    store = Store(tmp_path / "drawdown-history.db")
    store.settle_market(
        189,
        winner="DOWN",
        official=False,
        start_price=100.0,
        end_price=99.9,
    )
    for market_id in range(190, 198):
        store.settle_market(
            market_id,
            winner="DOWN",
            official=True,
            start_price=100.0,
            end_price=99.95,
        )

    history = store.drawdown_control_market_history(197, 6)

    assert [row["market_id"] for row in history] == list(range(191, 197))
    assert all(row["start_price"] == pytest.approx(100.0) for row in history)
    assert all(row["end_price"] == pytest.approx(99.95) for row in history)


def test_strategy_k_defaults_updates_validation_and_dashboard_summary(tmp_path: Path):
    store = Store(tmp_path / "sim.db")
    config = store.config()
    expected = {
        "strategy_k_enabled": True,
        "strategy_k_max_seconds_left": 180.0,
        "strategy_k_min_seconds_left": 20.0,
        "strategy_k_min_baseline_seconds_left": 290.0,
        "strategy_k_lookback_observations": 120.0,
        "strategy_k_min_return_samples": 60.0,
        "strategy_k_sigma_floor": 0.00002,
        "strategy_k_basis_uncertainty_bps": 1.5,
        "strategy_k_calibration_slope": 0.5,
        "strategy_k_latency_seconds": 2.0,
        "strategy_k_min_probability": 0.65,
        "strategy_k_max_spread": 0.02,
        "strategy_k_min_net_edge": 0.05,
        "strategy_k_spread_edge_multiplier": 3.0,
        "strategy_k_slippage_bps": 50.0,
        "strategy_k_max_book_skew_ms": 500.0,
        "strategy_k_max_book_age_ms": 2000.0,
        "strategy_k_stake": 10.0,
    }
    for key, value in expected.items():
        assert config[key] == pytest.approx(value) if not isinstance(value, bool) else config[key] is value

    updated = store.update_config(
        {
            "strategy_k_enabled": False,
            "strategy_k_max_seconds_left": 150,
            "strategy_k_min_seconds_left": 30,
            "strategy_k_min_baseline_seconds_left": 285,
            "strategy_k_lookback_observations": 100,
            "strategy_k_min_return_samples": 50,
            "strategy_k_stake": 5,
        }
    )
    assert updated["strategy_k_enabled"] is False
    assert updated["strategy_k_max_seconds_left"] == pytest.approx(150)
    assert updated["strategy_k_stake"] == pytest.approx(5)

    invalid_updates = (
        {"strategy_k_min_probability": 0.49},
        {"strategy_k_sigma_floor": 0},
        {"strategy_k_basis_uncertainty_bps": 0},
        {"strategy_k_calibration_slope": 0},
        {"strategy_k_slippage_bps": 10_001},
        {"strategy_k_stake": 0},
        {"strategy_k_min_return_samples": 100},
        {"strategy_k_min_baseline_seconds_left": 149},
        {"strategy_k_min_seconds_left": 151},
    )
    for invalid in invalid_updates:
        with pytest.raises(ValueError):
            store.update_config(invalid)

    collector = SimpleNamespace(
        status="LIVE", error=None, updated_at=None, interval=1.0,
        prediction=SimpleNamespace(last_rate_limits={}),
    )
    assert store.dashboard(collector)["summaries"]["K"] == {
        "trades": 0, "open": 0, "wins": 0, "losses": 0,
        "realized_pnl": 0, "resetAt": None,
        "cutoffTradeId": None, "carriedOpen": 0, "totalOpen": 0,
    }
