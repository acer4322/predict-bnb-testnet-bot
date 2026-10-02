from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_hazard_v21 as mod


def _parent(
    pid: str,
    first_ms: int,
    *,
    last_ms: int | None = None,
    role: str = "TAKER",
    side: str = "UP",
    quote: str = "BID",
    price: float = 0.5,
    shares: float = 1.0,
) -> dict:
    return {
        "parent_id": pid,
        "source_version": "OFFICIAL",
        "market_id": 1,
        "role": role,
        "side": side,
        "quote_type": quote,
        "first_event_ms": first_ms,
        "last_event_ms": first_ms if last_ms is None else last_ms,
        "shares": shares,
        "average_price": price,
        "notional_usdt": price * shares,
    }


def test_directional_burst_breaks_on_side_flip_and_maker():
    rows = [
        _parent("t1", 1_000, side="UP"),
        _parent("t2", 1_400, side="UP"),
        _parent("t3", 1_700, side="DOWN"),
        _parent("t4", 1_900, side="DOWN"),
        _parent("m1", 2_050, role="MAKER", side="UP"),
        _parent("t5", 2_200, side="DOWN"),
    ]
    groups = mod._directional_burst_groups(rows, idle_gap_ms=1_000, cap_ms=3_000)
    assert [[row["parent_id"] for row in group] for group in groups] == [
        ["t1", "t2"],
        ["t3", "t4"],
        ["t5"],
    ]


def test_directional_burst_still_respects_idle_gap_and_onset_cap():
    rows = [
        _parent("t1", 1_000, side="UP"),
        _parent("t2", 1_700, side="UP"),
        _parent("t3", 3_100, side="UP"),
        _parent("t4", 3_500, side="UP"),
    ]
    groups = mod._directional_burst_groups(rows, idle_gap_ms=1_000, cap_ms=2_000)
    assert [[row["parent_id"] for row in group] for group in groups] == [
        ["t1", "t2"],
        ["t3", "t4"],
    ]


def test_future_label_is_strictly_after_sample_and_splits_purpose():
    bursts = [
        {"first_event_ms": 2_000, "last_event_ms": 2_100, "purpose": "REPAIR", "side": "DOWN", "burst_id": "b1"},
        {"first_event_ms": 4_500, "last_event_ms": 4_600, "purpose": "ADD", "side": "UP", "burst_id": "b2"},
    ]
    starts = [2_000, 4_500]
    row = mod._labels(1_000, bursts, starts)
    assert row["repair_within_1s"] == 1
    assert row["add_within_3s"] == 0
    assert row["add_within_5s"] == 1

    same_time = mod._labels(2_000, bursts, starts)
    assert same_time["next_taker_delay_ms"] == 2_500
    assert same_time["next_taker_purpose"] == "ADD"


def test_directional_repair_purpose_uses_portfolio_effect():
    windows = {"TEST": (300_000, 310_000)}
    parents = [
        _parent("m1", 300_500, last_ms=301_000, role="MAKER", side="UP", quote="BID", price=0.4, shares=10),
        _parent("t1", 304_000, last_ms=304_100, role="TAKER", side="DOWN", quote="BID", price=0.2, shares=5),
    ]
    bursts = mod._directional_bursts(1, 1, parents, 1_000, 3_000, windows)
    assert len(bursts) == 1
    assert bursts[0]["portfolio_effect"] == "RISK_REDUCING"
    assert bursts[0]["purpose"] == "REPAIR"
    assert bursts[0]["side"] == "DOWN"


def test_fixed_grid_uses_completed_strict_past_parent_and_lifecycle_age():
    windows = {"TEST": (300_000, 310_000)}
    parents = [
        _parent("m1", 300_500, last_ms=301_500, role="MAKER", side="UP", quote="BID", price=0.4, shares=10),
        _parent("t1", 304_000, last_ms=304_100, role="TAKER", side="DOWN", quote="BID", price=0.2, shares=5),
        _parent("m2", 306_200, last_ms=306_300, role="MAKER", side="UP", quote="BID", price=0.45, shares=2),
    ]
    bursts = mod._directional_bursts(1, 1, parents, 1_000, 3_000, windows)
    states, audit = mod._fixed_grid(1, 1, parents, bursts, windows)

    assert all(row["sample_ms"] != 301_000 for row in states)
    at_302 = next(row for row in states if row["sample_ms"] == 302_000)
    assert at_302["prior_maker_parents"] == 1
    assert at_302["time_since_last_maker_ms"] == 500
    assert at_302["taker_within_3s"] == 1
    assert at_302["repair_within_3s"] == 1

    assert all(row["sample_ms"] != 304_000 for row in states)
    at_307 = next(row for row in states if row["sample_ms"] == 307_000)
    assert at_307["prior_taker_parents"] == 1
    assert at_307["maker_parents_since_last_taker"] == 1
    assert at_307["maker_streak_age_ms"] == 700
    assert audit["emitted"] > 0


def test_surface_sort_is_stable_when_feature_values_tie():
    rows = []
    for index in range(10):
        rows.append({
            "risk_deficit": 1.0,
            "taker_within_5s": int(index % 2 == 0),
            "repair_within_5s": 0,
            "add_within_5s": 0,
            "taker_within_15s": 0,
            "repair_within_15s": 0,
            "add_within_15s": 0,
        })
    surface = mod._surface(rows, "risk_deficit")
    assert len(surface) == 5
    assert sum(item["rows"] for item in surface) == 10
