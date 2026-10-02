from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_maker_taker_inventory_lifecycle_v1_1 as mod


def _row(parent_id: str, market_id: int, first_ms: int, last_ms: int, semantic: str = "BALANCE") -> dict:
    return {
        "parent_id": parent_id,
        "market_id": market_id,
        "first_event_ms": first_ms,
        "last_event_ms": last_ms,
        "taker_index_market": 99,
        "is_first_taker": 0,
        "semantic_primary": semantic,
        "maker_relation": "OPPOSE_MAKER_HEAVY",
        "balance_component_shares": 2.0,
        "maker_offset_component_shares": 3.0,
        "notional_usdt": 4.0,
    }


def test_dedupe_action_rows_keeps_one_row_per_parent_and_reindexes_market():
    rows = [
        _row("p1", 1, 1000, 1100),
        _row("p1", 1, 1000, 1100),
        _row("p2", 1, 1200, 1300, "ADD"),
        _row("p3", 2, 900, 1000),
        _row("p3", 2, 900, 1000),
    ]
    deduped, audit = mod._dedupe_action_rows(rows)
    assert audit["inputRows"] == 5
    assert audit["outputRows"] == 3
    assert audit["duplicateRowsRemoved"] == 2
    assert len({row["parent_id"] for row in deduped}) == 3

    market1 = [row for row in deduped if row["market_id"] == 1]
    market1.sort(key=lambda row: row["taker_index_market"])
    assert [row["parent_id"] for row in market1] == ["p1", "p2"]
    assert [row["taker_index_market"] for row in market1] == [1, 2]
    assert [row["is_first_taker"] for row in market1] == [1, 0]


def test_rebuild_market_action_fields_uses_deduped_actions_only():
    payloads = {
        1: {
            "takerParents": 999,
            "takerBalanceActions": 999,
            "takerFlipActions": 999,
            "takerAddActions": 999,
            "takerOpposeMakerHeavyActions": 999,
            "takerBalanceComponentShares": 999.0,
            "takerMakerOffsetComponentShares": 999.0,
            "takerRepairNotionalUsdt": 999.0,
        }
    }
    actions = [
        _row("p1", 1, 1000, 1100, "BALANCE"),
        _row("p2", 1, 1200, 1300, "ADD"),
    ]
    rebuilt = mod._rebuild_market_action_fields(payloads, actions)
    row = rebuilt[1]
    assert row["takerParents"] == 2
    assert row["takerBalanceActions"] == 1
    assert row["takerAddActions"] == 1
    assert row["takerFlipActions"] == 0
    assert row["takerOpposeMakerHeavyActions"] == 2
    assert row["takerBalanceComponentShares"] == 4.0
    assert row["takerMakerOffsetComponentShares"] == 6.0
    assert row["takerRepairNotionalUsdt"] == 8.0
