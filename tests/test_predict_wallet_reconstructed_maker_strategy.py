from predict_bot import predict_wallet_reconstructed_maker_strategy as strategy


def snapshot(*, seconds_left: float = 280, up_bid: float = 0.52, down_bid: float = 0.47) -> dict:
    return {
        "market_id": 123,
        "sampled_at_ms": 10_000,
        "predict_receipt_age_ms": 100,
        "seconds_left": seconds_left,
        "predict_up_bid": up_bid,
        "predict_up_ask": up_bid + 0.01,
        "predict_down_bid": down_bid,
        "predict_down_ask": down_bid + 0.01,
    }


def test_opening_rails_obey_cent_grid_minimum_and_ask_boundary() -> None:
    orders = strategy.opening_orders(snapshot())
    assert orders
    assert {order["side"] for order in orders} == {"UP", "DOWN"}
    assert min(order["price"] for order in orders) == 0.06
    assert max(order["price"] for order in orders if order["side"] == "UP") == 0.52
    assert max(order["price"] for order in orders if order["side"] == "DOWN") == 0.47
    assert all(order["price"] * order["shares"] >= 1 for order in orders)
    assert all(abs(order["price"] * 100 - round(order["price"] * 100)) < 1e-9 for order in orders)


def test_active_band_has_28_levels_and_respects_pair_sum() -> None:
    inventory = strategy.inventory(180, 180, 80, 80)
    orders = strategy.active_band_orders(snapshot(), inventory)
    up = [order for order in orders if order["side"] == "UP"]
    down = [order for order in orders if order["side"] == "DOWN"]
    assert len(up) == strategy.ACTIVE_BAND_LEVELS
    assert len(down) == strategy.ACTIVE_BAND_LEVELS
    assert all(order["price"] + (0.47 if order["side"] == "UP" else 0.52) <= 0.99 + 1e-9 for order in orders)


def test_soft_pool_guard_blocks_only_the_overweight_side() -> None:
    inventory = strategy.inventory(120, 80, 50, 35)
    assert inventory["imbalanceRatio"] == 0.2
    assert strategy.may_quote_side("UP", inventory)[0] is False
    assert strategy.may_quote_side("DOWN", inventory)[0] is True
    orders = strategy.active_band_orders(snapshot(), inventory)
    assert orders
    assert {order["side"] for order in orders} == {"DOWN"}


def test_refill_freezes_at_30_seconds_without_invalidating_snapshot() -> None:
    current = snapshot(seconds_left=30)
    assert strategy.snapshot_is_usable(current, market_id=123, now_ms=10_500) == (True, "OK")
    assert strategy.active_band_orders(current, strategy.inventory(0, 0, 0, 0)) == []


def test_policy_is_paper_only_and_target_independent() -> None:
    policy = strategy.policy()
    assert policy["paperOnly"] is True
    assert policy["forwardOnly"] is True
    assert policy["historicalBackfill"] is False
    assert policy["targetEventsDriveStrategy"] is False
    assert policy["liveOrdersAffected"] is False
