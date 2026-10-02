from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_fragment_ebm_v272 as mod


def _row(ms: int, mid: float | None, pressure_1s: float = 1.0) -> dict:
    row = {
        "market_id": 7,
        "sample_ms": ms,
        "micro_prediction_up_mid": mid,
        "micro_book_pressure_mean_1s": pressure_1s,
        "micro_book_pressure_mean_3s": pressure_1s,
        "micro_book_pressure_mean_5s": pressure_1s,
    }
    for h in (1, 3, 5):
        row[f"micro_prediction_delta_{h}s"] = 999.0
        row[f"micro_prediction_range_{h}s"] = 999.0
        row[f"micro_prediction_pressure_aligned_delta_{h}s"] = 999.0
    return row


def test_ab_specs_differ_only_by_prediction_features():
    specs = mod.feature_specs()
    control = set(specs["CORE_STATE_PUBLIC_NO_PREDICTION"])
    treatment = set(specs["CORE_STATE_PUBLIC_8778_PREDICTION"])
    assert control < treatment
    assert treatment - control
    assert all(mod._is_prediction_feature(name) for name in treatment - control)
    assert not any(mod._is_prediction_feature(name) for name in control)
    assert not any("within_" in name for name in treatment)
    assert not any(name.startswith("next_taker") for name in treatment)


def test_fresh_mid_requires_explicit_fresh_2s():
    assert mod._fresh_mid({"fresh_2s": True, "prediction_mid": 0.61}) == 0.61
    assert mod._fresh_mid({"fresh_2s": False, "prediction_mid": 0.61}) is None
    assert mod._fresh_mid(None) is None


def test_prediction_history_uses_same_market_fresh_grid_and_includes_current():
    rows = [_row(1000, 0.40), _row(2000, 0.45), _row(3000, 0.55)]
    mod._prediction_history_features(rows)
    last = rows[-1]
    assert math.isclose(last["micro_prediction_delta_1s"], 0.10)
    assert math.isclose(last["micro_prediction_range_1s"], 0.10)
    assert math.isclose(last["micro_prediction_pressure_aligned_delta_1s"], 0.10)


def test_prediction_history_blanks_when_coverage_is_too_sparse():
    rows = [_row(1000, 0.40), _row(3000, 0.55)]
    mod._prediction_history_features(rows)
    last = rows[-1]
    assert last["micro_prediction_delta_1s"] is None
    assert last["micro_prediction_range_1s"] is None


def test_prediction_history_never_crosses_target_market():
    rows = [_row(1000, 0.40), _row(2000, 0.50)]
    other = _row(1500, 0.99)
    other["market_id"] = 99
    rows.insert(1, other)
    mod._prediction_history_features(rows)
    assert math.isclose(rows[-1]["micro_prediction_delta_1s"], 0.10)


def test_metric_delta_signs_are_treatment_minus_control():
    control = {"oof": {"auc": 0.60, "logLoss": 0.70}}
    treatment = {"oof": {"auc": 0.65, "logLoss": 0.66}}
    got = mod._metric_delta(treatment, control)
    assert math.isclose(got["deltaAucTreatmentMinusControl"], 0.05)
    assert math.isclose(got["deltaLogLossTreatmentMinusControl"], -0.04)
