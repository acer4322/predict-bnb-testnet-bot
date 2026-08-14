from __future__ import annotations

import math
from typing import Any


VERSION = "TARGET_TAKER_PUBLIC_SIDE_V1_EXPLICIT_RULES"
SIDE_ONLY_COHORT = "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
HAZARD_SIDE_COHORT = "TARGET_TAKER_PUBLIC_SIDE_V1_HAZARD_SIDE"
COHORTS = (SIDE_ONLY_COHORT, HAZARD_SIDE_COHORT)

# This is a forward paper policy distilled from the BTC direct-Taker EBM study.
# It deliberately does not load the EBM artifact at runtime. Target-wallet events,
# target side, target execution price and target size are forbidden inputs.
STAKE_USDT = 1.0
FEE_RATE_BPS = 200
MAX_ASK = 0.95
MIN_SECONDS_LEFT = 10.0
MAX_SAMPLE_AGE_MS = 2_000
MAX_PREDICT_RECEIPT_AGE_MS = 2_500
MIN_AVAILABLE_FEATURES = 5
SIDE_SCORE_THRESHOLD = 0.38
HAZARD_SCORE_THRESHOLD = 0.55

# Relative importance follows the stable compact-side EBM ranking, but these are
# transparent research weights rather than fitted EBM coefficients/probabilities.
SIDE_FEATURES = (
    ("spot_minus_strike_bps", 1.00, 6.0, "TANH"),
    ("predict_up_mid", 0.90, 0.15, "CENTER_050"),
    ("futures_queue_imbalance", 0.80, 0.35, "LINEAR"),
    ("futures_return_1s_bps", 0.50, 1.50, "TANH"),
    ("spot_queue_imbalance", 0.50, 0.35, "LINEAR"),
    ("direction_score", 0.30, 0.50, "LINEAR"),
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


def _normalize(raw: float, scale: float, mode: str) -> float:
    if mode == "TANH":
        return math.tanh(raw / scale)
    if mode == "CENTER_050":
        return max(-1.0, min(1.0, (raw - 0.50) / scale))
    return max(-1.0, min(1.0, raw / scale))


def public_side_score(snapshot: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        "spot_minus_strike_bps": "spotMinusStrikeBps",
        "predict_up_mid": "predictUpMid",
        "futures_queue_imbalance": "futuresQueueImbalance",
        "futures_return_1s_bps": "futuresReturn1sBps",
        "spot_queue_imbalance": "spotQueueImbalance",
        "direction_score": "directionScore",
    }
    weighted = 0.0
    available_weight = 0.0
    contributions: dict[str, Any] = {}
    normalized: dict[str, float] = {}
    for feature, weight, scale, mode in SIDE_FEATURES:
        raw = _value(snapshot, feature, aliases[feature])
        if raw is None:
            contributions[feature] = {"raw": None, "normalized": None, "weight": weight}
            continue
        score = _normalize(raw, scale, mode)
        weighted += weight * score
        available_weight += weight
        normalized[feature] = score
        contributions[feature] = {"raw": raw, "normalized": score, "weight": weight}

    base_score = weighted / available_weight if available_weight else 0.0

    # The strongest EBM interactions involved Prediction mid with futures queue,
    # spot-vs-strike, and time. Keep only small, explicit agreement bonuses so
    # the paper strategy tests the same hypothesis without pretending to replay
    # the fitted EBM exactly.
    interaction = 0.0
    p = normalized.get("predict_up_mid")
    fq = normalized.get("futures_queue_imbalance")
    ss = normalized.get("spot_minus_strike_bps")
    seconds_left = _value(snapshot, "seconds_left", "secondsLeft")
    if p is not None and fq is not None and p * fq > 0:
        interaction += math.copysign(0.08 * min(abs(p), abs(fq)), p)
    if p is not None and ss is not None and p * ss > 0:
        interaction += math.copysign(0.06 * min(abs(p), abs(ss)), p)
    if p is not None and seconds_left is not None and seconds_left <= 60:
        interaction += 0.04 * p

    score = max(-1.0, min(1.0, base_score + interaction))
    side = "UP" if score > 0 else "DOWN" if score < 0 else None
    return {
        "score": score,
        "baseScore": base_score,
        "interactionAdjustment": interaction,
        "confidence": abs(score),
        "side": side,
        "availableFeatures": len(normalized),
        "availableWeight": available_weight,
        "contributions": contributions,
    }


def decide_side(
    snapshot: dict[str, Any],
    *,
    expected_market_id: int,
    now_ms: int,
) -> dict[str, Any]:
    sampled_at_ms = int(_value(snapshot, "sampled_at_ms", "sampledAtMs") or 0)
    market_id = int(_value(snapshot, "market_id", "marketId") or 0)
    sample_age_ms = max(0, now_ms - sampled_at_ms) if sampled_at_ms else None
    predict_age_ms = _value(snapshot, "predict_receipt_age_ms", "predictReceiptAgeMs")
    seconds_left = _value(snapshot, "seconds_left", "secondsLeft")
    signal = public_side_score(snapshot)
    side = signal["side"]
    ask = None
    if side == "UP":
        ask = _value(snapshot, "predict_up_ask", "predictUpAsk")
    elif side == "DOWN":
        ask = _value(snapshot, "predict_down_ask", "predictDownAsk")

    reason = "PUBLIC_SIDE_RULE_MATCH"
    if market_id != int(expected_market_id):
        reason = "MARKET_MISMATCH"
    elif sample_age_ms is None or sample_age_ms > MAX_SAMPLE_AGE_MS:
        reason = "STALE_PUBLIC_SNAPSHOT"
    elif predict_age_ms is None or predict_age_ms > MAX_PREDICT_RECEIPT_AGE_MS:
        reason = "STALE_PREDICT_BOOK"
    elif seconds_left is None or seconds_left <= MIN_SECONDS_LEFT:
        reason = "TOO_LATE"
    elif int(signal["availableFeatures"]) < MIN_AVAILABLE_FEATURES:
        reason = "INSUFFICIENT_PUBLIC_FEATURES"
    elif side not in {"UP", "DOWN"} or float(signal["confidence"]) < SIDE_SCORE_THRESHOLD:
        reason = "PUBLIC_SIDE_SCORE_TOO_WEAK"
    elif ask is None or not 0 < ask <= MAX_ASK:
        reason = "ASK_UNEXECUTABLE"

    return {
        "decision": "TRADE" if reason == "PUBLIC_SIDE_RULE_MATCH" else "SKIP",
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

    # The historical hazard model was aligned to the Maker-fill side. Keep that
    # exact causal semantics here: public Side may later choose either direction,
    # but the hazard price regime is always the side of our own paper Maker fill.
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
        "fixedStakeUsdt": STAKE_USDT,
        "feeRateBps": FEE_RATE_BPS,
        "maxAsk": MAX_ASK,
        "sideScoreThreshold": SIDE_SCORE_THRESHOLD,
        "minimumAvailablePublicFeatures": MIN_AVAILABLE_FEATURES,
        "sidePublicFeatures": [item[0] for item in SIDE_FEATURES],
        "sideRuleInterpretation": "explicit weighted public-state rule distilled from stable compact-side EBM feature families; not a replay of fitted EBM probabilities",
        "cohorts": {
            SIDE_ONLY_COHORT: {
                "entry": "first strong public-side state per complete forward market",
                "oneEntryPerMarket": True,
                "makerAnchorRequired": False,
            },
            HAZARD_SIDE_COHORT: {
                "entry": "first strong public-side state that also passes the 5s own-paper-Maker time+price hazard gate",
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
