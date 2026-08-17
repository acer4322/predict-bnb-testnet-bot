from __future__ import annotations

import threading

from predict_bot import predict_wallet_target_taker_public_side_strategy_v1 as strategy
from predict_bot import target_taker_public_side_test_v2 as service_v2


class NeverCalledModel:
    classes_ = [0, 1]

    def __init__(self) -> None:
        self.calls = 0

    def predict_proba(self, _frame):
        self.calls += 1
        raise AssertionError("predict_proba must not run with incomplete frozen features")


def complete_snapshot() -> dict[str, float | int]:
    return {
        "sampledAtMs": 1_000_000,
        "marketId": 123,
        "secondsLeft": 120.0,
        "predictUpBid": 0.54,
        "predictUpAsk": 0.56,
        "predictUpMid": 0.55,
        "predictDownBid": 0.44,
        "predictDownAsk": 0.46,
        "predictDownMid": 0.45,
        "predictReceiptAgeMs": 100.0,
        "spotMinusStrikeBps": 3.0,
        "chainlinkMinusStrikeBps": 2.0,
        "directionScore": 0.25,
        "spotQueueImbalance": 0.2,
        "spotTakerImbalance1s": 0.15,
        "spotReturn1sBps": 1.5,
        "spotReturn3sBps": 2.5,
        "futuresQueueImbalance": 0.18,
        "futuresTakerImbalance1s": 0.12,
        "futuresReturn1sBps": 1.2,
        "futuresReturn3sBps": 2.2,
    }


def bundle(model) -> dict[str, object]:
    return {
        "model": model,
        "features": list(strategy.SIDE_EBM_EXPECTED_FEATURES),
        "path": "fake.joblib",
        "reportVersion": strategy.EXPECTED_REPORT_VERSION,
    }


def test_missing_single_frozen_feature_never_calls_model() -> None:
    model = NeverCalledModel()
    snapshot = complete_snapshot()
    snapshot.pop("spotQueueImbalance")

    signal = strategy.public_side_score(snapshot, bundle(model), now_ms=1_000_100)

    assert signal["status"] == "FEATURES_INCOMPLETE"
    assert signal["featureInputComplete"] is False
    assert signal["availableFeatureCount"] == 15
    assert signal["requiredFeatureCount"] == 16
    assert signal["missingFeatures"] == ["spot_queue_imbalance"]
    assert signal["probabilityUp"] is None
    assert signal["probabilityDown"] is None
    assert signal["selectedProbability"] is None
    assert model.calls == 0


def test_decision_is_fail_closed_and_execution_has_defense_in_depth() -> None:
    model = NeverCalledModel()
    snapshot = complete_snapshot()
    snapshot.pop("chainlinkMinusStrikeBps")

    decision = strategy.decide_side(
        snapshot,
        bundle(model),
        expected_market_id=123,
        now_ms=1_000_100,
    )

    assert decision["decision"] == "SKIP"
    assert decision["reason"] == "PUBLIC_FEATURES_INCOMPLETE"
    assert decision["dataIntegrityPass"] is False
    assert decision["gates"]["features"]["pass"] is False
    assert decision["gates"]["features"]["available"] == 15
    assert decision["gates"]["features"]["missing"] == ["chainlink_minus_strike_bps"]
    assert model.calls == 0

    malformed_trade = {
        **decision,
        "decision": "TRADE",
        "side": "UP",
        "ask": 0.5,
    }
    assert strategy.execution(malformed_trade) is None


def test_v2_diagnostics_expose_inputs_outputs_and_fail_closed_state() -> None:
    instance = service_v2.TargetTakerPublicSideTest.__new__(service_v2.TargetTakerPublicSideTest)
    instance.lock = threading.RLock()
    instance.last_public_snapshot = {
        "marketId": 123,
        "predictUpBid": 0.54,
        "predictUpAsk": 0.56,
        "spotQueueImbalance": None,
    }
    instance.last_decision = {
        "decision": "SKIP",
        "reason": "PUBLIC_FEATURES_INCOMPLETE",
        "signal": {
            "status": "FEATURES_INCOMPLETE",
            "featureInputComplete": False,
            "requiredFeatureCount": 16,
            "availableFeatureCount": 15,
            "missingFeatures": ["spot_queue_imbalance"],
            "features": {"spot_queue_imbalance": None},
            "probabilityUp": None,
            "probabilityDown": None,
            "selectedProbability": None,
            "score": None,
            "confidence": None,
            "threshold": 0.60,
            "modelProbabilityCalibrationClaim": False,
        },
        "gates": {
            "features": {
                "pass": False,
                "available": 15,
                "required": 16,
                "missing": ["spot_queue_imbalance"],
            }
        },
    }

    diagnostics = instance._calculation_diagnostics()

    assert diagnostics["ready"] is False
    assert diagnostics["failClosed"] is True
    assert diagnostics["inferenceAttempted"] is False
    assert diagnostics["tradeAllowedNow"] is False
    assert diagnostics["missingFeatures"] == ["spot_queue_imbalance"]
    assert diagnostics["frozenFeatures"]["spot_queue_imbalance"] is None
    assert diagnostics["modelOutputs"]["probabilityUp"] is None
    assert diagnostics["rawPublicInputs"]["spotQueueImbalance"] is None
