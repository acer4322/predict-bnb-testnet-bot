from predict_bot import predict_wallet_taker_signal_strategy as strategy


def snapshot(**overrides):
    value = {
        "timestamp_ns": 10_000_000_000,
        "sampled_at_ms": 10_000,
        "market_id": 123,
        "seconds_left": 120,
        "predict_receipt_age_ms": 100,
        "predict_up_ask": 0.60,
        "predict_down_ask": 0.41,
        "direction_score": 0.2,
        "futures_taker_imbalance_1s": 0.4,
        "spot_taker_imbalance_250ms": 0.3,
        "futures_queue_imbalance": -0.1,
    }
    value.update(overrides)
    return value


def test_three_of_four_consensus_trades_without_target_input():
    result = strategy.decide(
        snapshot(), expected_market_id=123, now_ms=10_250,
        last_trade_ms=None, last_trade_side=None,
    )
    assert result["decision"] == "TRADE"
    assert result["side"] == "UP"
    assert result["upVotes"] == 3
    assert result["downVotes"] == 1


def test_split_vote_and_stale_sample_fail_closed():
    split = strategy.decide(
        snapshot(spot_taker_imbalance_250ms=-0.3), expected_market_id=123,
        now_ms=10_250, last_trade_ms=None, last_trade_side=None,
    )
    assert split["decision"] == "SKIP"
    assert split["reason"] == "NO_3_OF_4_CONSENSUS"

    stale = strategy.decide(
        snapshot(), expected_market_id=123, now_ms=11_001,
        last_trade_ms=None, last_trade_side=None,
    )
    assert stale["decision"] == "SKIP"
    assert stale["reason"] == "STALE_SIGNAL"


def test_repeat_and_flip_have_separate_cooldowns():
    repeated_snapshot = snapshot(sampled_at_ms=14_750)
    repeated = strategy.decide(
        repeated_snapshot, expected_market_id=123, now_ms=15_000,
        last_trade_ms=10_000, last_trade_side="UP",
    )
    assert repeated["reason"] == "COOLDOWN"

    flipped_snapshot = snapshot(
        direction_score=-0.2,
        futures_taker_imbalance_1s=-0.4,
        spot_taker_imbalance_250ms=-0.3,
        futures_queue_imbalance=0.1,
    )
    flipped_snapshot["sampled_at_ms"] = 12_500
    flipped = strategy.decide(
        flipped_snapshot, expected_market_id=123, now_ms=12_750,
        last_trade_ms=10_000, last_trade_side="UP",
    )
    assert flipped["decision"] == "TRADE"
    assert flipped["side"] == "DOWN"


def test_fixed_principal_execution_includes_taker_fee():
    fill = strategy.execution("UP", snapshot())
    assert fill is not None
    assert fill["principalUsdt"] == 5.0
    assert fill["feeUsdt"] == 0.1
    assert fill["totalCostUsdt"] == 5.1
    assert abs(fill["shares"] - 5.0 / 0.60) < 1e-9
