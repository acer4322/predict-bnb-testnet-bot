from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "analyze_target_taker_action_bursts_v1.py"
SPEC = importlib.util.spec_from_file_location("target_taker_action_bursts_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _event(ms: int, side: str, index: int) -> dict:
    return {
        "market_id": 1,
        "target_event_ms": ms,
        "side": side,
        "event_index": index,
        "parent_id": f"p{index}",
        "phase": "T120_060",
        "macro_phase": "MID",
        "event_type": "SAME_SIDE_REENTRY",
    }


def test_gap_zero_merges_same_second_only() -> None:
    events = [
        _event(1000, "UP", 1),
        _event(1000, "UP", 2),
        _event(2000, "UP", 3),
    ]
    bursts = mod._build_bursts(events, 0)
    assert [row["parent_count"] for row in bursts] == [2, 1]
    assert bursts[0]["burst_type"] == "FIRST_ENTRY"
    assert bursts[1]["burst_type"] == "SAME_SIDE_REENTRY"


def test_gap_one_second_chain_clusters_adjacent_parents() -> None:
    events = [
        _event(1000, "UP", 1),
        _event(2000, "UP", 2),
        _event(3000, "UP", 3),
        _event(5000, "UP", 4),
    ]
    bursts = mod._build_bursts(events, 1)
    assert [row["parent_count"] for row in bursts] == [3, 1]
    assert bursts[0]["burst_duration_ms"] == 2000


def test_mixed_burst_is_not_used_as_clean_direction_transition() -> None:
    events = [
        _event(1000, "UP", 1),
        _event(3000, "UP", 2),
        _event(3000, "DOWN", 3),
        _event(5000, "UP", 4),
        _event(7000, "DOWN", 5),
    ]
    bursts = mod._build_bursts(events, 0)
    assert [row["burst_type"] for row in bursts] == [
        "FIRST_ENTRY",
        "MIXED",
        "SAME_SIDE_REENTRY",
        "SIDE_FLIP",
    ]


def test_first_mixed_burst_leaves_later_clean_state_unknown() -> None:
    events = [
        _event(1000, "UP", 1),
        _event(1000, "DOWN", 2),
        _event(3000, "UP", 3),
        _event(5000, "UP", 4),
    ]
    bursts = mod._build_bursts(events, 0)
    assert [row["burst_type"] for row in bursts] == [
        "MIXED",
        "STATE_UNKNOWN",
        "SAME_SIDE_REENTRY",
    ]
