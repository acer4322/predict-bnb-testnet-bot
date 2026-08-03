import time

import pytest

from predict_bot.m_realtime import (
    LIVE_FORWARDABLE_PAPER_STRATEGIES,
    MSeriesRealtimeEngine,
)
from predict_bot.microprice_variants import (
    MICROPRICE_CONFIRM_STRATEGY,
    MICROPRICE_MAX_BOOK_AGE_MS,
    MICROPRICE_MAX_BOOK_SKEW_MS,
    MICROPRICE_MIN_CONFIRMATIONS,
    MICROPRICE_MIN_CONFIRMATION_MS,
    MICROPRICE_REVERSION_STRATEGY,
    MICROPRICE_VARIANT_VERSION,
    MICROPRICE_WINDOW_MIN_SECONDS_LEFT,
    microprice_score,
)


class _Store:
    def __init__(self):
        self.opened = []

    def config(self):
        return {"strategy_r_microprice_enabled": True}

    def maybe_enter_m_series(
        self,
        snapshot,
        fee_bps,
        *,
        realtime_context=None,
    ):
        return []

    def open_trade(self, **kwargs):
        self.opened.append(kwargs)


def _market():
    now_ms = time.time_ns() / 1_000_000
    return {
        "market_id": 101,
        "topic_id": 202,
        "title": "BTC Up or Down",
        "start_price": 65_000.0,
        "start_ms": now_ms - 120_000,
        "end_ms": now_ms + 180_000,
        "fee_bps": 200,
        "server_clock_offset_ms": 0.0,
    }


def _direct_event():
    return {
        "source": "prediction",
        "stream": "orderbook",
        "market_id": 101,
        "prediction_data_source": "dual_token_rest",
        "direct_outcome_books": True,
        "feature_eligible": True,
    }


def _snapshot(
    *,
    sequence: int,
    received_monotonic_ns: int,
    down_midpoint_shift: float = 0.0,
    up_midpoint_shift: float = 0.0,
    book_age_ms: float = 100.0,
    book_skew_ms: float = 20.0,
    seconds_left: float | None = None,
):
    up_bid = 0.68 + up_midpoint_shift
    up_ask = 0.70 + up_midpoint_shift
    down_bid = 0.29 + down_midpoint_shift
    down_ask = 0.31 + down_midpoint_shift
    return {
        "timestamp": f"2026-08-04T00:00:0{sequence}+00:00",
        "timestamp_ns": 1_000_000_000 + sequence,
        "topic_id": 202,
        "market_id": 101,
        "seconds_left": (
            180.0 - sequence * 0.2
            if seconds_left is None
            else seconds_left
        ),
        "up_bid": up_bid,
        "up_ask": up_ask,
        "up_bid_size": 30.0,
        "up_ask_size": 200.0,
        "down_bid": down_bid,
        "down_ask": down_ask,
        "down_bid_size": 200.0,
        "down_ask_size": 30.0,
        "book_age_ms": book_age_ms,
        "book_skew_ms": book_skew_ms,
        "received_monotonic_ns": received_monotonic_ns,
        "signal_event_sequence": f"prediction:{sequence}",
    }


def _context(sequence: int, received_monotonic_ns: int):
    return {
        "trigger_source": "prediction",
        "signal_event_type": "prediction",
        "execution_eligible": True,
        "prediction_data_source": "dual_token_rest",
        "signal_event_sequence": f"prediction:{sequence}",
        "received_monotonic_ns": received_monotonic_ns,
        "signal_received_monotonic_ns": received_monotonic_ns,
        "market_data_integrity_ok": True,
    }


def _engine_and_store():
    store = _Store()
    engine = MSeriesRealtimeEngine(
        store=store,
        current_market=_market,
    )
    engine.prediction_event = _direct_event()
    return engine, store


def test_relaxed_v2_keeps_book_safety_limits() -> None:
    assert MICROPRICE_VARIANT_VERSION == "MICROPRICE_VARIANTS_V2_RELAXED"
    assert MICROPRICE_MIN_CONFIRMATIONS == 2
    assert MICROPRICE_MIN_CONFIRMATION_MS == 150.0
    assert MICROPRICE_WINDOW_MIN_SECONDS_LEFT == 170.0
    assert MICROPRICE_MAX_BOOK_AGE_MS == 500.0
    assert MICROPRICE_MAX_BOOK_SKEW_MS == 150.0


def test_microprice_score_preserves_existing_direction_convention():
    snapshot = _snapshot(sequence=1, received_monotonic_ns=1)
    score = microprice_score(snapshot)

    assert score is not None
    assert score < -0.20


