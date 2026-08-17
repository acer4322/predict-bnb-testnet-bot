from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from . import predict_wallet_target_taker_public_side_strategy_v1 as public_side
from .target_taker_reentry_v1_dataset import ACTOR_STATE_FEATURES, DATASET_VERSION


ROOT = Path(__file__).resolve().parents[2]
VERSION = "TARGET_TAKER_SAME_SIDE_REENTRY_V1_EBM_FORWARD"
TASK = "SAME_SIDE_REENTRY_WITHIN_5S"
TARGET = "label_same_side_reentry_within_5000ms"
FEATURE_SET = "PUBLIC_ACTOR"
EXPECTED_REPORT_VERSION = "TARGET_TAKER_REENTRY_V1_5S_MARKET_WALK_FORWARD_EBM"
DEFAULT_MODEL_PATH = ROOT / "data" / "research" / "target_taker_reentry_v1_models" / "same_side_public_actor.joblib"
EXPECTED_FEATURES = tuple(public_side.SIDE_EBM_EXPECTED_FEATURES) + tuple(ACTOR_STATE_FEATURES)

REENTRY_PROBABILITY_THRESHOLD = float(os.environ.get("PREDICT_TARGET_TAKER_REENTRY_THRESHOLD", "0.50"))
MIN_REENTRY_DELAY_MS = max(250, int(os.environ.get("PREDICT_TARGET_TAKER_REENTRY_MIN_DELAY_MS", "250")))
MAX_REENTRY_DELAY_MS = 5_000

FORBIDDEN_RUNTIME_FEATURE_PREFIXES = ("target_", "audit_", "label_")


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def load_reentry_model(path: Path = DEFAULT_MODEL_PATH) -> dict[str, Any]:
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError('Re-entry EBM dependency missing. Run: pip install -e ".[research]"') from exc

    model_path = Path(path).expanduser().resolve()
    if not model_path.exists():
        raise FileNotFoundError(
            f"SAME_SIDE re-entry EBM model missing: {model_path}. "
            "Run tools/train_target_taker_reentry_runtime_v1.py first."
        )
    bundle = joblib.load(model_path)
    if not isinstance(bundle, dict):
        raise RuntimeError("Re-entry EBM artifact is not a model bundle")
    if bundle.get("reportVersion") != EXPECTED_REPORT_VERSION:
        raise RuntimeError(
            f"Re-entry EBM report version mismatch: {bundle.get('reportVersion')} != {EXPECTED_REPORT_VERSION}"
        )
    if bundle.get("datasetVersion") != DATASET_VERSION:
        raise RuntimeError(
            f"Re-entry EBM dataset version mismatch: {bundle.get('datasetVersion')} != {DATASET_VERSION}"
        )
    if bundle.get("task") != TASK or bundle.get("target") != TARGET:
        raise RuntimeError(
            f"Re-entry EBM task mismatch: task={bundle.get('task')} target={bundle.get('target')}"
        )
    if bundle.get("featureSet") != FEATURE_SET:
        raise RuntimeError(f"Re-entry EBM feature set mismatch: {bundle.get('featureSet')} != {FEATURE_SET}")
    if bundle.get("researchOnly") is not True or bundle.get("automaticStrategyPromotion") is not False:
        raise RuntimeError("Re-entry EBM artifact lacks research-only safety metadata")
    features = tuple(str(value) for value in bundle.get("features") or ())
    if features != EXPECTED_FEATURES:
        raise RuntimeError(f"Re-entry EBM feature contract mismatch: expected={EXPECTED_FEATURES} actual={features}")
    for feature in features:
        if feature.startswith(FORBIDDEN_RUNTIME_FEATURE_PREFIXES):
            raise RuntimeError(f"Forbidden runtime feature in re-entry EBM: {feature}")
    model = bundle.get("model")
    if model is None or not hasattr(model, "predict_proba"):
        raise RuntimeError("Re-entry EBM artifact has no classifier model")
    calibrator = bundle.get("calibrator")
    if calibrator is not None and not hasattr(calibrator, "predict_proba"):
        raise RuntimeError("Re-entry EBM artifact calibrator is invalid")
    return {**bundle, "path": str(model_path)}


