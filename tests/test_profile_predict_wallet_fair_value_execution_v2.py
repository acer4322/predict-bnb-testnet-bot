from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "profile_predict_wallet_fair_value_execution.py"
SPEC = importlib.util.spec_from_file_location("fair_value_v2", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def test_reconstruct_taker_parents_groups_fill_legs_by_hash() -> None:
    base = {
        "role": "TAKER",
        "asset": "BTC",
        "durationMinutes": 5,
        "marketBucket": 1000,
        "marketId": 1,
        "marketTitle": "Bitcoin Up or Down - August 12, 10:00AM-10:05AM ET",
        "side": "UP",
        "quoteType": "BID",
        "orderHash": "0xabc",
        "executionType": "UNKNOWN",
    }
    fills = [
        {**base, "eventMs": 1000, "eventAt": "a", "shares": 2.0, "price": 0.40},
        {**base, "eventMs": 2000, "eventAt": "b", "shares": 3.0, "price": 0.50},
    ]
    parents = m.reconstruct_parents(fills, "TAKER")
    assert len(parents) == 1
    assert parents[0]["fillLegs"] == 2
    assert parents[0]["shares"] == 5.0
    assert abs(parents[0]["price"] - 0.46) < 1e-12


def test_fixed_share_profile_detects_18_share_mode() -> None:
    rows = [{"shares": 18.0}, {"shares": 18.0}, {"shares": 18.0}, {"shares": 7.5}]
    result = m.fixed_share_profile(rows)
    assert result["modeShares"] == 18.0
    assert result["modeCount"] == 3
    assert result["belowModeCount"] == 1
    assert result["aboveModeCount"] == 0


def test_ladder_profile_detects_same_second_tick_grid() -> None:
    rows = []
    for i, price in enumerate((0.20, 0.21, 0.22, 0.23)):
        rows.append({
            "marketId": 1,
            "side": "UP",
            "eventMs": 1_000,
            "price": price,
            "orderHash": f"h{i}",
        })
    result = m.ladder_profile(rows)
    assert result["sameSecondLadderClusters"]["count"] == 1
    assert result["sameSecondLadderClusters"]["maxDistinctPriceLevels"] == 4
    assert result["oneCentAdjacentShare"]["median"] == 1.0


def test_execution_type_is_not_inferred_without_explicit_field() -> None:
    unknown = m.extract_execution_type({"kind": "PURCHASE"}, {"strategy": "LIMIT"})
    assert unknown["executionType"] == "UNKNOWN"
    explicit = m.extract_execution_type({"settlementType": "MINT"}, {})
    assert explicit["executionType"] == "MINT"
