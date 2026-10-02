from __future__ import annotations

from tools.replay_wallet_spot_strike_maker import (
    MakerCandidate,
    MakerPolicy,
    evaluate_policy,
    first_fresh_ask_touch,
)


def test_first_fresh_ask_touch_rejects_preplacement_stale_and_postcancel_rows() -> None:
    rows = [
        {"sampled_at_ms": 900, "seconds_left": 60, "receipt_age_ms": 1, "up_ask": 0.5},
        {"sampled_at_ms": 1100, "seconds_left": 50, "receipt_age_ms": 4000, "up_ask": 0.5},
        {"sampled_at_ms": 1200, "seconds_left": 5, "receipt_age_ms": 1, "up_ask": 0.5},
        {"sampled_at_ms": 1300, "seconds_left": 40, "receipt_age_ms": 1, "up_ask": 0.61},
        {"sampled_at_ms": 1400, "seconds_left": 30, "receipt_age_ms": 1, "up_ask": 0.60},
    ]
    fill = first_fresh_ask_touch(
        rows, placed_at_ms=1000, cancel_seconds=7, side="UP", quote=0.60, max_age_ms=3000
    )
    assert fill is not None
    assert fill["sampled_at_ms"] == 1400


def test_maker_policy_uses_quote_cost_and_official_winner() -> None:
    rows = [
        MakerCandidate(1, 11, 60, 1000, "UP", 2, 0, 0.60, 1, 1100, 50, 0.60, "UP"),
        MakerCandidate(2, 12, 60, 2000, "DOWN", -2, 0, 0.70, 1, 2100, 50, 0.70, "UP"),
        MakerCandidate(3, 13, 60, 3000, "UP", 2, 0, 0.95, 1, 3100, 50, 0.95, "UP"),
    ]
    result = evaluate_policy(rows, MakerPolicy(60, 0, 0.90, 0))
    assert result["fills"] == 2
    assert result["wins"] == 1
    assert result["roi"] < 0
