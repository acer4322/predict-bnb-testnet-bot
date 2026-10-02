from __future__ import annotations

from predict_bot.target_taker_direct_eligibility_special_regime_v1 import (
    _bias_persistence,
    _cross_timestamps,
    _special_features,
)


def _row(ms: int, distance: float, up_mid: float, spot: float, futures: float) -> dict:
    return {
        "sampled_at_ms": ms,
        "spot_minus_strike_bps": distance,
        "predict_up_mid": up_mid,
        "spot_price": spot,
        "futures_price": futures,
        "chainlink_price": spot,
        "spot_return_1s_bps": 0.0,
        "spot_return_3s_bps": 0.0,
        "spot_return_5s_bps": 0.0,
        "futures_return_1s_bps": 0.0,
        "futures_return_3s_bps": 0.0,
        "futures_return_5s_bps": 0.0,
    }


def test_cross_count_and_last_cross_features() -> None:
    rows = [
        _row(1_000, -0.01, 0.60, 100.0, 100.0),
        _row(2_000, +0.01, 0.61, 100.1, 100.1),
        _row(3_000, +0.02, 0.62, 100.2, 100.2),
        _row(4_000, -0.02, 0.63, 100.1, 100.1),
    ]
    assert _cross_timestamps(rows) == [2_000, 4_000]
    features = _special_features(rows, 3)
    assert features["strike_cross_count_10s"] == 2
    assert features["ms_since_last_strike_cross"] == 0


def test_bias_persistence_follows_current_prediction_side() -> None:
    rows = [
        _row(1_000, 0.01, 0.60, 100.0, 100.0),
        _row(2_000, 0.01, 0.62, 100.0, 100.0),
        _row(3_000, 0.01, 0.40, 100.0, 100.0),
        _row(4_000, 0.01, 0.65, 100.0, 100.0),
    ]
    assert _bias_persistence(rows) == 0.75
