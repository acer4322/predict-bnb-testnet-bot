from __future__ import annotations

import pytest

from predict_bot import predict_wallet_maker_ebm_strategy_v1 as strategy


class _FakeModel:
    classes_ = [0, 1]

    def __init__(self, probability: float) -> None:
        self.probability = probability

    def predict_proba(self, frame):
        p = float(self.probability)
        return [[1.0 - p, p]]


def _bundle(task: str, features: tuple[str, ...], probability: float) -> dict:
    return {
        "reportVersion": strategy.EXPECTED_REPORT_VERSION,
        "task": task,
        "features": list(features),
        "model": _FakeModel(probability),
        "researchOnly": True,
        "automaticStrategyPromotion": False,
        "path": f"fake-{task}.joblib",
    }


def _models(*, hazard: float = 0.80, level: float = 0.75) -> dict:
    return {
        "hazard": _bundle("hazard_5s_compact", strategy.HAZARD_FEATURES, hazard),
        "level": _bundle("level_2ticks_compact", strategy.LEVEL_FEATURES, level),
    }


def _snapshot(**overrides) -> dict:
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


def test_hazard_only_quotes_when_frozen_hazard_probability_passes() -> None:
    decision = strategy.decide(
        _snapshot(), _models(hazard=0.80), strategy.inventory(0, 0),
        cohort=strategy.HAZARD_ONLY_COHORT, expected_market_id=123, now_ms=10_200,
    )
    assert decision["decision"] == "QUOTE"
    assert decision["hazard"]["probability"] == pytest.approx(0.80)
    assert {order["side"] for order in decision["orders"]} == {"UP", "DOWN"}
    assert all(order["offsetTicks"] >= strategy.FIXED_HAZARD_ONLY_OFFSET_TICKS for order in decision["orders"])


def test_hazard_only_cancels_when_probability_is_below_threshold() -> None:
    decision = strategy.decide(
        _snapshot(), _models(hazard=0.40), strategy.inventory(0, 0),
        cohort=strategy.HAZARD_ONLY_COHORT, expected_market_id=123, now_ms=10_200,
    )
    assert decision["decision"] == "IDLE"
    assert decision["reason"] == "HAZARD_EBM_INACTIVE"


def test_level_only_bypasses_hazard_and_uses_level_ebm() -> None:
    decision = strategy.decide(
        _snapshot(), _models(hazard=0.10, level=0.80), strategy.inventory(0, 0),
        cohort=strategy.LEVEL_ONLY_COHORT, expected_market_id=123, now_ms=10_200,
    )
    assert decision["decision"] == "QUOTE"
    assert decision["hazard"]["used"] is False
    assert decision["levels"]["UP"]["probability"] == pytest.approx(0.80)
    assert decision["levels"]["UP"]["offsetTicks"] == 0


def test_combined_uses_own_inventory_to_choose_deficient_side() -> None:
    inventory = strategy.inventory(36, 18)
    decision = strategy.decide(
        _snapshot(), _models(), inventory,
        cohort=strategy.COMBINED_COHORT, expected_market_id=123, now_ms=10_200,
    )
    assert decision["decision"] == "QUOTE"
    assert [order["side"] for order in decision["orders"]] == ["DOWN"]
    assert decision["inventory"]["deltaShares"] == pytest.approx(18.0)


def test_level_feature_row_conditions_only_after_own_side_is_selected() -> None:
    up = strategy.level_feature_row(_snapshot(), "UP")
    down = strategy.level_feature_row(_snapshot(), "DOWN")
    assert up["label_side_up"] == 1.0
    assert down["label_side_up"] == 0.0
    assert up["chosen_predict_bid"] == pytest.approx(0.53)
    assert down["chosen_predict_bid"] == pytest.approx(0.45)
    assert set(up) == set(strategy.LEVEL_FEATURES)


def test_hazard_feature_contract_has_no_target_or_chosen_side_leakage() -> None:
    for feature in strategy.HAZARD_FEATURES:
        assert not feature.startswith(strategy.FORBIDDEN_HAZARD_PREFIXES)


def test_pair_price_cap_is_preserved() -> None:
    decision = strategy.decide(
        _snapshot(predict_up_bid=0.60, predict_down_bid=0.60), _models(level=0.80),
        strategy.inventory(0, 0), cohort=strategy.LEVEL_ONLY_COHORT,
        expected_market_id=123, now_ms=10_200,
    )
    assert decision["decision"] == "QUOTE"
    assert sum(float(order["price"]) for order in decision["orders"]) <= strategy.MAX_PAIR_PRICE_SUM + 1e-12


def test_ask_touch_requires_later_snapshot_and_minimum_rest() -> None:
    order = {
        "side": "UP",
        "price": 0.53,
        "priceTick": 53,
        "shares": 18.0,
        "placedAtMs": 10_000,
        "placedSnapshotNs": 10_000_000_000,
    }
    snapshot = _snapshot(predict_up_ask=0.52)
    assert not strategy.ask_touch_fill(order, snapshot, snapshot_ns=10_000_000_000, now_ms=10_500)
    assert not strategy.ask_touch_fill(order, snapshot, snapshot_ns=10_100_000_000, now_ms=10_100)
    assert strategy.ask_touch_fill(order, snapshot, snapshot_ns=10_300_000_000, now_ms=10_300)


def test_tail_freeze_blocks_new_quotes() -> None:
    decision = strategy.decide(
        _snapshot(seconds_left=29.0), _models(), strategy.inventory(0, 0),
        cohort=strategy.COMBINED_COHORT, expected_market_id=123, now_ms=10_200,
    )
    assert decision["decision"] == "IDLE"
    assert decision["reason"] == "TAIL_FREEZE"


def test_policy_is_forward_paper_and_does_not_promote_side_ebm() -> None:
    policy = strategy.policy()
    assert policy["paperOnly"] is True
    assert policy["forwardOnly"] is True
    assert policy["targetEventsDriveStrategy"] is False
    assert policy["liveOrdersAffected"] is False
    assert policy["sharesPerOrder"] == 18.0
    assert "not promoted" in policy["makerSideEbm"]
