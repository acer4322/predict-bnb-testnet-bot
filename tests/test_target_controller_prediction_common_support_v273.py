from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_prediction_common_support_v273b as mod


def test_complete_case_removes_missing_prediction_values():
    rows = [
        {"market_id": 1, "micro_prediction_up_mid": 0.4, "micro_prediction_distance_05": 0.1},
        {"market_id": 1, "micro_prediction_up_mid": None, "micro_prediction_distance_05": None},
    ]
    got = mod.complete(rows, ["micro_prediction_up_mid", "micro_prediction_distance_05"])
    assert len(got) == 1
    assert got[0]["micro_prediction_up_mid"] == 0.4


def test_current_value_arm_excludes_event_age():
    assert "micro_prediction_up_mid" in mod.CURRENT_VALUE_FEATURES
    assert "micro_prediction_distance_05" in mod.CURRENT_VALUE_FEATURES
    assert "micro_prediction_event_age_ms" not in mod.CURRENT_VALUE_FEATURES


def test_pair_summary_uses_same_holdout_ids_and_correct_deltas():
    control = {
        "oof": {"auc": 0.5, "logLoss": 0.8},
        "folds": [{"holdoutTargetMarketId": 10, "status": "OK", "auc": 0.4, "logLoss": 0.9, "trainRows": 90, "trainPositives": 9, "testRows": 10, "testPositives": 1}],
    }
    treatment = {
        "oof": {"auc": 0.6, "logLoss": 0.7},
        "folds": [{"holdoutTargetMarketId": 10, "status": "OK", "auc": 0.55, "logLoss": 0.75, "trainRows": 90, "trainPositives": 9, "testRows": 10, "testPositives": 1}],
    }
    got = mod.pair_summary(control, treatment)
    assert math.isclose(got["deltaAucTreatmentMinusControl"], 0.1)
    assert math.isclose(got["deltaLogLossTreatmentMinusControl"], -0.1)
    assert got["foldDeltas"][0]["holdoutTargetMarketId"] == 10
    assert math.isclose(got["foldDeltas"][0]["deltaAucTreatmentMinusControl"], 0.15)
    assert math.isclose(got["foldDeltas"][0]["deltaLogLossTreatmentMinusControl"], -0.15)
