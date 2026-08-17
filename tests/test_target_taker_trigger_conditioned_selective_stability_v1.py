from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_taker_trigger_conditioned_selective_stability_v1 as mod


def _row(fold: int, side: str, *, correct: bool) -> dict:
    target = side if correct else ("DOWN" if side == "UP" else "UP")
    return {
        "fold": fold,
        "market_id": fold,
        "sampled_ms": fold * 1000,
        "phase": "MID",
        "selected": True,
        "predicted_side": side,
        "success": 1,
        "next_is_subsequent": 1,
        "next_clean_mixed": "CLEAN",
        "next_burst_side": target,
        "next_onset_ms": fold * 1000 + 500,
    }


def test_fold_payload_keeps_fold_boundaries_and_direction_split():
    rows = [
        _row(1, "UP", correct=True),
        _row(1, "DOWN", correct=False),
        _row(2, "UP", correct=True),
        _row(2, "DOWN", correct=True),
    ]
    payload = mod._fold_payload(rows)
    assert set(payload) == {"1", "2"}
    assert payload["1"]["metrics"]["selectedActions"] == 2
    assert payload["2"]["metrics"]["correctSide"] == 2
    assert payload["1"]["byPredictedSide"]["UP"]["correctSide"] == 1
    assert payload["1"]["byPredictedSide"]["DOWN"]["correctSide"] == 0


def test_stability_summary_counts_up_beating_down():
    rows = [
        _row(1, "UP", correct=True),
        _row(1, "DOWN", correct=False),
        _row(2, "UP", correct=True),
        _row(2, "DOWN", correct=True),
    ]
    summary = mod._stability_summary(mod._fold_payload(rows))
    assert summary["upBeatsDown"]["comparableFolds"] == 2
    assert summary["upBeatsDown"]["upBetterFolds"] == 1
    assert summary["selectedActionsPerFold"]["total"] == 4
