from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_taker_trigger_conditioned_selective_replay_v1 as mod


def _row(i: int, side: float, mixed: float = 0.1, *, phase: str = "MID"):
    return {
        "candidate": "TOP10_LEVEL_CD5",
        "fold": 1,
        "market_id": 1,
        "sampled_ms": 1000 + i * 1000,
        "phase": phase,
        "side_score": side,
        "stage1_score": mixed,
        "success": 1,
        "next_delta_ms": 1000,
        "next_onset_ms": 2000 + i * 1000,
        "next_is_subsequent": 1,
        "next_clean_mixed": "CLEAN",
        "next_burst_side": "UP",
    }


def test_causal_threshold_uses_prior_scores_only():
    rows = [_row(i, 0.1 + i * 0.01) for i in range(20)]
    rows.append(_row(20, 0.99))
    marked, _ = mod._causal_marks(
        rows,
        side_fraction=0.10,
        mixed_veto_fraction=None,
        window_rows=80,
        min_history_rows=20,
    )
    last = marked[-1]
    assert last["threshold_ready"] is True
    assert last["predicted_side"] == "UP"
    assert last["side_high_threshold"] < 0.99


def test_low_and_high_side_tails_emit_down_and_up():
    history = [_row(i, i / 20.0) for i in range(20)]
    rows = history + [_row(20, -0.1), _row(21, 1.1)]
    marked, _ = mod._causal_marks(
        rows,
        side_fraction=0.10,
        mixed_veto_fraction=None,
        window_rows=80,
        min_history_rows=20,
    )
    assert marked[-2]["predicted_side"] == "DOWN"
    assert marked[-2]["selected"] is True
    assert marked[-1]["predicted_side"] == "UP"
    assert marked[-1]["selected"] is True


def test_target_labels_do_not_change_selection():
    rows_a = [_row(i, i / 20.0) for i in range(20)] + [_row(20, 1.1)]
    rows_b = [dict(row) for row in rows_a]
    rows_b[-1].update({"success": 0, "next_is_subsequent": 0, "next_clean_mixed": "MIXED", "next_burst_side": "DOWN"})
    a, _ = mod._causal_marks(
        rows_a,
        side_fraction=0.10,
        mixed_veto_fraction=None,
        window_rows=80,
        min_history_rows=20,
    )
    b, _ = mod._causal_marks(
        rows_b,
        side_fraction=0.10,
        mixed_veto_fraction=None,
        window_rows=80,
        min_history_rows=20,
    )
    assert a[-1]["predicted_side"] == b[-1]["predicted_side"] == "UP"
    assert a[-1]["selected"] == b[-1]["selected"] is True


def test_high_mixed_risk_veto_blocks_otherwise_selected_side():
    rows = [_row(i, i / 20.0, mixed=i / 20.0) for i in range(20)]
    rows.append(_row(20, 1.1, mixed=1.1))
    marked, _ = mod._causal_marks(
        rows,
        side_fraction=0.10,
        mixed_veto_fraction=0.20,
        window_rows=80,
        min_history_rows=20,
    )
    last = marked[-1]
    assert last["predicted_side"] == "UP"
    assert last["mixed_vetoed"] is True
    assert last["selected"] is False
