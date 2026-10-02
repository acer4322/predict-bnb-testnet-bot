from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_tug_of_war_v23 as mod


def _burst(
    bid: str,
    ms: int,
    *,
    purpose: str,
    side: str,
    quote: str = "BID",
    shares: float = 10.0,
    regime: str = mod.STRESS,
    market: int = 1,
) -> dict:
    bids = 1 if quote == "BID" else 0
    asks = 1 if quote == "ASK" else 0
    return {
        "regime": regime,
        "market_id": market,
        "segment_id": 1,
        "burst_id": bid,
        "first_event_ms": ms,
        "last_event_ms": ms + 100,
        "side": side,
        "bid_parent_count": bids,
        "ask_parent_count": asks,
        "shares": shares,
        "purpose": purpose,
        "pre_risk_deficit": 10.0,
        "post_risk_deficit": 20.0 if purpose == "ADD" else 5.0,
        "pre_abs_payoff_gap": 20.0,
        "post_abs_payoff_gap": 30.0 if purpose == "ADD" else 10.0,
    }


def test_exposure_direction_respects_ask_as_signed_opposite():
    assert mod.exposure_direction(_burst("a", 0, purpose="ADD", side="UP", quote="BID")) == "UP"
    assert mod.exposure_direction(_burst("b", 0, purpose="REPAIR", side="DOWN", quote="BID")) == "DOWN"
    assert mod.exposure_direction(_burst("c", 0, purpose="ADD", side="UP", quote="ASK")) == "DOWN"
    assert mod.exposure_direction(_burst("d", 0, purpose="ADD", side="DOWN", quote="ASK")) == "UP"


def test_mixed_bid_ask_direction_is_unknown():
    row = _burst("x", 0, purpose="ADD", side="UP")
    row["bid_parent_count"] = 1
    row["ask_parent_count"] = 1
    assert mod.exposure_direction(row) == "UNKNOWN"


def test_strict_tug_requires_add_opposite_repair_same_direction_add():
    a = _burst("a", 1_000, purpose="ADD", side="UP")
    b = _burst("b", 2_000, purpose="REPAIR", side="DOWN")
    c = _burst("c", 3_000, purpose="ADD", side="UP")
    for row in (a, b, c):
        row["exposure_direction"] = mod.exposure_direction(row)
    assert mod._is_strict_tug(a, b, c, 5_000)

    c_bad = dict(c)
    c_bad["side"] = "DOWN"
    c_bad["exposure_direction"] = mod.exposure_direction(c_bad)
    assert not mod._is_strict_tug(a, b, c_bad, 5_000)


def test_sequence_analysis_uses_consecutive_bursts_and_reports_risk_path():
    rows = [
        _burst("a", 1_000, purpose="ADD", side="UP", shares=10),
        _burst("b", 2_000, purpose="REPAIR", side="DOWN", shares=4),
        _burst("c", 3_000, purpose="ADD", side="UP", shares=8),
        _burst("d", 20_000, purpose="ADD", side="DOWN", shares=5),
    ]
    for row in rows:
        row["exposure_direction"] = mod.exposure_direction(row)
    summary, patterns = mod._analyze_regime(rows, 5_000)
    assert summary["strictOppositeRepairPairs"] == 1
    assert summary["strictTugOfWarTriplets"] == 1
    assert summary["sameDirectionAddContinuationGivenStrictPair"] == 1.0
    assert len(patterns) == 1
    assert patterns[0]["b_to_a_share_ratio"] == 0.4
    assert patterns[0]["c_to_a_share_ratio"] == 0.8
    assert patterns[0]["a_risk_delta"] > 0
    assert patterns[0]["b_risk_delta"] < 0


def test_non_adjacent_cherry_pick_is_not_counted():
    rows = [
        _burst("a", 1_000, purpose="ADD", side="UP"),
        _burst("noise", 1_500, purpose="ADD", side="DOWN"),
        _burst("b", 2_000, purpose="REPAIR", side="DOWN"),
        _burst("c", 3_000, purpose="ADD", side="UP"),
    ]
    for row in rows:
        row["exposure_direction"] = mod.exposure_direction(row)
    summary, _ = mod._analyze_regime(rows, 5_000)
    assert summary["strictTugOfWarTriplets"] == 0
