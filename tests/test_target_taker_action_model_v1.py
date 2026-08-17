from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

SPEC = importlib.util.spec_from_file_location(
    "target_taker_action_model_v1",
    TOOLS / "train_target_taker_action_model_v1.py",
)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _base_row(**overrides):
    row = {
        "market_id": 1,
        "burst_onset_ms": 1000,
        "burst_index": 1,
        "region": "ORDINARY_PRE_SPECIAL",
        "strict_past_within_2s": 1,
        "is_first_burst": 0,
        "clean_mixed_label": "CLEAN",
        "transition_label": "SAME",
        "clean_side_label": "UP",
        "macro_phase": "MID",
        "seconds_left": 120.0,
        "predict_up_mid": 0.7,
        "predict_up_spread": 0.04,
        "predict_down_spread": 0.06,
        "spot_minus_strike_bps": 2.0,
        "chainlink_minus_strike_bps": 3.0,
        "direction_score": 0.5,
        "spot_queue_imbalance": 0.2,
        "spot_taker_imbalance_1s": 0.3,
        "spot_return_1s_bps": 1.0,
        "spot_return_3s_bps": 2.0,
        "futures_queue_imbalance": 0.4,
        "futures_taker_imbalance_1s": 0.5,
        "futures_return_1s_bps": 1.5,
        "futures_return_3s_bps": 2.5,
        "signal_age_ms": 50.0,
    }
    row.update(overrides)
    return row


def test_prior_clean_side_ignores_mixed_bursts():
    frame = pd.DataFrame(
        [
            _base_row(burst_onset_ms=1000, burst_index=1, is_first_burst=1, clean_side_label="UP"),
            _base_row(
                burst_onset_ms=2000,
                burst_index=2,
                clean_mixed_label="MIXED",
                transition_label="",
                clean_side_label="",
            ),
            _base_row(
                burst_onset_ms=3000,
                burst_index=3,
                transition_label="FLIP",
                clean_side_label="DOWN",
            ),
            _base_row(
                burst_onset_ms=4000,
                burst_index=4,
                transition_label="FLIP",
                clean_side_label="UP",
            ),
        ]
    )
    out = mod._attach_prior_clean_side(frame, pd)
    assert out["prior_clean_side"].tolist() == ["", "UP", "UP", "DOWN"]


def test_prior_side_alignment_uses_previous_side_without_current_label():
    frame = pd.DataFrame(
        [
            _base_row(
                burst_onset_ms=1000,
                burst_index=1,
                is_first_burst=1,
                clean_side_label="DOWN",
            ),
            _base_row(
                burst_onset_ms=2000,
                burst_index=2,
                transition_label="SAME",
                clean_side_label="DOWN",
                predict_up_mid=0.70,
                predict_up_spread=0.04,
                predict_down_spread=0.06,
                direction_score=0.5,
                spot_minus_strike_bps=2.0,
            ),
        ]
    )
    out = mod._attach_prior_clean_side(frame, pd)
    row = out.iloc[1]
    assert row["prior_clean_side"] == "DOWN"
    assert row["prior_clean_side_up"] == 0.0
    assert abs(row["prior_side_predict_mid"] - 0.30) < 1e-12
    assert abs(row["prior_side_predict_spread"] - 0.06) < 1e-12
    assert abs(row["prior_aligned_direction_score"] + 0.5) < 1e-12
    assert abs(row["prior_aligned_spot_minus_strike_bps"] + 2.0) < 1e-12


def test_training_and_special_populations_are_separated():
    frame = pd.DataFrame(
        [
            _base_row(
                market_id=1,
                burst_onset_ms=1000,
                burst_index=1,
                is_first_burst=1,
                clean_side_label="UP",
                region="ORDINARY_PRE_SPECIAL",
            ),
            _base_row(
                market_id=1,
                burst_onset_ms=2000,
                burst_index=2,
                transition_label="FLIP",
                clean_side_label="DOWN",
                region="ORDINARY_PRE_SPECIAL",
            ),
            _base_row(
                market_id=2,
                burst_onset_ms=3000,
                burst_index=1,
                is_first_burst=1,
                clean_side_label="DOWN",
                region="POST_SPECIAL_START_AUDIT",
            ),
            _base_row(
                market_id=2,
                burst_onset_ms=4000,
                burst_index=2,
                transition_label="SAME",
                clean_side_label="DOWN",
                region="POST_SPECIAL_START_AUDIT",
            ),
        ]
    )
    prepared = mod._attach_prior_clean_side(frame, pd)
    ordinary = mod._task_frame(prepared, "stage2_same_vs_flip", pd)
    special = mod._special_task_frame(prepared, "stage2_same_vs_flip", pd)
    assert ordinary["market_id"].tolist() == [1]
    assert ordinary["label"].tolist() == [1]
    assert special["market_id"].tolist() == [2]
    assert special["label"].tolist() == [0]


def test_stage1_excludes_first_burst_and_labels_mixed_positive():
    frame = pd.DataFrame(
        [
            _base_row(
                burst_onset_ms=1000,
                burst_index=1,
                is_first_burst=1,
                clean_mixed_label="CLEAN",
            ),
            _base_row(
                burst_onset_ms=2000,
                burst_index=2,
                clean_mixed_label="MIXED",
                transition_label="",
                clean_side_label="",
            ),
            _base_row(
                burst_onset_ms=3000,
                burst_index=3,
                clean_mixed_label="CLEAN",
                transition_label="SAME",
                clean_side_label="UP",
            ),
        ]
    )
    prepared = mod._attach_prior_clean_side(frame, pd)
    stage1 = mod._task_frame(prepared, "stage1_clean_vs_mixed", pd)
    assert stage1["burst_index"].tolist() == [2, 3]
    assert stage1["label"].tolist() == [1, 0]
