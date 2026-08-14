from __future__ import annotations

from predict_bot import predict_wallet_target_taker_public_side_strategy_v1 as strategy


class _FakeSideModel:
    classes_ = [0, 1]

    def predict_proba(self, frame):
        spot = float(frame.iloc[0]["spot_minus_strike_bps"])
        p_up = 0.80 if spot >= 0 else 0.20
        return [[1.0 - p_up, p_up]]


def _bundle():
    return {
        "reportVersion": strategy.EXPECTED_REPORT_VERSION,
        "task": "side_up",
        "features": list(strategy.SIDE_EBM_EXPECTED_FEATURES),
        "model": _FakeSideModel(),
        "researchOnly": True,
        "automaticStrategyPromotion": False,
        "path": "fake-side-up.joblib",
    }


def _snapshot(**overrides):
    row = {
        "market_id": 123,
        "sampled_at_ms": 10_000,
        "predict_receipt_age_ms": 100.0,
        "seconds_left": 120.0,
        "predict_up_bid": 0.53,
        "predict_up_ask": 0.55,
        "predict_up_mid": 0.54,
        "predict_down_bid": 0.45,
        "predict_down_ask": 0.47,
        "predict_down_mid": 0.46,
        "spot_minus_strike_bps": 4.0,
        "chainlink_minus_strike_bps": 3.0,
        "direction_score": 0.35,
        "spot_queue_imbalance": 0.25,
        "spot_taker_imbalance_1s": 0.20,
        "spot_return_1s_bps": 1.0,
        "spot_return_3s_bps": 2.0,
        "futures_queue_imbalance": 0.30,
        "futures_taker_imbalance_1s": 0.22,
        "futures_return_1s_bps": 1.2,
        "futures_return_3s_bps": 2.2,
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


def test_public_side_uses_frozen_model_probability_for_up() -> None:
    decision = strategy.decide_side(
        _snapshot(), _bundle(), expected_market_id=123, now_ms=10_200
    )
    assert decision["decision"] == "TRADE"
    assert decision["side"] == "UP"
    assert decision["signal"]["probabilityUp"] == 0.80
    assert decision["signal"]["selectedProbability"] >= strategy.SIDE_PROBABILITY_THRESHOLD


def test_public_side_uses_frozen_model_probability_for_down() -> None:
    decision = strategy.decide_side(
        _snapshot(spot_minus_strike_bps=-4.0),
        _bundle(),
        expected_market_id=123,
        now_ms=10_200,
    )
    assert decision["decision"] == "TRADE"
    assert decision["side"] == "DOWN"
    assert decision["signal"]["probabilityUp"] == 0.20


def test_missing_side_model_fails_closed() -> None:
    decision = strategy.decide_side(
        _snapshot(), None, expected_market_id=123, now_ms=10_200
    )
    assert decision["decision"] == "SKIP"
    assert decision["reason"] == "SIDE_EBM_MODEL_UNAVAILABLE"


def test_side_model_feature_contract_contains_no_target_or_chosen_side_leakage() -> None:
    assert tuple(strategy.SIDE_EBM_EXPECTED_FEATURES) == (
        "seconds_left",
        "predict_up_mid",
        "predict_up_spread",
        "predict_down_spread",
        "spot_minus_strike_bps",
        "chainlink_minus_strike_bps",
        "direction_score",
        "spot_queue_imbalance",
        "spot_taker_imbalance_1s",
        "spot_return_1s_bps",
        "spot_return_3s_bps",
        "futures_queue_imbalance",
        "futures_taker_imbalance_1s",
        "futures_return_1s_bps",
        "futures_return_3s_bps",
        "signal_age_ms",
    )
    for feature in strategy.SIDE_EBM_EXPECTED_FEATURES:
        assert not feature.startswith(strategy.FORBIDDEN_RUNTIME_FEATURE_PREFIXES)


def test_forward_feature_row_derives_spreads_and_signal_age_without_target_data() -> None:
    signal = strategy.public_side_score(_snapshot(), _bundle(), now_ms=10_200)
    features = signal["features"]
    assert features["predict_up_spread"] == 0.02
    assert features["predict_down_spread"] == 0.02
    assert features["signal_age_ms"] == 200.0
    assert set(features) == set(strategy.SIDE_EBM_EXPECTED_FEATURES)


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
    decision = strategy.decide_side(
        _snapshot(), _bundle(), expected_market_id=123, now_ms=10_200
    )
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
    assert policy["sideModelReportVersion"] == strategy.EXPECTED_REPORT_VERSION
    assert policy["cohorts"][strategy.SIDE_ONLY_COHORT]["makerAnchorRequired"] is False
    assert policy["cohorts"][strategy.HAZARD_SIDE_COHORT]["makerAnchorRequired"] is True
    assert "own Maker fill side" in policy["cohorts"][strategy.HAZARD_SIDE_COHORT]["hazardPriceAlignment"]