def _actor_feature_row(
    snapshot: dict[str, Any],
    previous_entry: dict[str, Any],
    *,
    entry_count_so_far: int,
    sampled_at_ms: int,
) -> dict[str, float | None]:
    side = str(previous_entry.get("side") or "").upper()
    previous_price = _number(previous_entry.get("observed_ask"))
    previous_at_ms = int(_number(previous_entry.get("decision_at_ms")) or 0)
    elapsed = max(0.0, float(sampled_at_ms - previous_at_ms)) if previous_at_ms else None

    if side == "UP":
        bid = public_side._value(snapshot, "predict_up_bid", "predictUpBid")
        ask = public_side._value(snapshot, "predict_up_ask", "predictUpAsk")
        mid = public_side._value(snapshot, "predict_up_mid", "predictUpMid")
        opposite_mid = public_side._value(snapshot, "predict_down_mid", "predictDownMid")
    elif side == "DOWN":
        bid = public_side._value(snapshot, "predict_down_bid", "predictDownBid")
        ask = public_side._value(snapshot, "predict_down_ask", "predictDownAsk")
        mid = public_side._value(snapshot, "predict_down_mid", "predictDownMid")
        opposite_mid = public_side._value(snapshot, "predict_up_mid", "predictUpMid")
    else:
        bid = ask = mid = opposite_mid = None

    return {
        "actor_entry_count_so_far": float(entry_count_so_far),
        "actor_ms_since_prev_entry_min": elapsed,
        "actor_ms_since_prev_entry_max": elapsed,
        "actor_prev_entry_side_up": 1.0 if side == "UP" else 0.0 if side == "DOWN" else None,
        "actor_prev_entry_price": previous_price,
        "actor_current_side_bid": bid,
        "actor_current_side_ask": ask,
        "actor_current_side_mid": mid,
        "actor_current_side_spread": ask - bid if ask is not None and bid is not None else None,
        "actor_opposite_current_mid": opposite_mid,
        "actor_current_side_ask_minus_prev_entry_price": (
            ask - previous_price if ask is not None and previous_price is not None else None
        ),
    }


def feature_row(
    snapshot: dict[str, Any],
    previous_entry: dict[str, Any],
    *,
    entry_count_so_far: int,
    now_ms: int,
) -> dict[str, float | None]:
    sampled_at_ms = int(public_side._value(snapshot, "sampled_at_ms", "sampledAtMs") or 0)
    public = public_side._side_feature_row(snapshot, now_ms=sampled_at_ms or now_ms)
    actor = _actor_feature_row(
        snapshot,
        previous_entry,
        entry_count_so_far=entry_count_so_far,
        sampled_at_ms=sampled_at_ms or now_ms,
    )
    return {**public, **actor}


def _positive_probability(model: Any, frame: Any) -> float:
    probabilities = model.predict_proba(frame)[0]
    classes = [int(value) for value in model.classes_]
    if 1 not in classes:
        raise RuntimeError(f"Re-entry EBM has no positive class: {classes}")
    return max(0.0, min(1.0, float(probabilities[classes.index(1)])))


def _apply_calibrator(calibrator: Any | None, raw_probability: float) -> float:
    if calibrator is None:
        return raw_probability
    p = min(1.0 - 1e-7, max(1e-7, float(raw_probability)))
    logit = math.log(p / (1.0 - p))
    probabilities = calibrator.predict_proba([[logit]])[0]
    classes = [int(value) for value in calibrator.classes_]
    if 1 not in classes:
        raise RuntimeError(f"Re-entry calibrator has no positive class: {classes}")
    return max(0.0, min(1.0, float(probabilities[classes.index(1)])))


