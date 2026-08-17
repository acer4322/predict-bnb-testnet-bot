from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "build_target_maker_inventory_conditioned_side_v2.py"
SPEC = importlib.util.spec_from_file_location("maker_inventory_side_v2_builder_test", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _behavior(parent: str, market_id: int, placement_ms: int, side_up: int) -> dict[str, object]:
    return {
        "parent_id": parent,
        "market_id": market_id,
        "placement_first_ms": placement_ms,
        "label_side_up": side_up,
    }


def _event(
    leg_id: str,
    parent_id: str,
    market_id: int,
    event_ms: int,
    role: str,
    side: str,
    quote_type: str,
    shares: float,
) -> dict[str, object]:
    return {
        "leg_id": leg_id,
        "parent_id": parent_id,
        "market_id": market_id,
        "event_ms": event_ms,
        "role": role,
        "side": side,
        "quote_type": quote_type,
        "shares": shares,
    }


def test_same_timestamp_current_fill_is_excluded_until_next_placement():
    rows = [
        _behavior("placement-a", 101, 1000, 1),
        _behavior("placement-b", 101, 1100, 0),
    ]
    events = [
        _event("e0", "old-maker-up", 101, 900, "MAKER", "UP", "BID", 10.0),
        _event("e1", "same-ms-maker-down", 101, 1000, "MAKER", "DOWN", "BID", 5.0),
    ]
    output = mod._enrich_market_rows(rows, events, regime="ORDINARY_PRE_SPECIAL")
    first, second = output

    assert first["maker_up_net_shares"] == 10.0
    assert first["maker_down_net_shares"] == 0.0
    assert first["maker_delta_shares"] == 10.0
    assert first["prior_maker_parent_count"] == 1
    assert first["strict_past_same_timestamp_excluded"] == 1

    assert second["maker_up_net_shares"] == 10.0
    assert second["maker_down_net_shares"] == 5.0
    assert second["maker_delta_shares"] == 5.0
    assert second["prior_maker_parent_count"] == 2
    assert second["last_maker_side_up"] == 0
    assert second["last_maker_age_ms"] == 100


def test_bid_ask_signed_inventory_and_taker_feedback_are_correct():
    rows = [_behavior("placement", 202, 1000, 1)]
    events = [
        _event("m1", "maker-up-bid", 202, 700, "MAKER", "UP", "BID", 10.0),
        _event("m2", "maker-up-ask", 202, 800, "MAKER", "UP", "ASK", 3.0),
        _event("t1", "taker-down-bid", 202, 900, "TAKER", "DOWN", "BID", 4.0),
    ]
    row = mod._enrich_market_rows(rows, events, regime="SPECIAL")[0]

    assert row["maker_up_net_shares"] == 7.0
    assert row["maker_down_net_shares"] == 0.0
    assert row["maker_delta_shares"] == 7.0
    assert row["taker_down_net_shares"] == 4.0
    assert row["taker_delta_shares"] == -4.0
    assert row["combined_up_net_shares"] == 7.0
    assert row["combined_down_net_shares"] == 4.0
    assert row["combined_delta_shares"] == 3.0
    assert row["combined_paired_long_shares"] == 4.0
    assert row["prior_maker_parent_count"] == 2
    assert row["prior_taker_parent_count"] == 1
    assert row["last_taker_side_up"] == 0
    assert row["last_taker_age_ms"] == 100


def test_market_state_does_not_leak_across_markets():
    first = mod._enrich_market_rows(
        [_behavior("a", 1, 1000, 1)],
        [_event("e", "old", 1, 900, "MAKER", "UP", "BID", 9.0)],
        regime="ORDINARY_PRE_SPECIAL",
    )[0]
    second = mod._enrich_market_rows(
        [_behavior("b", 2, 1000, 0)],
        [],
        regime="ORDINARY_PRE_SPECIAL",
    )[0]

    assert first["maker_delta_shares"] == 9.0
    assert second["maker_delta_shares"] == 0.0
    assert second["prior_maker_parent_count"] == 0
    assert second["strict_past_official_event_count"] == 0


def test_inventory_feature_contract_contains_no_current_or_future_label_fields():
    forbidden_tokens = ("label_", "target_side", "placement_first", "winner", "pnl", "future")
    assert mod.INVENTORY_FEATURES
    assert len(mod.INVENTORY_FEATURES) == len(set(mod.INVENTORY_FEATURES))
    for feature in mod.INVENTORY_FEATURES:
        lowered = feature.lower()
        assert not any(token in lowered for token in forbidden_tokens)


def test_first_placement_flag_and_index_are_deterministic_after_sort():
    rows = [
        _behavior("later", 303, 2000, 0),
        _behavior("first", 303, 1000, 1),
    ]
    output = mod._enrich_market_rows(rows, [], regime="ORDINARY_PRE_SPECIAL")
    assert [row["parent_id"] for row in output] == ["first", "later"]
    assert [row["maker_placement_index_market"] for row in output] == [1, 2]
    assert [row["is_first_maker_placement"] for row in output] == [1, 0]
