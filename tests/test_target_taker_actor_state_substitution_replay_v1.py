from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "analyze_target_taker_actor_state_substitution_replay_v1.py"
SPEC = importlib.util.spec_from_file_location("target_taker_actor_state_substitution_replay_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _row(
    sampled_ms: int,
    *,
    score: float = 0.9,
    label: int = 1,
    onset_ms: int | None = None,
    target_active: int = 0,
    target_idle: int = 1,
    target_state: str = "POST_FIRST",
) -> dict:
    return {
        "fold": 1,
        "market_id": 1,
        "sampled_ms": sampled_ms,
        "seconds_left": 300.0 - sampled_ms / 1000.0,
        "phase": "MID",
        "score": score,
        "label": label,
        "next_delta_ms": (onset_ms - sampled_ms) if onset_ms is not None else None,
        "next_onset_ms": onset_ms,
        "target_position_state_audit": target_state,
        "target_burst_active_audit": target_active,
        "target_post_first_idle_audit": target_idle,
        "adaptive_threshold": 0.5,
        "above_threshold": score >= 0.5,
    }


def test_self_entry_and_cooldown_gate_without_target_truth() -> None:
    rows = [
        _row(1000, onset_ms=7000),
        _row(2000, onset_ms=7000),
        _row(3000, onset_ms=7000),
        _row(4000, onset_ms=7000),
        _row(5000, onset_ms=7000),
        _row(6000, onset_ms=7000),
        _row(7000, onset_ms=9000),
        _row(8000, onset_ms=9000),
    ]
    # Market starts at 1s. entryDelay=3s => our entry at 4s. CD2 => first possible hazard action at 6s.
    result = mod._run_actor_policy(rows, entry_delay_s=3, cooldown_s=2)
    assert result["triggers"] == 2
    assert result["successfulTriggers"] == 2
    assert result["targetTruthUsedForEligibility"] is False


def test_target_truth_changes_do_not_change_trigger_eligibility() -> None:
    base_rows = [
        _row(1000, onset_ms=5000),
        _row(2000, onset_ms=5000),
        _row(3000, onset_ms=5000),
        _row(4000, onset_ms=5000),
        _row(5000, onset_ms=8000),
        _row(6000, onset_ms=8000),
    ]
    changed = []
    for i, row in enumerate(base_rows):
        clone = dict(row)
        clone["target_burst_active_audit"] = 1 if i % 2 == 0 else 0
        clone["target_post_first_idle_audit"] = 0
        clone["target_position_state_audit"] = "PRE_FIRST"
        changed.append(clone)

    a = mod._run_actor_policy(base_rows, entry_delay_s=1, cooldown_s=2)
    b = mod._run_actor_policy(changed, entry_delay_s=1, cooldown_s=2)
    for key in (
        "triggers",
        "successfulTriggers",
        "triggerPrecision",
        "eligibleUniqueBursts",
        "capturedUniqueBursts",
        "uniqueBurstCaptureRate",
    ):
        assert a[key] == b[key]
    assert a["targetTruthAuditAtTrigger"] != b["targetTruthAuditAtTrigger"]


def test_adaptive_threshold_uses_only_prior_rows() -> None:
    rows = []
    for i in range(120):
        row = _row(1000 + i * 1000, score=0.1, label=0, onset_ms=None)
        row.pop("adaptive_threshold")
        row.pop("above_threshold")
        rows.append(row)
    current = _row(121000, score=0.0, label=0, onset_ms=None)
    current.pop("adaptive_threshold")
    current.pop("above_threshold")
    rows.append(current)

    marked, coverage = mod._adaptive_marks_by_fold(
        rows,
        fraction=0.10,
        window_rows=600,
        min_history_rows=120,
    )
    assert marked[-1]["adaptive_threshold"] == 0.1
    assert marked[-1]["above_threshold"] is False
    assert coverage["1"]["thresholdRows"] == 1
    assert coverage["1"]["warmupRows"] == 120


def test_threshold_history_resets_at_fold_boundary() -> None:
    rows = []
    for fold in (1, 2):
        for i in range(121):
            row = _row(1000 + i * 1000, score=0.2, label=0, onset_ms=None)
            row["fold"] = fold
            row["market_id"] = fold
            row.pop("adaptive_threshold")
            row.pop("above_threshold")
            rows.append(row)
    marked, coverage = mod._adaptive_marks_by_fold(
        rows,
        fraction=0.20,
        window_rows=600,
        min_history_rows=120,
    )
    assert coverage["1"]["warmupRows"] == 120
    assert coverage["2"]["warmupRows"] == 120
    assert sum(row["adaptive_threshold"] is not None for row in marked) == 2
