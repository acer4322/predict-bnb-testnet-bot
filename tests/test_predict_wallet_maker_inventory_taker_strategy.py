from predict_bot import predict_wallet_maker_inventory_taker_strategy as strategy


def snapshot(**changes):
    row = {
        "market_id": 10,
        "timestamp_ns": 10_000_000_000,
        "sampled_at_ms": 10_000,
        "predict_receipt_age_ms": 100,
        "seconds_left": 120,
        "predict_up_bid": 0.48,
        "predict_up_ask": 0.49,
        "predict_down_bid": 0.50,
        "predict_down_ask": 0.51,
        "direction_score": -0.40,
        "futures_queue_imbalance": -0.20,
        "futures_taker_imbalance_1s": 0.10,
        "spot_return_1s_bps": 1.0,
        "futures_return_1s_bps": 1.2,
        "volatility_alert": 0,
    }
    row.update(changes)
    return row


def test_inventory_baseline_requires_paired_maker_state_and_same_residual_side() -> None:
    empty = strategy.inventory_state(maker_up_shares=0, maker_down_shares=0)
    assert empty["makerResidualSide"] is None
    assert empty["combinedResidualSide"] is None

    inventory = strategy.inventory_state(
        maker_up_shares=90,
        maker_down_shares=54,
        taker_down_shares=9,
    )
    assert inventory["baselineReady"] is True
    assert inventory["makerResidualSide"] == "UP"
    assert inventory["combinedResidualSide"] == "UP"
    assert inventory["correctionSide"] == "DOWN"
    assert inventory["makerPairedCoverage"] == 0.75

    overcorrected = strategy.inventory_state(
        maker_up_shares=90,
        maker_down_shares=54,
        taker_down_shares=40,
    )
    assert overcorrected["makerResidualSide"] == "UP"
    assert overcorrected["combinedResidualSide"] == "DOWN"
    assert overcorrected["correctionSide"] is None


def test_taker_needs_inventory_and_independent_microstructure_confluence() -> None:
    inventory = strategy.inventory_state(maker_up_shares=90, maker_down_shares=54)
    decision = strategy.decide_taker(
        snapshot(), inventory, expected_market_id=10, now_ms=10_100, last_trade_ms=None
    )
    assert decision["decision"] == "TRADE"
    assert decision["side"] == "DOWN"
    assert decision["reason"] == "INVENTORY_MICRO_CONFLUENCE"

    conflict = strategy.decide_taker(
        snapshot(direction_score=0.40), inventory,
        expected_market_id=10, now_ms=10_100, last_trade_ms=None,
    )
    assert conflict["decision"] == "SKIP"
    assert conflict["reason"] == "NO_INVENTORY_MICRO_CONFLUENCE"


def test_taker_execution_reduces_but_does_not_flip_residual() -> None:
    inventory = strategy.inventory_state(maker_up_shares=90, maker_down_shares=54)
    fill = strategy.taker_execution("DOWN", snapshot(), inventory)
    assert fill is not None
    assert fill["shares"] == 18
    assert fill["principalUsdt"] >= 1
    assert fill["feeUsdt"] == fill["principalUsdt"] * 0.02


def test_dynamic_depth_uses_structural_anchors_and_inventory_tilt() -> None:
    balanced = strategy.inventory_state(maker_up_shares=72, maker_down_shares=72)
    calm = strategy.dynamic_depths(
        snapshot(spot_return_1s_bps=0.2, futures_return_1s_bps=0.3), balanced
    )
    assert (calm["upLevels"], calm["downLevels"]) == (15, 15)

    volatile = strategy.dynamic_depths(snapshot(volatility_alert=1), balanced)
    assert (volatile["upLevels"], volatile["downLevels"]) == (3, 3)

    up_heavy = strategy.inventory_state(maker_up_shares=90, maker_down_shares=54)
    tilted = strategy.dynamic_depths(snapshot(), up_heavy)
    assert (tilted["upLevels"], tilted["downLevels"]) == (3, 11)
    orders = strategy.desired_grid(snapshot(), tilted)
    assert sum(order["side"] == "UP" for order in orders) == 3
    assert sum(order["side"] == "DOWN" for order in orders) == 11
    assert all(order["price"] * order["shares"] >= 1 for order in orders)


