from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "analyze_target_opportunity_risk_alignment_v24.py"
spec = importlib.util.spec_from_file_location("target_opportunity_risk_alignment_v24", SCRIPT)
assert spec and spec.loader
v24 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v24)


def _burst(
    burst_id: str,
    *,
    market_id: int = 1,
    segment_id: int = 1,
    first: int,
    last: int,
    purpose: str,
    side: str,
    bids: int = 1,
    asks: int = 0,
):
    row = {
        "burst_id": burst_id,
        "market_id": market_id,
        "segment_id": segment_id,
        "first_event_ms": first,
        "last_event_ms": last,
        "purpose": purpose,
        "side": side,
        "bid_parent_count": bids,
        "ask_parent_count": asks,
        "shares": 10.0,
    }
    row["exposure_direction"] = v24.exposure_direction(row)
    return row


def _snapshot(ts_ns: int, *, market_id: int = 1, score: float = 0.6):
    return {
        "timestamp_ns": ts_ns,
        "market_id": market_id,
        "spot_price": 100.0,
        "futures_price": 100.1,
        "spot_queue_imbalance": 0.5,
        "futures_queue_imbalance": 0.4,
        "direction_score": score,
    }


def test_exposure_direction_respects_bid_ask_semantics():
    assert v24.exposure_direction({"side": "UP", "bid_parent_count": 1, "ask_parent_count": 0}) == "UP"
    assert v24.exposure_direction({"side": "DOWN", "bid_parent_count": 1, "ask_parent_count": 0}) == "DOWN"
    assert v24.exposure_direction({"side": "UP", "bid_parent_count": 0, "ask_parent_count": 1}) == "DOWN"
    assert v24.exposure_direction({"side": "DOWN", "bid_parent_count": 0, "ask_parent_count": 1}) == "UP"
    assert v24.exposure_direction({"side": "UP", "bid_parent_count": 1, "ask_parent_count": 1}) == "UNKNOWN"


def test_strict_past_excludes_equal_timestamp():
    rows = [_snapshot(999_000_000), _snapshot(1_000_000_000, score=-0.8)]
    index = v24.build_snapshot_index(rows)
    snap, status = v24.strict_past_snapshot(index, 1, 1000, 750)
    assert status == "OK"
    assert snap is not None
    assert snap["timestamp_ns"] == 999_000_000
    assert snap["direction_score"] == 0.6


def test_strict_past_rejects_stale_snapshot():
    index = v24.build_snapshot_index([_snapshot(100_000_000)])
    snap, status = v24.strict_past_snapshot(index, 1, 1000, 750)
    assert snap is None
    assert status == "STALE_SNAPSHOT"


def test_strict_triplet_requires_contiguous_actions_and_same_group():
    rows = [
        _burst("a", first=1000, last=1100, purpose="ADD", side="UP"),
        _burst("b", first=1200, last=1300, purpose="REPAIR", side="DOWN"),
        _burst("c", first=1400, last=1500, purpose="ADD", side="UP"),
    ]
    found = v24.strict_tug_triplets(rows, 0, 5000, 5000)
    assert len(found) == 1

    with_intervening = [
        rows[0],
        _burst("x", first=1150, last=1175, purpose="ADD", side="UP"),
        rows[1], rows[2],
    ]
    assert v24.strict_tug_triplets(with_intervening, 0, 5000, 5000) == []

    cross_market = [rows[0], _burst("b2", market_id=2, first=1200, last=1300, purpose="REPAIR", side="DOWN"), rows[2]]
    assert v24.strict_tug_triplets(cross_market, 0, 5000, 5000) == []


def test_triplet_alignment_detects_repair_against_persistent_opportunity():
    triplet = (
        _burst("a", first=1000, last=1100, purpose="ADD", side="UP"),
        _burst("b", first=1400, last=1500, purpose="REPAIR", side="DOWN"),
        _burst("c", first=1800, last=1900, purpose="ADD", side="UP"),
    )
    snapshots = [
        _snapshot(900_000_000, score=0.7),
        _snapshot(1_300_000_000, score=0.6),
        _snapshot(1_700_000_000, score=0.8),
    ]
    row = v24._triplet_alignment(triplet, v24.build_snapshot_index(snapshots), 750)
    assert row["all_three_fresh"] is True
    assert row["b_opportunity_state"] == "SUPPORT"
    assert row["abc_strong_persistent"] is True
    assert row["b_both_books_support_original"] is True


def test_triplet_alignment_detects_real_signal_reversal_at_repair():
    triplet = (
        _burst("a", first=1000, last=1100, purpose="ADD", side="UP"),
        _burst("b", first=1400, last=1500, purpose="REPAIR", side="DOWN"),
        _burst("c", first=1800, last=1900, purpose="ADD", side="UP"),
    )
    snapshots = [
        _snapshot(900_000_000, score=0.7),
        _snapshot(1_300_000_000, score=-0.7),
        _snapshot(1_700_000_000, score=0.8),
    ]
    row = v24._triplet_alignment(triplet, v24.build_snapshot_index(snapshots), 750)
    assert row["b_opportunity_state"] == "REVERSE"
    assert row["abc_strong_persistent"] is False
