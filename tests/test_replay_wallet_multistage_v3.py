from __future__ import annotations

from tools.replay_wallet_multistage_v3 import Inventory, Policy, simulate_market, summarize


def test_inventory_blocks_excess_net_exposure_and_worst_case_loss() -> None:
    policy = Policy(max_total_cost=10, max_net_shares=2, max_worst_case_loss=1)
    inventory = Inventory()
    assert inventory.can_add("UP", 1, 0.5, policy)
    inventory.add("UP", 1, 0.5)
    assert inventory.can_add("UP", 1, 0.5, policy)
    inventory.add("UP", 1, 0.5)
    assert not inventory.can_add("UP", 1, 0.5, policy)
    assert inventory.can_add("DOWN", 1, 0.4, policy)


def test_simulation_can_fill_maker_then_taker_without_target_inputs() -> None:
    policy = Policy(
        recenter_seconds=5,
        start_seconds=240,
        stop_seconds=10,
        maker_edge=0.01,
        taker_edge=0.03,
        max_total_cost=10,
        max_net_shares=4,
        max_worst_case_loss=3,
    )
    local = [
        {"market_bucket": 1000, "market_id": 7, "sampled_at_ms": 1_000_060_000, "seconds_left": 240, "up_bid": 0.50, "up_ask": 0.52, "down_bid": 0.45, "down_ask": 0.47},
        {"market_bucket": 1000, "market_id": 7, "sampled_at_ms": 1_000_061_000, "seconds_left": 239, "up_bid": 0.48, "up_ask": 0.50, "down_bid": 0.45, "down_ask": 0.47},
    ]
    upstream = [
        {"sampled_at_ms": 1_000_060_000, "poly_up_mid": 0.70, "poly_receipt_age_ms": 0},
        {"sampled_at_ms": 1_000_061_000, "poly_up_mid": 0.70, "poly_receipt_age_ms": 0},
    ]
    result = simulate_market(local, upstream, "UP", policy)
    assert result["makerFills"] == 1
    assert result["takerFills"] == 1
    assert {event["role"] for event in result["actions"]} == {"MAKER", "TAKER"}
    assert result["pnl"] > 0


def test_summary_uses_market_level_profitability() -> None:
    summary = summarize([
        {"traded": True, "pnl": 0.2, "cost": 1.0, "makerFills": 1, "takerFills": 1, "upShares": 1, "downShares": 1, "actions": [{}, {}]},
        {"traded": True, "pnl": -0.1, "cost": 1.0, "makerFills": 1, "takerFills": 0, "upShares": 1, "downShares": 0, "actions": [{}]},
    ])
    assert summary["marketWinRate"] == 0.5
    assert summary["roiOnCost"] == 0.05
    assert summary["bothRoleMarkets"] == 1
    assert summary["bothSideMarkets"] == 1
