from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_controller_complete_history_v2 as mod
import analyze_target_controller_parameter_extraction_v1 as core


def _event(source: str, leg_id: str, market_id: int, ms: int, role: str = "TAKER", side: str = "UP", quote: str = "BID", price: float = 0.5, shares: float = 1.0) -> dict:
    return {
        "source_version": source,
        "leg_id": leg_id,
        "wallet": mod.TARGET_WALLET,
        "asset": "BTC",
        "market_id": market_id,
        "role": role,
        "side": side,
        "quote_type": quote,
        "order_hash": leg_id,
        "event_ms": ms,
        "price": price,
        "shares": shares,
    }


def _parent(pid: str, ms: int, role: str = "TAKER", side: str = "UP", quote: str = "BID", price: float = 0.5, shares: float = 1.0) -> dict:
    return {
        "parent_id": pid,
        "source_version": "OFFICIAL",
        "market_id": 1,
        "role": role,
        "side": side,
        "quote_type": quote,
        "first_event_ms": ms,
        "last_event_ms": ms,
        "shares": shares,
        "average_price": price,
        "notional_usdt": price * shares,
    }


def test_stitch_never_uses_legacy_after_cutover_as_gap_fill():
    cutoff = 10_000
    legacy = [_event("LEGACY", "l-before", 1, 9_000), _event("LEGACY", "l-after", 2, 11_000)]
    official = [_event("OFFICIAL", "o-after", 3, 12_000)]
    rows, audit = mod._stitch(legacy, official, cutoff)
    assert [(r["source_version"], r["leg_id"]) for r in rows] == [
        ("LEGACY", "l-before"), ("OFFICIAL", "o-after")
    ]
    assert audit["legacyRowsSelected"] == 1
    assert audit["officialRowsSelected"] == 1
    assert all(r["leg_id"] != "l-after" for r in rows)


def test_hard_gap_splits_segment_and_invalidates_crossing_market():
    rows = [
        _event("OFFICIAL", "a", 1, 1_000),
        _event("OFFICIAL", "b", 1, 2_000),
        _event("OFFICIAL", "c", 1, 100_000),
    ]
    mapping, gaps = mod._assign_segments(rows, 30_000)
    assert len(gaps) == 1
    assert mapping[("OFFICIAL", "a")] == mapping[("OFFICIAL", "b")]
    assert mapping[("OFFICIAL", "c")] != mapping[("OFFICIAL", "b")]
    valid, segment, source, reason = mod._market_segment(rows, mapping)
    assert valid is False
    assert segment is None
    assert source == "OFFICIAL"
    assert "hard gap" in reason


def test_bursts_use_idle_gap_onset_cap_and_maker_break():
    p1 = _parent("t1", 1_000)
    p2 = _parent("t2", 1_600)
    p3 = _parent("t3", 3_700)
    maker = _parent("m1", 4_000, role="MAKER")
    p4 = _parent("t4", 4_100)
    p5 = _parent("t5", 4_500)
    groups = mod._burst_groups([p1, p2, p3, maker, p4, p5], idle_gap_ms=1_000, cap_ms=2_000)
    assert [[p["parent_id"] for p in group] for group in groups] == [
        ["t1", "t2"], ["t3"], ["t4", "t5"]
    ]


def test_cheap_opposite_side_buy_improves_tail_and_gap():
    state = core.PortfolioState(taker_up=10.0, taker_cash=-4.0)
    before = core._portfolio_metrics(state)
    core._apply_leg(state, "TAKER", "DOWN", "BID", 5.0, 0.05)
    after = core._portfolio_metrics(state)
    assert after["worst_case_pnl"] > before["worst_case_pnl"]
    assert after["abs_payoff_gap"] < before["abs_payoff_gap"]
    assert core._portfolio_effect(before, after) == "RISK_REDUCING"
    cash_cost = before["cash"] - after["cash"]
    assert cash_cost > 0
    assert (after["worst_case_pnl"] - before["worst_case_pnl"]) / cash_cost > 1


def test_mixed_source_market_is_invalid():
    rows = [_event("LEGACY", "l1", 7, 1_000), _event("OFFICIAL", "o1", 7, 1_100)]
    mapping, _ = mod._assign_segments(rows, 30_000)
    valid, segment, source, reason = mod._market_segment(rows, mapping)
    assert valid is False
    assert segment is None
    assert source == "MIXED"
    assert "source-version boundary" in reason
