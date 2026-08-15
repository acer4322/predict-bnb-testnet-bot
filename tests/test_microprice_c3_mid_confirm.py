from predict_bot.m_realtime import LIVE_FORWARDABLE_PAPER_STRATEGIES
from predict_bot.microprice_c3_mid_confirm import (
    C3_MID_CONFIRM_STRATEGY,
    C3_MID_CONFIRM_VERSION,
    CONFIRM_DELAY_SECONDS,
    MAX_MIDPOINT_DELTA,
    MIN_MIDPOINT_DELTA,
    MIN_RETAINED_STRENGTH,
    MicropriceC3MidConfirmTracker,
)


class _Engine:
    prediction_event = {
        "prediction_data_source": "dual_token_rest",
        "direct_outcome_books": True,
    }


class _Store:
    def __init__(self):
        self.opened = []

    def open_trade(self, **kwargs):
        self.opened.append(kwargs)


def _snapshot(*, shift=0.0, weak=False):
    if weak:
        up_bid_size, up_ask_size = 65.0, 35.0
        down_bid_size, down_ask_size = 35.0, 65.0
    else:
        up_bid_size, up_ask_size = 200.0, 30.0
        down_bid_size, down_ask_size = 30.0, 200.0
    return {
        "timestamp": "2026-08-08T00:00:00+00:00",
        "topic_id": 7,
        "market_id": 11,
        "seconds_left": 176.0,
        "up_bid": 0.30 + shift,
        "up_ask": 0.32 + shift,
        "up_bid_size": up_bid_size,
        "up_ask_size": up_ask_size,
        "down_bid": 0.68 - shift,
        "down_ask": 0.70 - shift,
        "down_bid_size": down_bid_size,
        "down_ask_size": down_ask_size,
        "book_age_ms": 100.0,
        "book_skew_ms": 20.0,
    }


def _context(ns):
    return {
        "trigger_source": "prediction",
        "execution_eligible": True,
        "prediction_data_source": "dual_token_rest",
        "market_data_integrity_ok": True,
        "received_monotonic_ns": ns,
    }


def _source_open():
    return [{
        "strategy": "R_MICROPRICE",
        "side": "UP",
        "entry_price": 0.3216,
        "stake": 5.0,
    }]


def test_frozen_c3_midpoint_parameters_and_live_isolation():
    assert C3_MID_CONFIRM_VERSION == "MICROPRICE_C3_MID_CONFIRM_V1"
    assert CONFIRM_DELAY_SECONDS == 3.0
    assert MIN_RETAINED_STRENGTH == 0.60
    assert MIN_MIDPOINT_DELTA == 0.005
    assert MAX_MIDPOINT_DELTA == 0.020
    assert C3_MID_CONFIRM_STRATEGY not in LIVE_FORWARDABLE_PAPER_STRATEGIES


def test_opens_once_after_three_second_retained_midpoint_confirmation():
    store = _Store()
    tracker = MicropriceC3MidConfirmTracker(_Engine(), store)
    base = 10_000_000_000

    assert tracker.process(_snapshot(), 200, _context(base), _source_open()) == []
    assert tracker.process(_snapshot(shift=0.004), 200, _context(base + 2_900_000_000), []) == []

    opened = tracker.process(
        _snapshot(shift=0.010),
        200,
        _context(base + 3_100_000_000),
        [],
    )

    assert len(opened) == 1
    assert opened[0]["strategy"] == C3_MID_CONFIRM_STRATEGY
    assert opened[0]["side"] == "UP"
    assert len(store.opened) == 1
    trade = store.opened[0]
    assert trade["strategy"] == C3_MID_CONFIRM_STRATEGY
    assert trade["strategy_version"] == C3_MID_CONFIRM_VERSION
    assert trade["diagnostics"]["paper_only"] is True
    assert trade["diagnostics"]["live_orders_affected"] is False
    assert 0.005 <= trade["diagnostics"]["confirmation"]["midpointDelta"] < 0.020
    assert trade["diagnostics"]["confirmation"]["retainedStrength"] >= 0.60


def test_midpoint_move_at_upper_bound_is_blocked_without_retry():
    store = _Store()
    tracker = MicropriceC3MidConfirmTracker(_Engine(), store)
    base = 20_000_000_000
    tracker.process(_snapshot(), 200, _context(base), _source_open())

    assert tracker.process(
        _snapshot(shift=0.020),
        200,
        _context(base + 3_050_000_000),
        [],
    ) == []
    assert store.opened == []
    assert tracker.last_decision["reason"] == "MIDPOINT_MOVE_TOO_LARGE"
    assert tracker.anchor is None

    # First post-3s event is the frozen decision point; a later nicer quote may not retry.
    assert tracker.process(
        _snapshot(shift=0.010),
        200,
        _context(base + 3_500_000_000),
        [],
    ) == []
    assert store.opened == []


def test_retained_strength_below_sixty_percent_is_blocked():
    store = _Store()
    tracker = MicropriceC3MidConfirmTracker(_Engine(), store)
    base = 30_000_000_000
    tracker.process(_snapshot(), 200, _context(base), _source_open())

    assert tracker.process(
        _snapshot(shift=0.010, weak=True),
        200,
        _context(base + 3_100_000_000),
        [],
    ) == []
    assert store.opened == []
    assert tracker.last_decision["reason"] == "RETAINED_STRENGTH"


def test_never_creates_anchor_without_actual_source_open():
    store = _Store()
    tracker = MicropriceC3MidConfirmTracker(_Engine(), store)
    base = 40_000_000_000

    tracker.process(_snapshot(), 200, _context(base), [])
    tracker.process(_snapshot(shift=0.010), 200, _context(base + 3_100_000_000), [])

    assert tracker.anchor is None
    assert store.opened == []
