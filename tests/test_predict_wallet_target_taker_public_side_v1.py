from __future__ import annotations

from predict_bot import predict_wallet_target_taker_public_side_strategy_v1 as strategy


def _snapshot(**overrides):
    row = {
        "market_id": 123,
        "sampled_at_ms": 10_000,
        "predict_receipt_age_ms": 100.0,
        "seconds_left": 120.0,
        "predict_up_ask": 0.55,
        "predict_down_ask": 0.47,
        "predict_up_mid": 0.54,
        "predict_down_mid": 0.46,
        "spot_minus_strike_bps": 4.0,
        "futures_queue_imbalance": 0.30,
        "futures_return_1s_bps": 1.2,
        "spot_queue_imbalance": 0.25,
        "direction_score": 0.35,
    }
    row.update(overrides)
    return row


def _eligibility(*, maker_side: str = "UP") -> dict:
    return {
        "openedAtMs": 10_000,
        "expiresAtMs": 15_000,
        "openedSnapshotNs": 10_000_000_000,
        "makerAnchorSide": maker_side,
    }


def test_public_side_rule_selects_up_from_strong_positive_public_state() -> None:
    decision = strategy.decide_side(_snapshot(), expected_market_id=123, now_ms=10_200)
    assert decision["decision"] == "TRADE"
    assert decision["side"] == "UP"
    assert decision["signal"]["confidence"] >= strategy.SIDE_SCORE_THRESHOLD


def test_public_side_rule_selects_down_from_strong_negative_public_state() -> None:
    decision = strategy.decide_side(
        _snapshot(
            predict_up_ask=0.43,
            predict_down_ask=0.59,
            predict_up_mid=0.42,
            predict_down_mid=0.58,
            spot_minus_strike_bps=-5.0,
            futures_queue_imbalance=-0.35,
            futures_return_1s_bps=-1.5,
            spot_queue_imbalance=-0.30,
            direction_score=-0.40,
        ),
        expected_market_id=123,
        now_ms=10_200,
    )
    assert decision["decision"] == "TRADE"
    assert decision["side"] == "DOWN"


def test_runtime_side_feature_contract_contains_no_target_or_chosen_side_leakage() -> None:
    features = [item[0] for item in strategy.SIDE_FEATURES]
    for feature in features:
        assert not feature.startswith(strategy.FORBIDDEN_RUNTIME_FEATURE_PREFIXES)
    assert "predict_up_mid" in features
    assert "spot_minus_strike_bps" in features
    assert "futures_queue_imbalance" in features


def test_hazard_gate_forbids_same_snapshot_as_own_maker_fill() -> None:
    result = strategy.hazard_gate(
        _snapshot(seconds_left=50.0),
        _eligibility(),
        snapshot_ns=10_000_000_000,
        now_ms=10_100,
    )
    assert result["eligible"] is False
    assert result["reason"] == "SAME_MAKER_FILL_SNAPSHOT_FORBIDDEN"


def test_hazard_gate_accepts_next_snapshot_in_late_mid_price_state() -> None:
    result = strategy.hazard_gate(
        _snapshot(seconds_left=50.0, predict_up_mid=0.55, predict_down_mid=0.45),
        _eligibility(maker_side="UP"),
        snapshot_ns=10_250_000_000,
        now_ms=10_250,
    )
    assert result["eligible"] is True
    assert result["reason"] == "HAZARD_TIME_PRICE_MATCH"
    assert result["makerAnchorSide"] == "UP"
    assert result["makerSidePredictMid"] == 0.55
    assert result["eligibilityScore"] >= strategy.HAZARD_SCORE_THRESHOLD


def test_hazard_gate_uses_maker_side_price_not_taker_candidate_side() -> None:
    # A DOWN Maker fill must use DOWN mid=.10 even if the public Side engine
    # happens to prefer UP. This preserves the historical hazard feature meaning.
    result = strategy.hazard_gate(
        _snapshot(seconds_left=120.0, predict_up_mid=0.90, predict_down_mid=0.10),
        _eligibility(maker_side="DOWN"),
        snapshot_ns=10_250_000_000,
        now_ms=10_250,
    )
    assert result["makerAnchorSide"] == "DOWN"
    assert result["makerSidePredictMid"] == 0.10
    assert result["priceRegime"] == "LOW_LT_033"


def test_hazard_gate_rejects_early_extreme_price_state() -> None:
    result = strategy.hazard_gate(
        _snapshot(seconds_left=240.0, predict_up_mid=0.90, predict_down_mid=0.10),
        _eligibility(maker_side="UP"),
        snapshot_ns=10_250_000_000,
        now_ms=10_250,
    )
    assert result["eligible"] is False
    assert result["reason"] == "HAZARD_TIME_PRICE_TOO_WEAK"


def test_hazard_gate_rejects_ambiguous_dual_side_maker_fill() -> None:
    result = strategy.hazard_gate(
        _snapshot(seconds_left=50.0),
        _eligibility(maker_side=""),
        snapshot_ns=10_250_000_000,
        now_ms=10_250,
    )
    assert result["eligible"] is False
    assert result["reason"] == "AMBIGUOUS_OWN_MAKER_FILL_SIDE"


def test_execution_is_fixed_one_dollar_stake_and_fee_adjusted() -> None:
    decision = strategy.decide_side(_snapshot(), expected_market_id=123, now_ms=10_200)
    fill = strategy.execution(decision)
    assert fill is not None
    assert fill["stakeUsdt"] == strategy.STAKE_USDT == 1.0
    assert fill["effectiveUnitCost"] > fill["ask"]
    assert abs(fill["shares"] * fill["effectiveUnitCost"] - 1.0) < 1e-12


def test_policy_keeps_side_only_and_hazard_side_separate_and_paper_only() -> None:
    policy = strategy.policy()
    assert policy["paperOnly"] is True
    assert policy["automaticStrategyPromotion"] is False
    assert policy["targetEventsDriveRuntime"] is False
    assert policy["cohorts"][strategy.SIDE_ONLY_COHORT]["makerAnchorRequired"] is False
    assert policy["cohorts"][strategy.HAZARD_SIDE_COHORT]["makerAnchorRequired"] is True
    assert "own Maker fill side" in policy["cohorts"][strategy.HAZARD_SIDE_COHORT]["hazardPriceAlignment"]
