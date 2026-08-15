from predict_bot import predict_wallet_reconstructed_maker_strategy_v2 as strategy


def snapshot(**changes):
    row = {
        "market_id": 10,
        "sampled_at_ms": 10_000,
        "predict_receipt_age_ms": 50,
        "seconds_left": 180,
        "predict_up_bid": 0.49,
        "predict_up_ask": 0.50,
        "predict_down_bid": 0.50,
        "predict_down_ask": 0.51,
        "direction_score": 0.0,
        "futures_return_3s_bps": 0.0,
        "spot_return_3s_bps": 0.0,
        "futures_taker_imbalance_1s": 0.0,
        "spot_taker_imbalance_1s": 0.0,
        "futures_queue_imbalance": 0.0,
        "spot_queue_imbalance": 0.0,
    }
    row.update(changes)
    return row


def test_v2_uses_five_near_touch_levels_not_wide_rails() -> None:
    orders = strategy.opening_orders(snapshot())
    assert sum(order["side"] == "UP" for order in orders) == 5
    assert sum(order["side"] == "DOWN" for order in orders) == 5
    assert min(order["price"] for order in orders if order["side"] == "UP") >= 0.45
    assert strategy.policy()["activeBandLevelsPerSide"] == 5


def test_v2_pair_first_quotes_only_deficient_side_after_one_lot_residual() -> None:
    inventory = strategy.inventory(54, 36, 25.0, 18.0)
    levels = strategy.quote_levels_by_side(inventory)
    assert levels == {"UP": 0, "DOWN": 1}
    orders = strategy.desired_orders(snapshot(), inventory)
    assert orders
    assert all(order["side"] == "DOWN" for order in orders)

    inventory = strategy.inventory(108, 36, 50.0, 18.0)
    levels = strategy.quote_levels_by_side(inventory)
    assert levels == {"UP": 0, "DOWN": 4}


def test_v2_toxic_flow_blocks_falling_token_side() -> None:
    flow = strategy.toxic_flow(snapshot(
        direction_score=0.8,
        futures_return_3s_bps=2.2,
        spot_return_3s_bps=2.0,
        futures_taker_imbalance_1s=0.9,
    ))
    assert flow["toxic"] is True
    assert flow["pressureSide"] == "UP"
    assert flow["cancelSide"] == "DOWN"

    balanced = strategy.inventory(36, 36, 18.0, 18.0)
    orders = strategy.desired_orders(snapshot(), balanced, blocked_sides={flow["cancelSide"]})
    assert orders
    assert all(order["side"] == "UP" for order in orders)


def test_v2_keeps_v1_fill_proxy_and_core_unit_for_clean_ab() -> None:
    order = {"side": "UP", "price": 0.48, "placedAtMs": 9_000}
    assert strategy.ask_touch_fill(order, snapshot(predict_up_ask=0.48), now_ms=10_100) is True
    assert strategy.SHARES_PER_ORDER == 18
    assert strategy.GRID == 0.01
    assert strategy.MAX_PAIR_PRICE_SUM == 0.99
