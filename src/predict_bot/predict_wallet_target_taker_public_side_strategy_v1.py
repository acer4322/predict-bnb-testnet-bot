from __future__ import annotations

import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
VERSION = "TARGET_TAKER_PUBLIC_SIDE_V1_EBM_FORWARD"
SIDE_ONLY_COHORT = "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
HAZARD_SIDE_COHORT = "TARGET_TAKER_PUBLIC_SIDE_V1_HAZARD_SIDE"
COHORTS = (SIDE_ONLY_COHORT, HAZARD_SIDE_COHORT)
DEFAULT_SIDE_MODEL_PATH = ROOT / "data" / "research" / "target_taker_behavior_models_v1" / "side_up.joblib"
EXPECTED_REPORT_VERSION = "TARGET_TAKER_BEHAVIOR_V1_HAZARD_SIDE_SIZE_WALK_FORWARD"

# Forward paper execution policy. Size EBM was weak, so stake remains fixed.
STAKE_USDT = 1.0
FEE_RATE_BPS = 200
MAX_ASK = 0.95
MIN_SECONDS_LEFT = 10.0
MAX_SAMPLE_AGE_MS = 2_000
MAX_PREDICT_RECEIPT_AGE_MS = 2_500
SIDE_PROBABILITY_THRESHOLD = 0.60
HAZARD_SCORE_THRESHOLD = 0.55

