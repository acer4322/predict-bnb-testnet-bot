from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import build_target_taker_action_onset_preflight_v1 as mod


def _public(ms: int) -> dict:
    return {
        "market_id": 7,
        "decision_sampled_at_ms": ms,
        "macro_phase": "MID",
        "seconds_left": 120,
    }


def _burst(kind: str, side: str = "UP", mixed: bool = False) -> dict:
    return {
        "market_id": 7,
        "burst_onset_ms": 5000,
        "burst_type": kind,
        "side": side,
        "mixed_sides": mixed,
    }


def test_strict_past_join_forbids_same_timestamp_snapshot() -> None:
    index = mod._index_public([_public(3000), _public(4000), _public(5000)])
    row, lead = mod._strict_past_join(index, 7, 5000)
    assert row is not None
    assert row["decision_sampled_at_ms"] == 4000
    assert lead == 1000


def test_strict_past_join_returns_none_without_prior_snapshot() -> None:
    index = mod._index_public([_public(5000), _public(6000)])
    row, lead = mod._strict_past_join(index, 7, 5000)
    assert row is None
    assert lead is None


def test_transition_labels_are_hierarchical() -> None:
    same = mod._labels(_burst("SAME_SIDE_REENTRY", "UP"))
    flip = mod._labels(_burst("SIDE_FLIP", "DOWN"))
    mixed = mod._labels(_burst("MIXED", "", True))
    first = mod._labels(_burst("FIRST_ENTRY", "UP"))

    assert same["clean_mixed_label"] == "CLEAN"
    assert same["transition_label"] == "SAME"
    assert flip["transition_label"] == "FLIP"
    assert mixed["clean_mixed_label"] == "MIXED"
    assert mixed["transition_label"] == ""
    assert first["is_first_burst"] == 1
    assert first["transition_label"] == ""


def test_cap3_exact_onset_audit_does_not_force_unmatched_bursts() -> None:
    cap2 = [
        {"market_id": 1, "burst_onset_ms": 1000, "burst_type": "SAME_SIDE_REENTRY", "side": "UP", "mixed_sides": False},
        {"market_id": 1, "burst_onset_ms": 3000, "burst_type": "SIDE_FLIP", "side": "DOWN", "mixed_sides": False},
    ]
    cap3 = [
        {"market_id": 1, "burst_onset_ms": 1000, "burst_type": "SAME_SIDE_REENTRY", "side": "UP", "mixed_sides": False},
    ]
    audit = mod._cap3_exact_audit(cap2, cap3)
    assert audit["exactOnsetMatches"] == 1
    assert audit["cap2ExactOnsetMatchRate"] == 0.5
    assert audit["transitionAgreement"] == 1.0
