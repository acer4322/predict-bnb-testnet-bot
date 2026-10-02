from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools" / "analyze_target_opportunity_risk_alignment_v241.py"
spec = importlib.util.spec_from_file_location("v241", PATH)
assert spec and spec.loader
v241 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v241)


def snap(ts_ns: int, market_id: int = 6991509, score: float = 0.4):
    return {
        "timestamp_ns": ts_ns,
        "market_id": market_id,
        "spot_price": 100.0,
        "futures_price": 100.1,
        "spot_queue_imbalance": 0.3,
        "futures_queue_imbalance": 0.4,
        "direction_score": score,
    }


def test_strict_past_excludes_equal_timestamp():
    rows = [snap(1_000_000_000), snap(2_000_000_000)]
    index = v241.build_time_index(rows)
    got, status = v241.strict_past_snapshot_by_time(index, 2000, 1500)
    assert status == "OK"
    assert got is not None
    assert got["timestamp_ns"] == 1_000_000_000


def test_stale_snapshot_is_rejected():
    rows = [snap(1_000_000_000)]
    index = v241.build_time_index(rows)
    got, status = v241.strict_past_snapshot_by_time(index, 2000, 750)
    assert got is None
    assert status == "STALE_SNAPSHOT"


def test_time_join_does_not_require_equal_market_ids():
    rows = [snap(1_900_000_000, market_id=6991509)]
    index = v241.build_time_index(rows)
    got, status = v241.strict_past_snapshot_by_time(index, 2000, 750)
    assert status == "OK"
    target = {
        "market_id": 1396279,
        "segment_id": 1,
        "burst_id": "x",
        "first_event_ms": 2000,
        "purpose": "ADD",
        "exposure_direction": "UP",
        "shares": 10.0,
    }
    aligned = v241._action_alignment(target, got, status)
    assert aligned["target_market_id"] == 1396279
    assert aligned["micro_market_id"] == 6991509
    assert aligned["join_status"] == "OK"


def test_mapping_summary_reports_cross_namespace_pair():
    actions = [
        {"join_status": "OK", "target_market_id": 1396279, "micro_market_id": 6991509},
        {"join_status": "OK", "target_market_id": 1396279, "micro_market_id": 6991509},
        {"join_status": "OK", "target_market_id": 1396279, "micro_market_id": 6991528},
    ]
    summary = v241._mapping_summary(actions)
    row = summary["byTargetMarket"]["1396279"]
    assert row["dominantMicroMarketId"] == 6991509
    assert row["dominantCount"] == 2
    assert abs(row["purity"] - 2 / 3) < 1e-12
