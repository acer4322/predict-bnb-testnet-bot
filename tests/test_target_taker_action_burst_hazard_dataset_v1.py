from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
MODULE = TOOLS / "build_target_taker_action_burst_hazard_dataset_v1.py"
spec = importlib.util.spec_from_file_location("burst_hazard_v1", MODULE)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def burst(onset: int, end: int, burst_type: str = "SAME_SIDE_REENTRY", side: str = "UP") -> dict:
    return {
        "market_id": 1,
        "burst_index": 1,
        "burst_onset_ms": onset,
        "burst_end_ms": end,
        "burst_type": burst_type,
        "side": side,
    }


def test_same_second_burst_is_not_future_positive() -> None:
    index = mod._index_bursts([burst(10_000, 11_000)])
    row = mod._annotate_one(market_id=1, decision_ms=10_250, index=index, prefix="cap2")
    assert row["cap2_label_next_burst_1s"] == 0
    assert row["cap2_label_next_burst_2s"] == 0
    assert row["cap2_burst_active"] == 1


def test_next_second_burst_is_positive() -> None:
    index = mod._index_bursts([burst(12_000, 12_000)])
    row = mod._annotate_one(market_id=1, decision_ms=10_250, index=index, prefix="cap2")
    assert row["cap2_label_next_burst_1s"] == 0
    assert row["cap2_label_next_burst_2s"] == 1
    assert row["cap2_label_next_burst_5s"] == 1
    assert row["cap2_next_burst_delta_ms"] == 2000


def test_idle_and_post_first_state() -> None:
    index = mod._index_bursts([burst(10_000, 11_000), burst(15_000, 15_000)])
    active = mod._annotate_one(market_id=1, decision_ms=11_500, index=index, prefix="cap2")
    idle = mod._annotate_one(market_id=1, decision_ms=13_500, index=index, prefix="cap2")
    assert active["cap2_burst_active"] == 1
    assert active["cap2_risk_idle"] == 0
    assert idle["cap2_burst_active"] == 0
    assert idle["cap2_risk_idle"] == 1
    assert idle["cap2_risk_post_first_idle"] == 1


def test_pre_first_is_not_post_first_idle() -> None:
    index = mod._index_bursts([burst(10_000, 10_000, burst_type="FIRST_ENTRY")])
    row = mod._annotate_one(market_id=1, decision_ms=8_500, index=index, prefix="cap2")
    assert row["cap2_position_state"] == "PRE_FIRST"
    assert row["cap2_risk_idle"] == 1
    assert row["cap2_risk_post_first_idle"] == 0


def test_macro_phase_boundaries() -> None:
    assert mod._macro_phase("181") == "OPEN"
    assert mod._macro_phase("180") == "MID"
    assert mod._macro_phase("61") == "MID"
    assert mod._macro_phase("60") == "TAIL"
