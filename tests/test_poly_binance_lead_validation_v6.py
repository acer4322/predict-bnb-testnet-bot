from __future__ import annotations

from predict_bot.cross_oracle_strategy_chop_guard_v6 import (
    LEAD_MAX_PAIR_LAG_MS,
    _crossings,
    _pair_crossings,
    _wilson,
)


def test_poly_reaches_same_price_first_reports_positive_lead_seconds():
    events = _pair_crossings(
        [1_000],
        [2_400],
        market_id=101,
        poly_slug="btc-updown-5m-1",
        side="UP",
        threshold=0.70,
    )
    assert len(events) == 1
    assert events[0]["matched"] is True
    assert events[0]["leader"] == "POLY"
    assert events[0]["signedPolyLeadSeconds"] == 1.4


def test_binance_reaches_same_price_first_reports_negative_poly_lead():
    events = _pair_crossings(
        [3_000],
        [1_500],
        market_id=102,
        poly_slug="btc-updown-5m-2",
        side="DOWN",
        threshold=0.80,
    )
    assert len(events) == 1
    assert events[0]["matched"] is True
    assert events[0]["leader"] == "BINANCE"
    assert events[0]["signedPolyLeadSeconds"] == -1.5


def test_crossing_rearms_only_after_price_moves_back_below_hysteresis():
    series = [
        (1_000, 0.60),
        (2_000, 0.71),
        (3_000, 0.72),
        (4_000, 0.68),
        (5_000, 0.66),
        (6_000, 0.71),
    ]
    assert _crossings(series, side="UP", threshold=0.70) == [2_000, 6_000]


def test_down_side_uses_complement_probability_and_detects_reversal_move():
    # UP mid falling from 0.45 to 0.28 means DOWN selected probability rises
    # from 0.55 to 0.72 and crosses the DOWN 0.70 milestone.
    series = [
        (1_000, 0.45),
        (2_000, 0.40),
        (3_000, 0.28),
    ]
    assert _crossings(series, side="DOWN", threshold=0.70) == [3_000]


def test_crossings_farther_apart_than_pair_window_are_not_called_a_lead_match():
    events = _pair_crossings(
        [1_000],
        [1_000 + LEAD_MAX_PAIR_LAG_MS + 1],
        market_id=103,
        poly_slug="btc-updown-5m-3",
        side="UP",
        threshold=0.90,
    )
    assert [event["leader"] for event in events] == ["POLY_ONLY", "BINANCE_ONLY"]
    assert all(event["matched"] is False for event in events)


def test_wilson_probability_keeps_exact_observed_rate_and_bounded_interval():
    result = _wilson(7, 10)
    assert result["probability"] == 0.7
    assert 0.0 <= float(result["lower95"]) < 0.7
    assert 0.7 < float(result["upper95"]) <= 1.0
