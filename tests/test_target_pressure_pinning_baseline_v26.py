from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_pressure_pinning_baseline_v26 as v26


def _snap(ms: int, market: int, score: float, spot: float, fut: float) -> dict:
    return {
        "timestamp_ns": ms * 1_000_000,
        "market_id": market,
        "direction_score": score,
        "spot_price": spot,
        "futures_price": fut,
        "spot_queue_imbalance": score,
        "futures_queue_imbalance": score,
        "spot_taker_imbalance_250ms": None,
        "futures_taker_imbalance_250ms": None,
        "spot_taker_imbalance_1s": None,
        "futures_taker_imbalance_1s": None,
        "prediction_up_mid": 0.5,
    }


def test_pressure_metrics_selects_down_direction_and_uses_past_only():
    snaps = [
        _snap(1000, 7, -0.5, 100.00, 100.00),
        _snap(1250, 7, -0.6, 100.00, 100.00),
        _snap(1500, 7, -0.7, 100.00, 100.00),
        _snap(1750, 7, -0.8, 100.00, 100.00),
        _snap(2000, 7, +1.0, 101.00, 101.00),  # equal-time/future must not enter [1000,2000)
    ]
    ts = [r["timestamp_ns"] for r in snaps]
    out = v26._pressure_metrics(snaps, ts, 2000, 7, 1000)
    assert out["usable"] is True
    assert out["pressureDirection"] == "DOWN"
    assert out["persistentPressure"] is True
    assert out["maxRangeBps"] == 0.0
    assert out["noFollowThrough"]["0.1"] is True


def test_next_add_direction_is_strict_future_and_same_target_market_segment():
    groups = {
        (1, 2): (
            [1000, 1500, 2200],
            [
                {"first_event_ms": 1000, "purpose": "ADD", "exposure_direction": "UP"},
                {"first_event_ms": 1500, "purpose": "REPAIR", "exposure_direction": "DOWN"},
                {"first_event_ms": 2200, "purpose": "ADD", "exposure_direction": "DOWN"},
            ],
        )
    }
    assert v26._next_add_direction(groups, 1, 2, 1000, 2) == "DOWN"
    assert v26._next_add_direction(groups, 1, 99, 1000, 2) is None
    assert v26._next_add_direction(groups, 1, 2, 1000, 1) is None


def test_condition_summary_reports_prevalence_lift_and_direction_match():
    rows = [
        {"add_within_5s": 1, "h1000_pressureDirection": "UP", "next_add_direction_5s": "UP"},
        {"add_within_5s": 0, "h1000_pressureDirection": "DOWN", "next_add_direction_5s": None},
        {"add_within_5s": 0, "h1000_pressureDirection": "UP", "next_add_direction_5s": None},
        {"add_within_5s": 0, "h1000_pressureDirection": "DOWN", "next_add_direction_5s": None},
    ]
    out = v26._condition_summary(rows, rows[:2], 1000)
    assert out["states"] == 2
    assert out["prevalence"] == 0.5
    assert out["addWithin5s"]["baselineRate"] == 0.25
    assert out["addWithin5s"]["conditionRate"] == 0.5
    assert out["addWithin5s"]["liftVsBaseline"] == 2.0
    assert out["addWithin5s"]["directionMatchRate"] == 1.0