# Exact compact_side feature family used by the winning BTC Side EBM. These are
# raw public, pre-event variables only. No Target-side alignment is legal here.
SIDE_EBM_EXPECTED_FEATURES = (
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

FORBIDDEN_RUNTIME_FEATURE_PREFIXES = (
    "target_",
    "chosen_",
    "side_aligned_",
    "label_",
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _value(snapshot: dict[str, Any], snake: str, camel: str | None = None) -> float | None:
    value = _number(snapshot.get(snake))
    if value is None and camel:
        value = _number(snapshot.get(camel))
    return value


def load_side_model(path: Path = DEFAULT_SIDE_MODEL_PATH) -> dict[str, Any]:
    """Load and validate the frozen full-data compact_side EBM research artifact."""
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError('Side EBM dependency missing. Run: pip install -e ".[research]"') from exc
    model_path = Path(path).expanduser().resolve()
    if not model_path.exists():
        raise FileNotFoundError(
            f"Side EBM model missing: {model_path}. Run tools/train_target_taker_behavior_v1.py first."
        )
    bundle = joblib.load(model_path)
    if not isinstance(bundle, dict):
        raise RuntimeError("Side EBM artifact is not a model bundle")
    if bundle.get("reportVersion") != EXPECTED_REPORT_VERSION:
        raise RuntimeError(
            f"Side EBM report version mismatch: {bundle.get('reportVersion')} != {EXPECTED_REPORT_VERSION}"
        )
    if bundle.get("task") != "side_up":
        raise RuntimeError(f"Side EBM task mismatch: {bundle.get('task')}")
    if bundle.get("researchOnly") is not True or bundle.get("automaticStrategyPromotion") is not False:
        raise RuntimeError("Side EBM artifact lacks the required research-only safety metadata")
    features = tuple(str(value) for value in bundle.get("features") or ())
    if features != SIDE_EBM_EXPECTED_FEATURES:
        raise RuntimeError(
            "Side EBM feature contract mismatch. "
            f"expected={SIDE_EBM_EXPECTED_FEATURES} actual={features}"
        )
    for feature in features:
        if feature.startswith(FORBIDDEN_RUNTIME_FEATURE_PREFIXES):
            raise RuntimeError(f"Forbidden runtime feature in Side EBM: {feature}")
    model = bundle.get("model")
    if model is None or not hasattr(model, "predict_proba"):
        raise RuntimeError("Side EBM artifact has no classifier model")
    return {**bundle, "path": str(model_path)}


def _side_feature_row(snapshot: dict[str, Any], *, now_ms: int) -> dict[str, float | None]:
    up_bid = _value(snapshot, "predict_up_bid", "predictUpBid")
    up_ask = _value(snapshot, "predict_up_ask", "predictUpAsk")
    down_bid = _value(snapshot, "predict_down_bid", "predictDownBid")
    down_ask = _value(snapshot, "predict_down_ask", "predictDownAsk")
    sampled_at_ms = int(_value(snapshot, "sampled_at_ms", "sampledAtMs") or 0)
    return {
        "seconds_left": _value(snapshot, "seconds_left", "secondsLeft"),
        "predict_up_mid": _value(snapshot, "predict_up_mid", "predictUpMid"),
        "predict_up_spread": up_ask - up_bid if up_ask is not None and up_bid is not None else None,
        "predict_down_spread": down_ask - down_bid if down_ask is not None and down_bid is not None else None,
        "spot_minus_strike_bps": _value(snapshot, "spot_minus_strike_bps", "spotMinusStrikeBps"),
        "chainlink_minus_strike_bps": _value(snapshot, "chainlink_minus_strike_bps", "chainlinkMinusStrikeBps"),
        "direction_score": _value(snapshot, "direction_score", "directionScore"),
        "spot_queue_imbalance": _value(snapshot, "spot_queue_imbalance", "spotQueueImbalance"),
        "spot_taker_imbalance_1s": _value(snapshot, "spot_taker_imbalance_1s", "spotTakerImbalance1s"),
        "spot_return_1s_bps": _value(snapshot, "spot_return_1s_bps", "spotReturn1sBps"),
        "spot_return_3s_bps": _value(snapshot, "spot_return_3s_bps", "spotReturn3sBps"),
        "futures_queue_imbalance": _value(snapshot, "futures_queue_imbalance", "futuresQueueImbalance"),
        "futures_taker_imbalance_1s": _value(snapshot, "futures_taker_imbalance_1s", "futuresTakerImbalance1s"),
        "futures_return_1s_bps": _value(snapshot, "futures_return_1s_bps", "futuresReturn1sBps"),
        "futures_return_3s_bps": _value(snapshot, "futures_return_3s_bps", "futuresReturn3sBps"),
        # Historical signal_age_ms measured strict-pre-event snapshot staleness.
        # Forward inference uses the age of the public snapshot at decision time.
        "signal_age_ms": max(0.0, float(now_ms - sampled_at_ms)) if sampled_at_ms else None,
    }


def public_side_score(
    snapshot: dict[str, Any],
    model_bundle: dict[str, Any] | None,
    *,
    now_ms: int,
) -> dict[str, Any]:
    row = _side_feature_row(snapshot, now_ms=now_ms)
    missing = [name for name, value in row.items() if value is None]
    if not isinstance(model_bundle, dict) or model_bundle.get("model") is None:
        return {
            "status": "MODEL_UNAVAILABLE",
            "side": None,
            "probabilityUp": None,
            "selectedProbability": None,
            "score": None,
            "confidence": None,
            "features": row,
            "missingFeatures": missing,
        }
    try:
        import pandas as pd
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError('Side EBM dependency missing. Run: pip install -e ".[research]"') from exc
    features = [str(value) for value in model_bundle["features"]]
    model = model_bundle["model"]
    frame = pd.DataFrame([{name: row.get(name) for name in features}], columns=features)
    probabilities = model.predict_proba(frame)[0]
    classes = [int(value) for value in model.classes_]
    if 1 not in classes:
        raise RuntimeError(f"Side EBM has no positive class: {classes}")
    p_up = float(probabilities[classes.index(1)])
    p_up = max(0.0, min(1.0, p_up))
    side = "UP" if p_up >= 0.5 else "DOWN"
    selected_probability = p_up if side == "UP" else 1.0 - p_up
    return {
        "status": "OK",
        "side": side,
        "probabilityUp": p_up,
        "selectedProbability": selected_probability,
        # Signed score is convenient for forward diagnostics; it is not claimed
        # to be a calibrated edge because the EBM was trained with class weights.
        "score": 2.0 * p_up - 1.0,
        "confidence": selected_probability,
        "threshold": SIDE_PROBABILITY_THRESHOLD,
        "features": row,
        "missingFeatures": missing,
        "modelPath": model_bundle.get("path"),
        "reportVersion": model_bundle.get("reportVersion"),
        "modelProbabilityCalibrationClaim": False,
    }


def decide_side(
    snapshot: dict[str, Any],
    model_bundle: dict[str, Any] | None,
    *,
    expected_market_id: int,
    now_ms: int,
) -> dict[str, Any]:
    sampled_at_ms = int(_value(snapshot, "sampled_at_ms", "sampledAtMs") or 0)
    market_id = int(_value(snapshot, "market_id", "marketId") or 0)
    sample_age_ms = max(0, now_ms - sampled_at_ms) if sampled_at_ms else None
    predict_age_ms = _value(snapshot, "predict_receipt_age_ms", "predictReceiptAgeMs")
    seconds_left = _value(snapshot, "seconds_left", "secondsLeft")
    signal = public_side_score(snapshot, model_bundle, now_ms=now_ms)
    side = signal["side"]
    ask = None
    if side == "UP":
        ask = _value(snapshot, "predict_up_ask", "predictUpAsk")
    elif side == "DOWN":
        ask = _value(snapshot, "predict_down_ask", "predictDownAsk")

    reason = "PUBLIC_SIDE_EBM_MATCH"
    if signal["status"] != "OK":
        reason = "SIDE_EBM_MODEL_UNAVAILABLE"
    elif market_id != int(expected_market_id):
        reason = "MARKET_MISMATCH"
    elif sample_age_ms is None or sample_age_ms > MAX_SAMPLE_AGE_MS:
        reason = "STALE_PUBLIC_SNAPSHOT"
    elif predict_age_ms is None or predict_age_ms > MAX_PREDICT_RECEIPT_AGE_MS:
        reason = "STALE_PREDICT_BOOK"
    elif seconds_left is None or seconds_left <= MIN_SECONDS_LEFT:
        reason = "TOO_LATE"
    elif side not in {"UP", "DOWN"} or float(signal["selectedProbability"] or 0.0) < SIDE_PROBABILITY_THRESHOLD:
        reason = "PUBLIC_SIDE_EBM_TOO_WEAK"
    elif ask is None or not 0 < ask <= MAX_ASK:
        reason = "ASK_UNEXECUTABLE"

    return {
        "decision": "TRADE" if reason == "PUBLIC_SIDE_EBM_MATCH" else "SKIP",
        "reason": reason,
        "side": side,
        "ask": ask,
        "secondsLeft": seconds_left,
        "sampledAtMs": sampled_at_ms or None,
        "sampleAgeMs": sample_age_ms,
        "predictReceiptAgeMs": predict_age_ms,
        "signal": signal,
    }


def hazard_gate(
    snapshot: dict[str, Any],
    maker_eligibility: dict[str, Any] | None,
    *,
    snapshot_ns: int,
    now_ms: int,
) -> dict[str, Any]:
    if not isinstance(maker_eligibility, dict):
        return {"eligible": False, "reason": "NO_OWN_MAKER_FILL_ELIGIBILITY", "eligibilityScore": None}
    opened_ns = int(_number(maker_eligibility.get("openedSnapshotNs")) or 0)
    expires_ms = int(_number(maker_eligibility.get("expiresAtMs")) or 0)
    if snapshot_ns <= opened_ns:
        return {"eligible": False, "reason": "SAME_MAKER_FILL_SNAPSHOT_FORBIDDEN", "eligibilityScore": None}
    if expires_ms <= 0 or now_ms > expires_ms:
        return {"eligible": False, "reason": "OWN_MAKER_FILL_ELIGIBILITY_EXPIRED", "eligibilityScore": None}

    maker_side = str(maker_eligibility.get("makerAnchorSide") or "").upper()
    if maker_side not in {"UP", "DOWN"}:
        return {"eligible": False, "reason": "AMBIGUOUS_OWN_MAKER_FILL_SIDE", "eligibilityScore": None}

    seconds_left = _value(snapshot, "seconds_left", "secondsLeft")
    up_mid = _value(snapshot, "predict_up_mid", "predictUpMid")
    down_mid = _value(snapshot, "predict_down_mid", "predictDownMid")
    if down_mid is None and up_mid is not None:
        down_mid = 1.0 - up_mid
    maker_side_mid = up_mid if maker_side == "UP" else down_mid
    if seconds_left is None or maker_side_mid is None:
        return {"eligible": False, "reason": "HAZARD_STATE_MISSING", "eligibilityScore": None}

    # The best 5s hazard family was time+price. Keep it as a transparent ordinal
    # gate instead of deploying the inferior fixed time_price_micro research model.
    if seconds_left <= 60:
        time_score = 0.70
        time_regime = "LATE_LE_60S"
    elif seconds_left <= 200:
        time_score = 0.45
        time_regime = "MID_60_200S"
    else:
        time_score = 0.30
        time_regime = "EARLY_GT_200S"

    if 0.33 <= maker_side_mid <= 0.67:
        price_score = 0.65
        price_regime = "MID_033_067"
    elif maker_side_mid < 0.33:
        price_score = 0.45
        price_regime = "LOW_LT_033"
    else:
        price_score = 0.35
        price_regime = "HIGH_GT_067"

    eligibility_score = 0.50 * time_score + 0.50 * price_score
    eligible = eligibility_score + 1e-12 >= HAZARD_SCORE_THRESHOLD
    return {
        "eligible": eligible,
        "reason": "HAZARD_TIME_PRICE_MATCH" if eligible else "HAZARD_TIME_PRICE_TOO_WEAK",
        "eligibilityScore": eligibility_score,
        "threshold": HAZARD_SCORE_THRESHOLD,
        "timeRegime": time_regime,
        "priceRegime": price_regime,
        "makerAnchorSide": maker_side,
        "makerSidePredictMid": maker_side_mid,
        "eligibilityOpenedAtMs": maker_eligibility.get("openedAtMs"),
        "eligibilityExpiresAtMs": expires_ms,
        "openedSnapshotNs": opened_ns,
    }


def effective_unit_cost(ask: float, fee_rate_bps: int = FEE_RATE_BPS) -> float:
    price = float(ask)
    return price + min(price, 1.0 - price) * fee_rate_bps / 10_000.0


def execution(decision: dict[str, Any]) -> dict[str, float] | None:
    if decision.get("decision") != "TRADE" or decision.get("side") not in {"UP", "DOWN"}:
        return None
    ask = _number(decision.get("ask"))
    if ask is None or not 0 < ask <= MAX_ASK:
        return None
    unit_cost = effective_unit_cost(ask)
    shares = STAKE_USDT / unit_cost
    return {
        "ask": ask,
        "effectiveUnitCost": unit_cost,
        "stakeUsdt": STAKE_USDT,
        "shares": shares,
    }


def policy() -> dict[str, Any]:
    return {
        "version": VERSION,
        "paperOnly": True,
        "forwardOnly": True,
        "automaticStrategyPromotion": False,
        "targetEventsDriveRuntime": False,
        "sideModelPath": str(DEFAULT_SIDE_MODEL_PATH),
        "sideModelReportVersion": EXPECTED_REPORT_VERSION,
        "sideProbabilityThreshold": SIDE_PROBABILITY_THRESHOLD,
        "probabilityCalibrationClaim": False,
        "fixedStakeUsdt": STAKE_USDT,
        "feeRateBps": FEE_RATE_BPS,
        "maxAsk": MAX_ASK,
        "sidePublicFeatures": list(SIDE_EBM_EXPECTED_FEATURES),
        "sideRuleInterpretation": "direct frozen compact_side EBM inference from the completed BTC Target Taker study; class-weighted probability is used only as a ranking/confidence score, not claimed calibrated",
        "cohorts": {
            SIDE_ONLY_COHORT: {
                "entry": "first public state per complete forward market where frozen compact_side EBM selected-side score >= threshold",
                "oneEntryPerMarket": True,
                "makerAnchorRequired": False,
            },
            HAZARD_SIDE_COHORT: {
                "entry": "same frozen compact_side EBM side rule plus the 5s own-paper-Maker time+price hazard gate",
                "oneEntryPerMarket": True,
                "makerAnchorRequired": True,
                "makerAnchorSource": "Lifecycle V3 own strict paper Maker fill only",
                "hazardPriceAlignment": "own Maker fill side, matching historical time_price hazard features; independent of chosen Taker side",
                "sameFillSnapshotForbidden": True,
                "eligibilityWindowMs": 5_000,
                "hazardScoreThreshold": HAZARD_SCORE_THRESHOLD,
            },
        },
        "forbiddenRuntimeFeaturePrefixes": list(FORBIDDEN_RUNTIME_FEATURE_PREFIXES),
        "sizeModelUsed": False,
    }
