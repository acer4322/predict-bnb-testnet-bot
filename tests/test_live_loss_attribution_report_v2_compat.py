from __future__ import annotations

from predict_bot import live_loss_attribution_report_v2 as v2


def test_late_trajectory_accepts_sparse_sample_inside_1500ms_tolerance() -> None:
    signal_at_ms = 1_000_000
    rows = [
        {
            "observed_at_ms": signal_at_ms + 10_000 + 1_250,
            "poly_received_at_ms": signal_at_ms + 10_000 + 1_240,
            "binance_observed_at_ms": signal_at_ms + 10_000 + 1_250,
            "poly_up_mid": 0.62,
            "binance_up_mid": 0.60,
            "seconds_left_skew": 0.0,
            "binance_book_age_ms": 25.0,
            "binance_book_skew_ms": 5.0,
        }
    ]
    round_row = {
        "entrySignalAtMs": signal_at_ms,
        "side": "UP",
        "trajectory": {
            "atSignal": {
                "polySelectedMid": 0.70,
                "binanceSelectedMid": 0.68,
            }
        },
    }

    result = v2._late_trajectory(rows, round_row)

    assert result["plus10s"] is not None
    assert result["plus10s"]["nearestDeltaMs"] == 1_250
    assert result["plus10s"]["polySelectedDeltaFromSignal"] == -0.08
    assert result["plus10s"]["binanceSelectedDeltaFromSignal"] == -0.08