def reentry_score(
    snapshot: dict[str, Any],
    previous_entry: dict[str, Any],
    model_bundle: dict[str, Any] | None,
    *,
    entry_count_so_far: int,
    now_ms: int,
) -> dict[str, Any]:
    row = feature_row(
        snapshot,
        previous_entry,
        entry_count_so_far=entry_count_so_far,
        now_ms=now_ms,
    )
    missing = [name for name in EXPECTED_FEATURES if row.get(name) is None]
    base = {
        "status": "MODEL_UNAVAILABLE" if not isinstance(model_bundle, dict) else "FEATURES_INCOMPLETE" if missing else "READY",
        "probability": None,
        "rawProbability": None,
        "threshold": REENTRY_PROBABILITY_THRESHOLD,
        "features": row,
        "missingFeatures": missing,
        "requiredFeatureCount": len(EXPECTED_FEATURES),
        "availableFeatureCount": len(EXPECTED_FEATURES) - len(missing),
        "featureInputComplete": not missing,
        "modelProbabilityCalibrationClaim": bool(model_bundle and model_bundle.get("calibrator") is not None),
    }
    if not isinstance(model_bundle, dict) or model_bundle.get("model") is None:
        return base
    if missing:
        return {**base, "modelPath": model_bundle.get("path"), "reportVersion": model_bundle.get("reportVersion")}

    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError('Re-entry EBM dependency missing. Run: pip install -e ".[research]"') from exc
    features = [str(value) for value in model_bundle["features"]]
    frame = pd.DataFrame([{name: row.get(name) for name in features}], columns=features)
    raw_probability = _positive_probability(model_bundle["model"], frame)
    probability = _apply_calibrator(model_bundle.get("calibrator"), raw_probability)
    return {
        **base,
        "status": "OK",
        "rawProbability": raw_probability,
        "probability": probability,
        "modelPath": model_bundle.get("path"),
        "reportVersion": model_bundle.get("reportVersion"),
        "featureSet": model_bundle.get("featureSet"),
    }


def decide_same_side_reentry(
    snapshot: dict[str, Any],
    previous_entry: dict[str, Any],
    model_bundle: dict[str, Any] | None,
    *,
    entry_count_so_far: int,
    expected_market_id: int,
    now_ms: int,
) -> dict[str, Any]:
    sampled_at_ms = int(public_side._value(snapshot, "sampled_at_ms", "sampledAtMs") or 0)
    market_id = int(public_side._value(snapshot, "market_id", "marketId") or 0)
    sample_age_ms = max(0, now_ms - sampled_at_ms) if sampled_at_ms else None
    predict_age_ms = public_side._value(snapshot, "predict_receipt_age_ms", "predictReceiptAgeMs")
    seconds_left = public_side._value(snapshot, "seconds_left", "secondsLeft")
    side = str(previous_entry.get("side") or "").upper()
    previous_at_ms = int(_number(previous_entry.get("decision_at_ms")) or 0)
    elapsed_ms = max(0, (sampled_at_ms or now_ms) - previous_at_ms) if previous_at_ms else None
    ask = (
        public_side._value(snapshot, "predict_up_ask", "predictUpAsk")
        if side == "UP"
        else public_side._value(snapshot, "predict_down_ask", "predictDownAsk")
        if side == "DOWN"
        else None
    )
    signal = reentry_score(
        snapshot,
        previous_entry,
        model_bundle,
        entry_count_so_far=entry_count_so_far,
        now_ms=now_ms,
    )
    probability = _number(signal.get("probability"))

    model_pass = signal.get("status") != "MODEL_UNAVAILABLE"
    features_pass = signal.get("featureInputComplete") is True
    side_pass = side in {"UP", "DOWN"}
    market_pass = market_id == int(expected_market_id)
    sample_pass = sample_age_ms is not None and sample_age_ms <= public_side.MAX_SAMPLE_AGE_MS
    predict_pass = predict_age_ms is not None and predict_age_ms <= public_side.MAX_PREDICT_RECEIPT_AGE_MS
    time_pass = seconds_left is not None and seconds_left > public_side.MIN_SECONDS_LEFT
    horizon_pass = elapsed_ms is not None and MIN_REENTRY_DELAY_MS <= elapsed_ms <= MAX_REENTRY_DELAY_MS
    probability_pass = probability is not None and probability >= REENTRY_PROBABILITY_THRESHOLD
    ask_pass = ask is not None and 0 < ask <= public_side.MAX_ASK

    reason = "SAME_SIDE_REENTRY_EBM_MATCH"
    if signal.get("status") == "MODEL_UNAVAILABLE":
        reason = "REENTRY_EBM_MODEL_UNAVAILABLE"
    elif signal.get("status") == "FEATURES_INCOMPLETE":
        reason = "REENTRY_FEATURES_INCOMPLETE"
    elif signal.get("status") != "OK":
        reason = "REENTRY_EBM_NOT_READY"
    elif not side_pass:
        reason = "INVALID_PREVIOUS_SIDE"
    elif not market_pass:
        reason = "MARKET_MISMATCH"
    elif not sample_pass:
        reason = "STALE_PUBLIC_SNAPSHOT"
    elif not predict_pass:
        reason = "STALE_PREDICT_BOOK"
    elif not time_pass:
        reason = "TOO_LATE"
    elif not horizon_pass:
        reason = "OUTSIDE_5S_REENTRY_RISK_WINDOW"
    elif not probability_pass:
        reason = "REENTRY_EBM_TOO_WEAK"
    elif not ask_pass:
        reason = "ASK_UNEXECUTABLE"

    gates = {
        "model": {"pass": model_pass, "status": signal.get("status")},
        "features": {
            "pass": features_pass,
            "available": signal.get("availableFeatureCount"),
            "required": signal.get("requiredFeatureCount"),
            "missing": list(signal.get("missingFeatures") or []),
            "rule": "all PUBLIC_ACTOR EBM inputs must be finite",
        },
        "sameSide": {"pass": side_pass, "actual": side, "rule": "must reuse previous paper entry side"},
        "market": {"pass": market_pass, "actual": market_id or None, "expected": int(expected_market_id)},
        "sampleFreshness": {"pass": sample_pass, "actualMs": sample_age_ms, "maxMs": public_side.MAX_SAMPLE_AGE_MS},
        "predictFreshness": {"pass": predict_pass, "actualMs": predict_age_ms, "maxMs": public_side.MAX_PREDICT_RECEIPT_AGE_MS},
        "timeRemaining": {"pass": time_pass, "actualSeconds": seconds_left, "minExclusiveSeconds": public_side.MIN_SECONDS_LEFT},
        "reentryWindow": {
            "pass": horizon_pass,
            "actualMs": elapsed_ms,
            "minInclusive": MIN_REENTRY_DELAY_MS,
            "maxInclusive": MAX_REENTRY_DELAY_MS,
            "rule": f"{MIN_REENTRY_DELAY_MS}ms ≤ elapsed ≤ {MAX_REENTRY_DELAY_MS}ms",
        },
        "reentryProbability": {"pass": probability_pass, "actual": probability, "minInclusive": REENTRY_PROBABILITY_THRESHOLD},
        "ask": {"pass": ask_pass, "actual": ask, "minExclusive": 0.0, "maxInclusive": public_side.MAX_ASK},
    }
    return {
        "decision": "TRADE" if reason == "SAME_SIDE_REENTRY_EBM_MATCH" else "SKIP",
        "reason": reason,
        "side": side if side_pass else None,
        "ask": ask,
        "secondsLeft": seconds_left,
        "sampledAtMs": sampled_at_ms or None,
        "sampleAgeMs": sample_age_ms,
        "predictReceiptAgeMs": predict_age_ms,
        "msSincePrevEntry": elapsed_ms,
        "entryCountSoFar": int(entry_count_so_far),
        "dataIntegrityPass": features_pass,
        "gates": gates,
        "signal": signal,
    }


