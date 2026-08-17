from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "tools" / "analyze_target_taker_action_bursts_v2.py"
spec = importlib.util.spec_from_file_location("target_bursts_v2", MODULE)
assert spec and spec.loader
v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2)


def event(ms: int, side: str = "UP") -> dict:
    return {
        "market_id": 1,
        "target_event_ms": ms,
        "side": side,
        "event_index": 1,
        "parent_id": str(ms),
        "phase": "T120_060",
        "macro_phase": "MID",
        "event_type": "SAME_SIDE_REENTRY",
    }


def test_onset_cap_breaks_adjacent_gap_chain() -> None:
    episode = [event(0), event(1000), event(2000), event(3000), event(4000)]
    groups = v2._split_episode_capped(episode, 2000)
    assert [[row["target_event_ms"] for row in group] for group in groups] == [
        [0, 1000, 2000],
        [3000, 4000],
    ]


def test_same_second_parents_stay_together() -> None:
    episode = [event(1000, "UP"), event(1000, "DOWN"), event(2000, "UP")]
    groups = v2._split_episode_capped(episode, 1000)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_first_mixed_is_preserved_and_does_not_set_direction_state() -> None:
    events = [
        event(0, "UP"),
        event(0, "DOWN"),
        event(3000, "UP"),
        event(6000, "UP"),
    ]
    bursts, _ = v2._build_capped_bursts(events, idle_gap_s=0, max_duration_s=1)
    assert bursts[0]["burst_type"] == "FIRST_MIXED"
    assert bursts[1]["burst_type"] == "STATE_UNKNOWN"
    assert bursts[2]["burst_type"] == "SAME_SIDE_REENTRY"


def test_capped_bursts_never_exceed_max_duration() -> None:
    events = [event(i * 1000, "UP") for i in range(10)]
    bursts, _ = v2._build_capped_bursts(events, idle_gap_s=1, max_duration_s=2)
    assert max(row["burst_duration_ms"] for row in bursts) <= 2000
    assert len(bursts) == 4
