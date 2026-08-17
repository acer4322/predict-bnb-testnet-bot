from __future__ import annotations

from tools.analyze_target_pressure_pinning_v25 import window_metrics


def _snap(ms: int, market: int, score: float, spot: float, fut: float, sqi: float = 0.7, fqi: float = 0.6):
    return {
        "timestamp_ns": ms * 1_000_000,
        "market_id": market,
        "direction_score": score,
        "spot_price": spot,
        "futures_price": fut,
        "spot_queue_imbalance": sqi,
        "futures_queue_imbalance": fqi,
    }


def test_persistent_pressure_with_static_price_is_pinned():
    rows = [_snap(ms, 7, 0.6, 100.0, 100.0) for ms in range(0, 3000, 250)]
    ts = [r["timestamp_ns"] for r in rows]
    out = window_metrics(rows, ts, 3000, 7, "UP", 3000)
    assert out["usable"] is True
    assert out["persistentPressure"] is True
    assert out["persistentPressurePinnedByRangeBps"]["0.5"] is True
    assert out["alignedDirectionScoreMean"] > 0.5


def test_direction_alignment_flips_for_down():
    rows = [_snap(ms, 7, 0.6, 100.0, 100.0) for ms in range(0, 3000, 250)]
    ts = [r["timestamp_ns"] for r in rows]
    out = window_metrics(rows, ts, 3000, 7, "DOWN", 3000)
    assert out["usable"] is True
    assert out["alignedDirectionScoreMean"] < -0.5
    assert out["persistentPressure"] is False


def test_large_price_range_is_not_pinned_even_with_pressure():
    rows = []
    for i, ms in enumerate(range(0, 3000, 250)):
        px = 100.0 + i * 0.01
        rows.append(_snap(ms, 7, 0.7, px, px))
    ts = [r["timestamp_ns"] for r in rows]
    out = window_metrics(rows, ts, 3000, 7, "UP", 3000)
    assert out["persistentPressure"] is True
    assert out["persistentPressurePinnedByRangeBps"]["2.0"] is False


def test_history_does_not_cross_micro_market_rollover():
    rows = [_snap(ms, 1 if ms < 1500 else 2, 0.7, 100.0, 100.0) for ms in range(0, 3000, 250)]
    ts = [r["timestamp_ns"] for r in rows]
    out = window_metrics(rows, ts, 3000, 2, "UP", 3000)
    assert out["usable"] is False
    assert out["reason"] == "LOW_HISTORY_COVERAGE"
