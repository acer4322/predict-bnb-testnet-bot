from predict_bot import predict_wallet_maker_grid_strategy as strategy


def snapshot(**changes):
    row = {
        "market_id": 10,
        "sampled_at_ms": 10_000,
        "predict_receipt_age_ms": 100,
        "seconds_left": 120,
        "predict_up_bid": 0.48,
        "predict_up_ask": 0.49,
        "predict_down_bid": 0.50,
        "predict_down_ask": 0.51,
    }
    row.update(changes)
    return row


def test_depth_variants_differ_only_in_grid_depth() -> None:
    counts = [len(strategy.desired_grid(snapshot(), row["levels"])) for row in strategy.COHORTS]
    assert counts == [6, 14, 30]
    for order in strategy.desired_grid(snapshot(), 15):
        assert order["shares"] == 18
        assert order["price"] * order["shares"] >= 1


def test_complement_cap_and_minimum_order_are_enforced_atomically() -> None:
    orders = strategy.desired_grid(snapshot(predict_up_bid=0.93, predict_up_ask=0.94, predict_down_bid=0.06, predict_down_ask=0.07), 15)
    pairs = {}
    for order in orders:
        pairs.setdefault(order["level"], {})[order["side"]] = order["price"]
    assert pairs
    assert all(set(pair) == {"UP", "DOWN"} for pair in pairs.values())
    assert all(pair["UP"] + pair["DOWN"] <= 0.99 for pair in pairs.values())
    assert all(min(pair.values()) * 18 >= 1 for pair in pairs.values())


def test_fill_requires_resting_order_and_later_ask_touch() -> None:
    order = {"status": "ACTIVE", "side": "UP", "price": 0.48, "placed_at_ms": 10_000}
    assert not strategy.ask_touch_fill(order, snapshot(predict_up_ask=0.48), now_ms=10_200)
    assert strategy.ask_touch_fill(order, snapshot(predict_up_ask=0.48), now_ms=10_300)
    assert not strategy.ask_touch_fill(order, snapshot(predict_up_ask=0.49), now_ms=10_300)


def test_cutoff_and_recenter_gate() -> None:
    usable, reason = strategy.snapshot_is_usable(snapshot(seconds_left=30), market_id=10, now_ms=10_100)
    assert not usable and reason == "AFTER_MAKER_ACTIVE_WINDOW"
    assert not strategy.should_recenter((0.48, 0.50), (0.46, 0.52), last_recenter_ms=8_000, now_ms=10_000)
    assert strategy.should_recenter((0.48, 0.50), (0.46, 0.52), last_recenter_ms=4_000, now_ms=10_000)
