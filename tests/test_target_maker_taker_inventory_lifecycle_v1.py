from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_maker_taker_inventory_lifecycle_v1 as mod


def test_signed_directional_effect_covers_bid_and_ask_both_outcomes():
    assert mod._directional_effect("UP", "BID", 4.0) == 4.0
    assert mod._directional_effect("UP", "ASK", 4.0) == -4.0
    assert mod._directional_effect("DOWN", "BID", 4.0) == -4.0
    assert mod._directional_effect("DOWN", "ASK", 4.0) == 4.0


def test_balance_add_and_flip_semantics():
    assert mod._semantic(10.0, 6.0) == "BALANCE"
    assert mod._semantic(10.0, 14.0) == "ADD"
    assert mod._semantic(10.0, -5.0) == "FLIP"
    assert mod._semantic(0.0, 0.0) == "NEUTRAL"


def test_bid_down_repairs_up_heavy_maker_inventory():
    before = mod._empty_state()
    mod._apply_flow(before, "MAKER", "UP", "BID", 10.0)
    metrics = mod._state_metrics(before)
    effect = mod._directional_effect("DOWN", "BID", 4.0)
    after = mod._counterfactual_after(before, "TAKER", "DOWN", "BID", 4.0)
    after_metrics = mod._state_metrics(after)
    assert metrics["maker_delta"] == 10.0
    assert effect == -4.0
    assert after_metrics["combined_delta"] == 6.0
    assert mod._semantic(metrics["combined_delta"], after_metrics["combined_delta"]) == "BALANCE"
    assert mod._maker_relation(metrics["maker_delta"], effect) == "OPPOSE_MAKER_HEAVY"
    assert mod._repair_component(metrics["maker_delta"], effect) == 4.0


def test_ask_up_can_repair_up_heavy_maker_inventory():
    before = mod._empty_state()
    mod._apply_flow(before, "MAKER", "UP", "BID", 10.0)
    metrics = mod._state_metrics(before)
    effect = mod._directional_effect("UP", "ASK", 4.0)
    after = mod._counterfactual_after(before, "TAKER", "UP", "ASK", 4.0)
    after_metrics = mod._state_metrics(after)
    assert effect == -4.0
    assert after_metrics["combined_delta"] == 6.0
    assert mod._maker_relation(metrics["maker_delta"], effect) == "OPPOSE_MAKER_HEAVY"


def test_flip_tracks_oversized_repair_component_without_calling_all_shares_repair():
    before = mod._empty_state()
    mod._apply_flow(before, "MAKER", "UP", "BID", 10.0)
    effect = mod._directional_effect("DOWN", "BID", 15.0)
    after = mod._counterfactual_after(before, "TAKER", "DOWN", "BID", 15.0)
    before_metrics = mod._state_metrics(before)
    after_metrics = mod._state_metrics(after)
    assert after_metrics["combined_delta"] == -5.0
    assert mod._semantic(before_metrics["combined_delta"], after_metrics["combined_delta"]) == "FLIP"
    assert mod._repair_component(before_metrics["combined_delta"], effect) == 10.0


def test_parent_aggregation_reconstructs_official_parent_identity_and_weighted_price():
    events = [
        {
            "leg_id": "leg-a",
            "asset": "BTC",
            "market_id": 1,
            "role": "TAKER",
            "side": "DOWN",
            "quote_type": "BID",
            "order_hash": "0xabc",
            "event_ms": 1000,
            "price": 0.40,
            "shares": 2.0,
        },
        {
            "leg_id": "leg-b",
            "asset": "BTC",
            "market_id": 1,
            "role": "TAKER",
            "side": "DOWN",
            "quote_type": "BID",
            "order_hash": "0xabc",
            "event_ms": 1100,
            "price": 0.50,
            "shares": 3.0,
        },
    ]
    for row in events:
        row["parent_id"] = mod._parent_id_for_event(row)
    parents = mod._build_parents(events)
    assert len(parents) == 1
    parent = parents[0]
    assert parent["parent_id"] == "BTC:TAKER:0xabc:DOWN:BID"
    assert parent["fill_legs"] == 2
    assert parent["shares"] == 5.0
    assert abs(parent["average_price"] - 0.46) < 1e-12
    assert parent["first_event_ms"] == 1000
    assert parent["last_event_ms"] == 1100


def test_state_before_parent_is_strictly_prior_to_current_parent_effect():
    before = mod._empty_state()
    mod._apply_flow(before, "MAKER", "UP", "BID", 8.0)
    frozen = mod._copy_state(before)
    after = mod._counterfactual_after(frozen, "TAKER", "DOWN", "BID", 3.0)
    assert mod._state_metrics(frozen)["combined_delta"] == 8.0
    assert mod._state_metrics(after)["combined_delta"] == 5.0
    # Counterfactual application must not mutate the strict-prior snapshot.
    assert mod._state_metrics(frozen)["combined_delta"] == 8.0