def execution(decision: dict[str, Any]) -> dict[str, float] | None:
    return public_side.execution(decision)


def policy() -> dict[str, Any]:
    return {
        "version": VERSION,
        "paperOnly": True,
        "forwardOnly": True,
        "automaticStrategyPromotion": False,
        "targetEventsDriveRuntime": False,
        "task": TASK,
        "featureSet": FEATURE_SET,
        "modelPath": str(DEFAULT_MODEL_PATH),
        "reportVersion": EXPECTED_REPORT_VERSION,
        "datasetVersion": DATASET_VERSION,
        "probabilityThreshold": REENTRY_PROBABILITY_THRESHOLD,
        "probabilityCalibrationClaim": True,
        "minReentryDelayMs": MIN_REENTRY_DELAY_MS,
        "maxReentryDelayMs": MAX_REENTRY_DELAY_MS,
        "fixedStakeUsdt": public_side.STAKE_USDT,
        "feeRateBps": public_side.FEE_RATE_BPS,
        "maxAsk": public_side.MAX_ASK,
        "minSecondsLeft": public_side.MIN_SECONDS_LEFT,
        "requiredFeatureCount": len(EXPECTED_FEATURES),
        "features": list(EXPECTED_FEATURES),
        "missingFeaturePolicy": "FAIL_CLOSED_NO_INFERENCE_NO_REENTRY",
        "entryCountCap": None,
        "sameSideOnly": True,
        "oppositeSideEnabled": False,
        "runtimeActorState": "own paper entries + current public market data only",
    }
