from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_prediction_gap_oriented_v274 as mod


def test_positive_payoff_gap_orients_to_down_repair_side():
    row = {
        "payoff_gap": 10.0,
        "micro_prediction_up_mid": 0.70,
        "micro_prediction_delta_3s": 0.04,
    }
    got = mod.add_gap_oriented_prediction(row)
    assert got["prediction_repair_side"] == "DOWN"
    assert math.isclose(got["prediction_repair_side_mid"], 0.30)
    assert math.isclose(got["prediction_repair_side_delta_3s"], -0.04)


def test_negative_payoff_gap_orients_to_up_repair_side():
    row = {
        "payoff_gap": -10.0,
        "micro_prediction_up_mid": 0.70,
        "micro_prediction_delta_3s": 0.04,
    }
    got = mod.add_gap_oriented_prediction(row)
    assert got["prediction_repair_side"] == "UP"
    assert math.isclose(got["prediction_repair_side_mid"], 0.70)
    assert math.isclose(got["prediction_repair_side_delta_3s"], 0.04)


def test_balanced_gap_is_neutral_orientation():
    row = {
        "payoff_gap": 0.0,
        "micro_prediction_up_mid": 0.80,
        "micro_prediction_delta_3s": -0.03,
    }
    got = mod.add_gap_oriented_prediction(row)
    assert got["prediction_repair_side"] == "BALANCED"
    assert math.isclose(got["prediction_repair_side_mid"], 0.5)
    assert math.isclose(got["prediction_repair_side_delta_3s"], 0.0)


def test_oriented_arm_replaces_raw_directional_prediction_fields():
    added = [f for f in mod.ORIENTED_FEATURES if f not in mod.CONTROL]
    assert "prediction_repair_side_mid" in added
    assert "prediction_repair_side_delta_3s" in added
    assert "micro_prediction_up_mid" not in added
    assert "micro_prediction_delta_3s" not in added
    assert len(added) == len([f for f in mod.RAW_FEATURES if f not in mod.CONTROL])