def test_reservation_skew_research_profile_reduces_heavy_side() -> None:
    variant = next(row for row in strategy.COHORTS if row["cohort"] == "MAKER_RESERVATION_SKEW_SHARED_V1")
    balanced = strategy.inventory_state(maker_up_shares=72, maker_down_shares=72)
    neutral = strategy.depth_plan(
        variant,
        snapshot(spot_return_1s_bps=0.2, futures_return_1s_bps=0.3, seconds_left=240),
        balanced,
    )
    assert neutral["reservationSkewEnabled"] is True
    assert (neutral["upLevels"], neutral["downLevels"]) == (15, 15)
    assert (neutral["upPriceOffsetTicks"], neutral["downPriceOffsetTicks"]) == (0, 0)

    up_heavy = strategy.inventory_state(maker_up_shares=90, maker_down_shares=54)
    tilted = strategy.depth_plan(variant, snapshot(seconds_left=120), up_heavy)
    assert tilted["suspendUp"] is True
    assert tilted["suspendDown"] is False
    assert tilted["upPriceOffsetTicks"] == -2
    assert tilted["downPriceOffsetTicks"] == 2
    assert tilted["downLevels"] <= 7
    orders = strategy.desired_grid(snapshot(), tilted)
    assert orders
    assert all(order["side"] == "DOWN" for order in orders)
    assert all(order["shares"] == 18 for order in orders)


def test_reservation_skew_widens_in_volatility_and_caps_late_depth() -> None:
    variant = next(row for row in strategy.COHORTS if row["cohort"] == "MAKER_RESERVATION_SKEW_SHARED_V1")
    balanced = strategy.inventory_state(maker_up_shares=72, maker_down_shares=72)
    plan = strategy.depth_plan(variant, snapshot(volatility_alert=1, seconds_left=45), balanced)
    assert (plan["upLevels"], plan["downLevels"]) == (3, 3)
    assert (plan["upPriceOffsetTicks"], plan["downPriceOffsetTicks"]) == (-1, -1)
    assert "VOL_WIDEN" in plan["regime"]


def test_cooldown_and_stale_snapshot_fail_closed() -> None:
    inventory = strategy.inventory_state(maker_up_shares=90, maker_down_shares=54)
    cooldown = strategy.decide_taker(
        snapshot(), inventory, expected_market_id=10, now_ms=10_100, last_trade_ms=5_000
    )
    assert cooldown["reason"] == "COOLDOWN"
    stale = strategy.decide_taker(
        snapshot(sampled_at_ms=1_000), inventory,
        expected_market_id=10, now_ms=10_100, last_trade_ms=None,
    )
    assert stale["reason"] == "STALE_PUBLIC_SNAPSHOT"


def test_recentered_pooled_inventory_quotes_only_the_deficient_side_when_heavy() -> None:
    variant = next(
        row for row in strategy.COHORTS
        if row["cohort"] == "RECENTERED_POOLED_INVENTORY_V1"
    )
    balanced = strategy.inventory_state(maker_up_shares=72, maker_down_shares=72)
    neutral = strategy.depth_plan(variant, snapshot(seconds_left=240), balanced)
    assert (neutral["upLevels"], neutral["downLevels"]) == (3, 3)
    assert neutral["pooledInventoryEnabled"] is True
    assert len(strategy.desired_grid(snapshot(), neutral)) == 6

    up_heavy = strategy.inventory_state(maker_up_shares=90, maker_down_shares=72)
    guarded = strategy.depth_plan(variant, snapshot(seconds_left=240), up_heavy)
    assert guarded["suspendUp"] is True
    assert guarded["suspendDown"] is False
    assert guarded["upLevels"] == 0
    assert guarded["downLevels"] == 3
    assert guarded["downPriceOffsetTicks"] > 0
    orders = strategy.desired_grid(snapshot(), guarded)
    assert len(orders) == 3
    assert all(order["side"] == "DOWN" for order in orders)

    assert strategy.pooled_rebalance_required(
        variant, neutral, guarded, fills_in_batch=1
    ) is True
    assert strategy.pooled_rebalance_required(
        variant, neutral, guarded, fills_in_batch=0
    ) is False
    assert strategy.policy(variant)["pooledInventory"]["enabled"] is True


def test_balance_first_matches_repair_depth_to_missing_lots_and_freezes_late_risk() -> None:
    variant = next(
        row for row in strategy.COHORTS
        if row["cohort"] == "BALANCE_FIRST_POOLED_V2"
    )
    balanced = strategy.inventory_state(maker_up_shares=72, maker_down_shares=72)
    neutral = strategy.depth_plan(variant, snapshot(seconds_left=120), balanced)
    assert (neutral["upLevels"], neutral["downLevels"]) == (1, 1)
    assert neutral["regime"] == "BALANCE_FIRST_ONE_PAIR"
    assert len(strategy.desired_grid(snapshot(), neutral)) == 2

    up_heavy = strategy.inventory_state(maker_up_shares=108, maker_down_shares=72)
    repair = strategy.depth_plan(variant, snapshot(seconds_left=120), up_heavy)
    assert repair["suspendUp"] is True
    assert repair["upLevels"] == 0
    assert repair["downLevels"] == 2
    orders = strategy.desired_grid(snapshot(), repair)
    assert len(orders) == 2
    assert all(order["side"] == "DOWN" for order in orders)

    late_flat = strategy.depth_plan(variant, snapshot(seconds_left=59), balanced)
    assert (late_flat["upLevels"], late_flat["downLevels"]) == (0, 0)
    assert late_flat["lateRiskFreeze"] is True
    assert late_flat["forceImmediateRebalance"] is True
    assert strategy.pooled_rebalance_required(
        variant, neutral, late_flat, fills_in_batch=0
    ) is True

    late_repair = strategy.depth_plan(variant, snapshot(seconds_left=59), up_heavy)
    assert late_repair["upLevels"] == 0
    assert late_repair["downLevels"] == 2
    assert late_repair["regime"].endswith("_LATE_REPAIR_ONLY")
    policy = strategy.policy(variant)["pooledInventory"]
    assert policy["balancedLevelsPerSide"] == 1
    assert policy["repairLevelsMatchMissingLots"] is True
    assert policy["stopNewBalancedRiskAtSecondsLeft"] == 60.0
    assert policy["targetPairedCoverage"] == 0.90