def test_paired_variants_open_after_two_confirmations_and_midpoint_move():
    engine, store = _engine_and_store()
    base_ns = 10_000_000_000

    first = store.maybe_enter_m_series(
        _snapshot(
            sequence=1,
            received_monotonic_ns=base_ns,
        ),
        200,
        realtime_context=_context(1, base_ns),
    )
    second = store.maybe_enter_m_series(
        _snapshot(
            sequence=2,
            received_monotonic_ns=base_ns + 200_000_000,
            down_midpoint_shift=0.001,
            up_midpoint_shift=-0.001,
        ),
        200,
        realtime_context=_context(2, base_ns + 200_000_000),
    )

    assert first == []
    assert len(store.opened) == 2
    by_strategy = {
        item["strategy"]: item
        for item in store.opened
    }
    assert by_strategy[MICROPRICE_CONFIRM_STRATEGY]["side"] == "DOWN"
    assert by_strategy[MICROPRICE_REVERSION_STRATEGY]["side"] == "UP"
    assert all(
        item["strategy_version"] == MICROPRICE_VARIANT_VERSION
        for item in store.opened
    )
    assert all(
        item["diagnostics"]["paper_only"] is True
        and item["diagnostics"]["live_orders_affected"] is False
        for item in store.opened
    )
    assert {
        item["strategy"] for item in second
    } == {
        MICROPRICE_CONFIRM_STRATEGY,
        MICROPRICE_REVERSION_STRATEGY,
    }
    assert all(item["paper_only"] is True for item in second)

    state = engine.state()["micropriceVariants"]
    assert state["confirmedPairs"] == 1
    assert state["lastDecision"]["status"] == "OPENED_PAIR"


def test_relaxed_window_accepts_signal_at_170_seconds_left():
    _, store = _engine_and_store()
    base_ns = 15_000_000_000

    store.maybe_enter_m_series(
        _snapshot(
            sequence=1,
            received_monotonic_ns=base_ns,
            seconds_left=170.2,
        ),
        200,
        realtime_context=_context(1, base_ns),
    )
    store.maybe_enter_m_series(
        _snapshot(
            sequence=2,
            received_monotonic_ns=base_ns + 200_000_000,
            down_midpoint_shift=0.001,
            up_midpoint_shift=-0.001,
            seconds_left=170.0,
        ),
        200,
        realtime_context=_context(2, base_ns + 200_000_000),
    )

    assert len(store.opened) == 2


def test_variants_are_never_live_forwardable():
    assert MICROPRICE_CONFIRM_STRATEGY not in LIVE_FORWARDABLE_PAPER_STRATEGIES
    assert MICROPRICE_REVERSION_STRATEGY not in LIVE_FORWARDABLE_PAPER_STRATEGIES


def test_stale_book_resets_confirmation_streak():
    engine, store = _engine_and_store()
    base_ns = 20_000_000_000

    store.maybe_enter_m_series(
        _snapshot(sequence=1, received_monotonic_ns=base_ns),
        200,
        realtime_context=_context(1, base_ns),
    )
    store.maybe_enter_m_series(
        _snapshot(
            sequence=2,
            received_monotonic_ns=base_ns + 200_000_000,
            down_midpoint_shift=0.001,
            book_age_ms=501.0,
        ),
        200,
        realtime_context=_context(2, base_ns + 200_000_000),
    )

    assert store.opened == []
    state = engine.state()["micropriceVariants"]
    assert state["currentConfirmations"] == 0
    assert state["rejections"]["staleBook"] == 1


def test_pair_is_opened_only_once_per_market():
    _, store = _engine_and_store()
    base_ns = 30_000_000_000

    for sequence, shift in ((1, 0.0), (2, 0.001)):
        store.maybe_enter_m_series(
            _snapshot(
                sequence=sequence,
                received_monotonic_ns=base_ns + sequence * 200_000_000,
                down_midpoint_shift=shift,
                up_midpoint_shift=-shift,
            ),
            200,
            realtime_context=_context(
                sequence,
                base_ns + sequence * 200_000_000,
            ),
        )

    assert len(store.opened) == 2

    store.maybe_enter_m_series(
        _snapshot(
            sequence=3,
            received_monotonic_ns=base_ns + 800_000_000,
            down_midpoint_shift=0.005,
            up_midpoint_shift=-0.005,
        ),
        200,
        realtime_context=_context(3, base_ns + 800_000_000),
    )
    assert len(store.opened) == 2
