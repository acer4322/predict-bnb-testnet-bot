from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_fragment_ebm_v27 as mod


def _snap(ms: int, market: int, spot_qi: float, fut_qi: float) -> dict:
    return {
        "timestamp_ns": ms * 1_000_000,
        "market_id": market,
        "spot_price": 100.0,
        "futures_price": 100.0,
        "spot_queue_imbalance": spot_qi,
        "futures_queue_imbalance": fut_qi,
        "spot_taker_imbalance_250ms": None,
        "futures_taker_imbalance_250ms": None,
        "spot_taker_imbalance_1s": None,
        "futures_taker_imbalance_1s": None,
        "prediction_up_mid": 0.5,
        "direction_score": 0.0,
    }


def test_strict_past_excludes_equal_timestamp_and_enforces_freshness():
    rows = [_snap(1000, 1, 0.1, 0.1), _snap(2000, 1, 0.2, 0.2)]
    ts = [r["timestamp_ns"] for r in rows]
    snap, status = mod.strict_past_snapshot(rows, ts, 2000, freshness_ms=1500)
    assert status == "OK"
    assert snap is not None
    assert snap["timestamp_ns"] == 1_000_000_000

    snap, status = mod.strict_past_snapshot(rows, ts, 3000, freshness_ms=750)
    assert snap is None
    assert status == "STALE"


def test_history_features_never_cross_micro_market_rollover():
    rows = [
        _snap(2000, 11, 0.5, 0.5),
        _snap(2400, 99, -1.0, -1.0),
        _snap(2800, 11, 0.5, 0.5),
    ]
    ts = [r["timestamp_ns"] for r in rows]
    got = mod._history_features(rows, ts, sample_ms=3000, micro_market_id=11, history_ms=1000)
    assert math.isclose(got["micro_book_pressure_mean_1s"], 0.5)
    assert math.isclose(got["micro_book_pressure_sign_consistency_1s"], 1.0)


def test_core_spec_excludes_post_treatment_and_future_labels():
    specs = mod.feature_specs()
    core = specs["CORE_STATE_PUBLIC"]
    full = specs["FULL_DESCRIPTIVE"]
    assert "risk_deficit" in core
    assert "micro_prediction_up_mid" in core
    assert "time_since_last_taker_ms" not in core
    assert "max_risk_since_taker_reset" not in core
    assert "time_since_last_taker_ms" in full
    assert not any("within_" in field for field in core)
    assert not any(field.startswith("next_taker") for field in core)


def test_leave_one_market_out_never_leaks_holdout_market():
    rows = [
        {"market_id": 10}, {"market_id": 10},
        {"market_id": 20}, {"market_id": 20},
        {"market_id": 30},
    ]
    folds = mod.build_leave_one_market_out(rows)
    assert [f[0] for f in folds] == [10, 20, 30]
    for holdout, train_idx, test_idx in folds:
        assert all(rows[i]["market_id"] != holdout for i in train_idx)
        assert all(rows[i]["market_id"] == holdout for i in test_idx)
        assert set(train_idx).isdisjoint(test_idx)


def test_prediction_freshness_is_strict_past():
    events = {7: [1_000_000_000, 2_000_000_000]}
    # At exactly 2000ms, the 2000ms event is not allowed; the 1000ms event is used.
    assert mod._prediction_age_ms(events, 7, 2000) == 1000.0
