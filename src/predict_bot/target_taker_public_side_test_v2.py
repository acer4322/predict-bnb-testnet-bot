from __future__ import annotations

import os
from http.server import ThreadingHTTPServer
from typing import Any

from . import target_taker_public_side_test_v1 as v1


VERSION = "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY_TEST_V2_DATA_INTEGRITY"
# 8780 is permanently reserved for the ETH Taker public-signal collector.
# Keep the EBM forward test isolated on its own port unless explicitly overridden.
PORT = int(os.environ.get("PREDICT_TARGET_TAKER_PUBLIC_SIDE_TEST_PORT", "8782"))
v1.PORT = PORT


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class TargetTakerPublicSideTest(v1.TargetTakerPublicSideTest):
    """V1 forward test with an explicit, inspectable data-integrity contract."""

    def _calculation_diagnostics(self) -> dict[str, Any]:
        with self.lock:
            decision = dict(self.last_decision) if isinstance(self.last_decision, dict) else {}
            snapshot = dict(self.last_public_snapshot) if isinstance(self.last_public_snapshot, dict) else {}
        signal = _record(decision.get("signal"))
        gates = _record(decision.get("gates"))
        missing = [str(value) for value in signal.get("missingFeatures") or []]
        required = int(signal.get("requiredFeatureCount") or len(v1.public_side.SIDE_EBM_EXPECTED_FEATURES))
        available = int(signal.get("availableFeatureCount") or max(0, required - len(missing)))
        feature_complete = signal.get("featureInputComplete") is True
        signal_status = str(signal.get("status") or "NO_SIGNAL")
        inference_attempted = signal_status == "OK"

        raw_keys = (
            "timestampNs",
            "sampledAtMs",
            "marketId",
            "secondsLeft",
            "strikePrice",
            "predictUpBid",
            "predictUpAsk",
            "predictUpMid",
            "predictDownBid",
            "predictDownAsk",
            "predictDownMid",
            "predictSourceAgeMs",
            "predictReceiptAgeMs",
            "spotPrice",
            "spotMicroprice",
            "spotQueueImbalance",
            "spotTakerImbalance250ms",
            "spotTakerImbalance1s",
            "spotReturn250msBps",
            "spotReturn1sBps",
            "spotReturn3sBps",
            "spotReturn5sBps",
            "futuresPrice",
            "futuresMicroprice",
            "futuresQueueImbalance",
            "futuresTakerImbalance250ms",
            "futuresTakerImbalance1s",
            "futuresReturn250msBps",
            "futuresReturn1sBps",
            "futuresReturn3sBps",
            "futuresReturn5sBps",
            "perpSpotBasisBps",
            "chainlinkPrice",
            "chainlinkSourceAgeMs",
            "chainlinkReceiptAgeMs",
            "spotMinusStrikeBps",
            "chainlinkMinusStrikeBps",
            "spotMinusChainlinkBps",
            "directionScore",
            "directionBias",
            "volatilityAlert",
        )
        raw_inputs = {key: snapshot.get(key) for key in raw_keys}
        return {
            "contract": "ALL_16_FROZEN_FEATURES_REQUIRED_FAIL_CLOSED",
            "ready": feature_complete and signal_status == "OK",
            "failClosed": not feature_complete or signal_status != "OK",
            "inferenceAttempted": inference_attempted,
            "tradeAllowedNow": decision.get("decision") == "TRADE" and feature_complete and signal_status == "OK",
            "signalStatus": signal_status,
            "requiredFeatureCount": required,
            "availableFeatureCount": available,
            "missingFeatures": missing,
            "frozenFeatures": dict(_record(signal.get("features"))),
            "modelOutputs": {
                "selectedSide": signal.get("side"),
                "probabilityUp": signal.get("probabilityUp"),
                "probabilityDown": signal.get("probabilityDown"),
                "selectedProbability": signal.get("selectedProbability"),
                "signedScore": signal.get("score"),
                "confidence": signal.get("confidence"),
                "probabilityThreshold": signal.get("threshold"),
                "probabilityCalibrationClaim": signal.get("modelProbabilityCalibrationClaim"),
            },
            "gates": gates,
            "final": {
                "decision": decision.get("decision"),
                "reason": decision.get("reason"),
                "side": decision.get("side"),
                "ask": decision.get("ask"),
                "secondsLeft": decision.get("secondsLeft"),
                "sampleAgeMs": decision.get("sampleAgeMs"),
                "predictReceiptAgeMs": decision.get("predictReceiptAgeMs"),
            },
            "rawPublicInputs": raw_inputs,
            "note": (
                "Model diagnostic values expose inputs, derived features, probabilities and gates only. "
                "If any frozen feature is absent/non-finite, predict_proba is not called and TRADE is impossible."
            ),
        }

    def health_snapshot(self) -> dict[str, Any]:
        payload = super().health_snapshot()
        diagnostics = self._calculation_diagnostics()
        payload["version"] = VERSION
        payload["port"] = PORT
        payload["strategyInputReady"] = diagnostics["ready"]
        payload["strategyFailClosed"] = diagnostics["failClosed"]
        payload["inferenceAttempted"] = diagnostics["inferenceAttempted"]
        payload["availableFeatureCount"] = diagnostics["availableFeatureCount"]
        payload["requiredFeatureCount"] = diagnostics["requiredFeatureCount"]
        payload["missingFeatures"] = diagnostics["missingFeatures"]
        # Keep process/lifecycle health separate from strategy input readiness.
        # The service stays alive so a transient feed outage can recover, while
        # the shared strategy contract guarantees no inference/trade meanwhile.
        payload["processHealthy"] = bool(payload.get("ok"))
        payload["strategyStatus"] = "READY" if diagnostics["ready"] else "FAIL_CLOSED_INPUT_INCOMPLETE"
        return payload

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        diagnostics = self._calculation_diagnostics()
        payload["version"] = VERSION
        payload["port"] = PORT
        payload["calculationDiagnostics"] = diagnostics
        payload["dataIntegrity"] = {
            "ready": diagnostics["ready"],
            "failClosed": diagnostics["failClosed"],
            "requiredFeatureCount": diagnostics["requiredFeatureCount"],
            "availableFeatureCount": diagnostics["availableFeatureCount"],
            "missingFeatures": diagnostics["missingFeatures"],
            "missingFeaturePolicy": "NO_MODEL_INFERENCE_NO_TRADE",
        }
        return payload


class Handler(v1.Handler):
    collector: TargetTakerPublicSideTest


def main() -> int:
    collector = TargetTakerPublicSideTest()
    collector.start()
    handler = type("TargetTakerPublicSideTestV2Handler", (Handler,), {"collector": collector})
    server = ThreadingHTTPServer((v1.HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{v1.HOST}:{PORT}/state; "
        f"strategy={v1.STRATEGY}; all16FeaturesRequired=true; failClosed=true; "
        "paperOnly=true; targetEventsUsed=false; liveOrdersAffected=false; reserved8780=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
