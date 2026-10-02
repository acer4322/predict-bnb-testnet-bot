from __future__ import annotations

from predict_bot import predict_wallet_reconstructed_maker_strategy_v3 as strategy


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


def order(side: str = "UP", tick: int = 45):
    return {"side": side, "priceTick": tick, "price": tick * 0.01, "shares": 18.0}


def test_v3_keeps_same_opening_and_fill_proxy_as_v2_control() -> None:
    opening = strategy.opening_orders(snapshot())
    assert sum(item["side"] == "UP" for item in opening) == 5
    assert sum(item["side"] == "DOWN" for item in opening) == 5
    assert strategy.SHARES_PER_ORDER == 18.0
    assert strategy.POST_FILL_DECISION_DELAY_MS == 1200
    filled = {**order(), "placedAtMs": 8_000}
    assert strategy.ask_touch_fill(filled, snapshot(predict_up_ask=0.45), now_ms=10_000) is True


def test_v3_defaults_ambiguous_reprice_away_one_tick() -> None:
    inv = strategy.inventory(18, 18, 8.1, 8.1)
    plan = strategy.post_fill_plan(snapshot(), inv, order("UP", 45))
    assert plan["action"] == "REPRICE"
    assert plan["reprice"]["direction"] == "AWAY_FROM_TOUCH"
    assert plan["targetPriceTick"] == 44
    assert plan["signedTicksTowardTouch"] == -1


def test_v3_moves_toward_touch_only_on_coherent_support() -> None:
    inv = strategy.inventory(18, 18, 8.1, 8.1)
    plan = strategy.post_fill_plan(
        snapshot(
            direction_score=0.7,
            spot_queue_imbalance=0.7,
            futures_queue_imbalance=0.6,
            predict_up_bid=0.49,
            predict_up_ask=0.52,
        ),
        inv,
        order("UP", 45),
    )
    assert plan["action"] == "REPRICE"
    assert plan["reprice"]["direction"] == "TOWARD_TOUCH"
    assert plan["targetPriceTick"] > 45
    assert 1 <= plan["targetPriceTick"] - 45 <= 3


def test_v3_stops_when_fill_worsens_two_lot_inventory() -> None:
    inv = strategy.inventory(54, 18, 24.0, 8.0)
    plan = strategy.post_fill_plan(snapshot(), inv, order("UP", 45))
    assert plan["action"] == "STOP"
    assert "TWO_LOT_OVERWEIGHT" in plan["continuation"]["reasons"]


def test_v3_same_price_refill_is_narrow_inventory_repair_case() -> None:
    # DOWN remains overweight after an UP fill, so another UP unit repairs the
    # residual. Neutral public flow is the only situation that allows same-price refill.
    inv = strategy.inventory(36, 54, 16.0, 25.0)
    plan = strategy.post_fill_plan(snapshot(), inv, order("UP", 45))
    assert plan["action"] == "SAME_PRICE_REFILL"
    assert plan["targetPriceTick"] == 45


def test_v3_policy_does_not_claim_partial_fill_path_runtime_signal() -> None:
    policy = strategy.policy()
    assert policy["targetEventsDriveStrategy"] is False
    assert policy["controlCohort"] == "TARGET_MAKER_RULES_GRID18_SOFTPOOL_V2"
    assert "partial-fill" in policy["limitations"].lower()
