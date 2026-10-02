from predict_bot import predict_wallet_wide_maker_flow_strategy as strategy


def snapshot(**changes):
    row = {
        "market_id": 10,
        "timestamp_ns": 10_000_000_000,
        "sampled_at_ms": 10_000,
        "predict_receipt_age_ms": 100,
        "seconds_left": 290,
        "predict_up_bid": 0.48,
        "predict_up_ask": 0.49,
        "predict_down_bid": 0.50,
        "predict_down_ask": 0.51,
        "direction_score": 0.4,
        "futures_taker_imbalance_1s": 0.3,
        "spot_taker_imbalance_1s": 0.2,
        "futures_queue_imbalance": -0.1,
    }
    row.update(changes)
    return row


def test_opening_grid_is_wide_legal_and_never_crosses_ask() -> None:
    orders = strategy.opening_grid(snapshot())
    up = [row for row in orders if row["side"] == "UP"]
    down = [row for row in orders if row["side"] == "DOWN"]
    assert (up[0]["price"], up[-1]["price"]) == (0.06, 0.48)
    assert (down[0]["price"], down[-1]["price"]) == (0.06, 0.50)
    assert len(up) == 43
    assert len(down) == 45
    assert all(row["shares"] == 18 for row in orders)
    assert all(row["price"] * row["shares"] >= 1 for row in orders)
    assert strategy.reserved_capital(orders) > 400


def test_refill_modes_require_price_recovery_and_stop_at_30_seconds() -> None:
    static, refill, inner = strategy.COHORTS
    assert strategy.may_refill(static, side="UP", price_tick=40, snapshot=snapshot())[0] is False
    assert strategy.may_refill(refill, side="UP", price_tick=40, snapshot=snapshot())[0] is True
    assert strategy.may_refill(refill, side="UP", price_tick=49, snapshot=snapshot())[1] == "WAIT_PRICE_RECOVERY"
    assert strategy.may_refill(refill, side="UP", price_tick=40, snapshot=snapshot(seconds_left=30))[1] == "REFILL_CUTOFF_30S"
    assert strategy.may_refill(inner, side="UP", price_tick=42, snapshot=snapshot())[0] is True
    assert strategy.may_refill(inner, side="UP", price_tick=40, snapshot=snapshot())[1] == "OUTSIDE_INNER_7"


def test_maker_flow_uses_only_strict_prior_one_to_five_seconds() -> None:
    events = [
        {"side": "UP", "shares": 18, "filledAtMs": 9_500},
        {"side": "UP", "shares": 36, "filledAtMs": 8_000},
        {"side": "DOWN", "shares": 18, "filledAtMs": 6_000},
        {"side": "DOWN", "shares": 180, "filledAtMs": 4_000},
    ]
    flow = strategy.maker_flow(events, now_ms=10_000)
    assert flow["events"] == 2
    assert flow["deltaShares"] == 18
    assert flow["side"] == "UP"


def test_primary_alpha_needs_micro_confirmation_and_inventory_guard() -> None:
    flow = {"side": "UP", "deltaShares": 36, "events": 2}
    balanced = strategy.inventory_state(maker_up_shares=72, maker_down_shares=72)
    trade = strategy.primary_decision(
        snapshot(), balanced, flow, expected_market_id=10, now_ms=10_100, last_trade_ms=None
    )
    assert trade["decision"] == "TRADE"
    assert trade["side"] == "UP"
    assert trade["microstructure"]["supportVotes"] >= 2

    hard = strategy.inventory_state(
        maker_up_shares=270, maker_down_shares=54, maker_up_cost=130, maker_down_cost=25
    )
    blocked = strategy.primary_decision(
        snapshot(), hard, flow, expected_market_id=10, now_ms=10_100, last_trade_ms=None
    )
    assert blocked["decision"] == "SKIP"
    assert blocked["reason"] == "HARD_SKIP_SAME_DIRECTION"


def test_tail_insurance_is_min1_opposite_and_budget_limited() -> None:
    inventory = strategy.inventory_state(
        maker_up_shares=100, maker_down_shares=80,
        maker_up_cost=70, maker_down_cost=20,
        primary_up_principal=50,
    )
    decision = strategy.tail_decision(
        snapshot(seconds_left=45, predict_down_ask=0.08), inventory,
        expected_market_id=10, now_ms=10_100, last_trade_ms=None, tail_parents=0,
    )
    assert decision["decision"] == "TRADE"
    assert decision["side"] == "DOWN"
    assert decision["budgetUsdt"] == 1
    fill = strategy.tail_execution("DOWN", snapshot(predict_down_ask=0.08))
    assert fill is not None
    assert fill["principalUsdt"] == 1
    assert fill["shares"] == 12.5

    spent = strategy.inventory_state(
        maker_up_shares=100, maker_down_shares=80,
        maker_up_cost=70, maker_down_cost=20,
        primary_up_principal=50, tail_principal=1,
    )
    blocked = strategy.tail_decision(
        snapshot(sampled_at_ms=29_900, seconds_left=20, predict_down_ask=0.05), spent,
        expected_market_id=10, now_ms=30_000, last_trade_ms=None, tail_parents=1,
    )
    assert blocked["reason"] == "TAIL_BUDGET_EXHAUSTED"


def test_policy_is_forward_paper_only() -> None:
    policy = strategy.policy(strategy.COHORTS[1])
    assert policy["paperOnly"] is True
    assert policy["forwardOnly"] is True
    assert policy["targetEventsUsed"] is False
    assert policy["tailInsurance"]["principalPerParentUsdt"] == 1
