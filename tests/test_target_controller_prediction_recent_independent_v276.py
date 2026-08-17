from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_prediction_recent_independent_v276 as mod


def test_bucket_bounds_are_five_minute_aligned():
    start, end = mod._bucket_bounds(300_001)
    assert start == 300_000
    assert end == 600_000


def test_support_gate_requires_micro_and_prediction_joint_support():
    meta = [{"marketId": 10, "bucketStartMs": 1_000_000}, {"marketId": 11, "bucketStartMs": 700_000}]
    states = [{"market_id": 10} for _ in range(40)] + [{"market_id": 11} for _ in range(40)]
    joined = [{"market_id": 10} for _ in range(35)] + [{"market_id": 11} for _ in range(31)]
    complete = [{"market_id": 10} for _ in range(30)] + [{"market_id": 11} for _ in range(19)]
    rows = {r["marketId"]: r for r in mod._support_report(meta, states, joined, complete)}
    assert rows[10]["jointEligible"] is True
    assert rows[11]["jointEligible"] is False


def test_select_model_markets_uses_newest_eligible_only():
    markets = [
        {"marketId": 1, "bucketStartMs": 100, "jointEligible": True},
        {"marketId": 2, "bucketStartMs": 300, "jointEligible": True},
        {"marketId": 3, "bucketStartMs": 200, "jointEligible": False},
        {"marketId": 4, "bucketStartMs": 250, "jointEligible": True},
    ]
    selected = mod._select_model_markets(markets, 2)
    assert [x["marketId"] for x in selected] == [2, 4]


def test_pair_signal_requires_pooled_and_majority_fold_improvement():
    pair = {
        "deltaAucTreatmentMinusControl": 0.05,
        "deltaLogLossTreatmentMinusControl": -0.04,
        "foldDeltas": [
            {"deltaAucTreatmentMinusControl": 0.10, "deltaLogLossTreatmentMinusControl": -0.05},
            {"deltaAucTreatmentMinusControl": 0.02, "deltaLogLossTreatmentMinusControl": -0.01},
            {"deltaAucTreatmentMinusControl": 0.03, "deltaLogLossTreatmentMinusControl": -0.02},
            {"deltaAucTreatmentMinusControl": -0.01, "deltaLogLossTreatmentMinusControl": 0.01},
        ],
    }
    result = mod._pair_signal(pair)
    assert result["orientationReplicated"] is True
    pair["foldDeltas"][2] = {"deltaAucTreatmentMinusControl": -0.03, "deltaLogLossTreatmentMinusControl": 0.02}
    assert mod._pair_signal(pair)["orientationReplicated"] is False