def test_target_core_maker_allows_soft_corridor_then_repairs_hard_residual() -> None:
    variant = next(
        row for row in strategy.COHORTS
        if row["cohort"] == "TARGET_CORE_INTEGRATED_V1"
    )
    balanced = strategy.inventory_state(maker_up_shares=180, maker_down_shares=180)
    neutral = strategy.depth_plan(variant, snapshot(seconds_left=180), balanced)
    assert (neutral["upLevels"], neutral["downLevels"]) == (15, 15)
    assert neutral["regime"] == "TARGET_CORE_SOFT_CORRIDOR_TWO_SIDED"

    soft = strategy.inventory_state(maker_up_shares=234, maker_down_shares=180)
    tilted = strategy.depth_plan(variant, snapshot(seconds_left=180), soft)
    assert (tilted["upLevels"], tilted["downLevels"]) == (7, 15)
    assert tilted["suspendUp"] is False
    assert tilted["downPriceOffsetTicks"] == 1

    hard = strategy.inventory_state(maker_up_shares=288, maker_down_shares=180)
    repair = strategy.depth_plan(variant, snapshot(seconds_left=180), hard)
    assert repair["upLevels"] == 0
    assert repair["downLevels"] == 6
    assert repair["suspendUp"] is True
    assert all(order["side"] == "DOWN" for order in strategy.desired_grid(snapshot(), repair))
    assert strategy.pooled_rebalance_required(variant, tilted, repair, fills_in_batch=1) is True

    tail = strategy.depth_plan(variant, snapshot(seconds_left=45), balanced)
    assert (tail["upLevels"], tail["downLevels"]) == (7, 7)
    assert tail["regime"].endswith("_TAIL_SHALLOW")


def test_target_core_taker_uses_continuous_public_score_and_shared_inventory() -> None:
    strong = snapshot(
        predict_up_bid=0.54, predict_up_ask=0.55,
        predict_down_bid=0.44, predict_down_ask=0.45,
        direction_score=0.9,
        futures_return_1s_bps=1.0,
        futures_taker_imbalance_1s=0.9,
        futures_queue_imbalance=0.8,
        spot_return_1s_bps=0.8,
        spot_taker_imbalance_1s=0.8,
    )
    balanced = strategy.inventory_state(maker_up_shares=180, maker_down_shares=180)
    decision = strategy.decide_target_core_taker(
        strong, balanced, expected_market_id=10, now_ms=10_100,
        last_trade_ms=None, last_trade_side=None,
    )
    assert decision["decision"] == "TRADE"
    assert decision["side"] == "UP"
    assert decision["estimatedEdgePerShare"] > 0
    fill = strategy.target_core_taker_execution(decision, strong)
    assert fill is not None
    assert 1 <= fill["principalUsdt"] <= 15
    assert fill["feeUsdt"] == fill["principalUsdt"] * 0.02

    up_heavy = strategy.inventory_state(maker_up_shares=306, maker_down_shares=180)
    blocked = strategy.decide_target_core_taker(
        strong, up_heavy, expected_market_id=10, now_ms=10_100,
        last_trade_ms=None, last_trade_side=None,
    )
    assert blocked["reason"] == "INVENTORY_HARD_BLOCK"

    correcting_snapshot = dict(strong)
    for feature in (
        "direction_score", "futures_return_1s_bps", "futures_taker_imbalance_1s",
        "futures_queue_imbalance", "spot_return_1s_bps", "spot_taker_imbalance_1s",
    ):
        correcting_snapshot[feature] = -abs(float(correcting_snapshot[feature]))
    correction = strategy.decide_target_core_taker(
        correcting_snapshot, up_heavy, expected_market_id=10, now_ms=10_100,
        last_trade_ms=None, last_trade_side=None,
    )
    assert correction["decision"] == "TRADE"
    assert correction["side"] == "DOWN"
    assert correction["correctingResidual"] is True
    corrected_fill = strategy.target_core_taker_execution(correction, correcting_snapshot)
    assert corrected_fill is not None
    assert corrected_fill["principalUsdt"] >= fill["principalUsdt"]
