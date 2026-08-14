from __future__ import annotations

import math
from typing import Any

from . import predict_wallet_maker_grid_strategy as maker_grid
from . import predict_wallet_maker_inventory_taker_strategy as shared

VERSION = "LIFECYCLE_STATE_TAKER_V4"
COHORT = "TARGET_MAKER_LIFECYCLE_V3_STATE_TAKER_V4"
ELIGIBILITY_MS = 5_000
MIN_TAKER_SECONDS_LEFT = 5.0
EARLY_SECONDS_LEFT = 200.0
LATE_SECONDS_LEFT = 60.0
EARLY_SCORE_THRESHOLD = 0.50
MID_SCORE_THRESHOLD = 0.45
LATE_SCORE_THRESHOLD = 0.35
MIN_AVAILABLE_FEATURES = shared.TARGET_CORE_MIN_AVAILABLE_FEATURES
MIN_EDGE = shared.TARGET_CORE_MIN_EDGE
MATERIAL_IMBALANCE_RATIO = 0.10
MAX_TAKER_ASK = shared.MAX_TAKER_ASK
TAKER_FEE_RATE = shared.TAKER_FEE_RATE
ALPHA_PROBABILITY = shared.TARGET_CORE_ALPHA_PROBABILITY


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def score_threshold(seconds_left: float | None) -> tuple[float, str]:
    if seconds_left is None:
        return EARLY_SCORE_THRESHOLD, "UNKNOWN"
    if seconds_left <= LATE_SECONDS_LEFT:
        return LATE_SCORE_THRESHOLD, "LATE_LE60"
    if seconds_left <= EARLY_SECONDS_LEFT:
        return MID_SCORE_THRESHOLD, "MID_60_200"
    return EARLY_SCORE_THRESHOLD, "EARLY_GT200"


def price_bucket(midpoint: float | None) -> str:
    if midpoint is None:
        return "UNKNOWN"
    if midpoint < 0.33:
        return "LOW_LT033"
    if midpoint > 0.67:
        return "HIGH_GT067"
    return "MID_033_067"


def inventory_state(
    *,
    maker_up_shares: float,
    maker_down_shares: float,
    taker_up_shares: float = 0.0,
    taker_down_shares: float = 0.0,
) -> dict[str, Any]:
    maker_up = max(0.0, float(maker_up_shares))
    maker_down = max(0.0, float(maker_down_shares))
    taker_up = max(0.0, float(taker_up_shares))
    taker_down = max(0.0, float(taker_down_shares))
    combined_up = maker_up + taker_up
    combined_down = maker_down + taker_down
    combined_total = combined_up + combined_down
    delta = combined_up - combined_down
    imbalance = abs(delta) / combined_total if combined_total else 0.0
    heavy_side = "UP" if delta > 1e-9 else "DOWN" if delta < -1e-9 else None
    repair_side = "DOWN" if heavy_side == "UP" else "UP" if heavy_side == "DOWN" else None
    return {
        "makerUpShares": maker_up,
        "makerDownShares": maker_down,
        "takerUpShares": taker_up,
        "takerDownShares": taker_down,
        "combinedUpShares": combined_up,
        "combinedDownShares": combined_down,
        "combinedTotalShares": combined_total,
        "combinedDelta": delta,
        "combinedImbalanceRatio": imbalance,
        "heavySide": heavy_side,
        "repairSide": repair_side,
        "materialImbalance": bool(repair_side and imbalance >= MATERIAL_IMBALANCE_RATIO - 1e-12),
    }


def decide(
    snapshot: dict[str, Any],
    inventory: dict[str, Any],
    eligibility: dict[str, Any] | None,
    *,
    expected_market_id: int,
    now_ms: int,
) -> dict[str, Any]:
    sampled_at_ms = int(_number(snapshot.get("sampled_at_ms")) or 0)
    sample_market_id = int(_number(snapshot.get("market_id")) or 0)
    sample_age_ms = max(0, now_ms - sampled_at_ms) if sampled_at_ms else None
    predict_age_ms = _number(snapshot.get("predict_receipt_age_ms"))
    seconds_left = _number(snapshot.get("seconds_left"))
    threshold, time_regime = score_threshold(seconds_left)
    signal = shared.target_core_signal(snapshot)
    side = signal.get("side")
    confidence = float(signal.get("confidence") or 0.0)

    ask = _number(snapshot.get("predict_up_ask" if side == "UP" else "predict_down_ask")) if side else None
    bid = _number(snapshot.get("predict_up_bid" if side == "UP" else "predict_down_bid")) if side else None
    midpoint = (ask + bid) / 2.0 if ask is not None and bid is not None else None
    signed_alpha = ALPHA_PROBABILITY * float(signal.get("score") or 0.0)
    fair_probability = None
    if midpoint is not None and side in {"UP", "DOWN"}:
        fair_probability = max(
            0.01,
            min(0.99, midpoint + signed_alpha if side == "UP" else midpoint - signed_alpha),
        )
    estimated_edge = (
        fair_probability - ask * (1.0 + TAKER_FEE_RATE)
        if fair_probability is not None and ask is not None
        else None
    )

    eligibility_state = "NORMAL_MAKER"
    opened_at_ms = expires_at_ms = opened_snapshot_ns = None
    if eligibility:
        opened_at_ms = int(eligibility.get("openedAtMs") or 0) or None
        expires_at_ms = int(eligibility.get("expiresAtMs") or 0) or None
        opened_snapshot_ns = int(eligibility.get("openedSnapshotNs") or 0) or None
        if expires_at_ms is not None and now_ms <= expires_at_ms:
            eligibility_state = "TAKER_ELIGIBLE"
        else:
            eligibility_state = "ELIGIBILITY_EXPIRED"

    repair_side = inventory.get("repairSide")
    material_imbalance = bool(inventory.get("materialImbalance"))
    correcting = bool(material_imbalance and side in {"UP", "DOWN"} and side == repair_side)
    intent = "INVENTORY_REPAIR_TAKER" if correcting else "DIRECTIONAL_TAKER"

    reason = "PUBLIC_EDGE_CONFIRMED"
    if not eligibility:
        reason = "STATE_NOT_ELIGIBLE"
    elif expires_at_ms is None or now_ms > expires_at_ms:
        reason = "ELIGIBILITY_EXPIRED"
    elif sample_market_id != int(expected_market_id):
        reason = "MARKET_MISMATCH"
    elif sample_age_ms is None or sample_age_ms > maker_grid.MAX_SAMPLE_AGE_MS:
        reason = "STALE_PUBLIC_SNAPSHOT"
    elif predict_age_ms is None or predict_age_ms > maker_grid.MAX_PREDICT_AGE_MS:
        reason = "STALE_PREDICT_BOOK"
    elif seconds_left is None or seconds_left <= MIN_TAKER_SECONDS_LEFT:
        reason = "TOO_LATE"
    elif opened_snapshot_ns is not None and int(_number(snapshot.get("timestamp_ns")) or 0) <= opened_snapshot_ns:
        reason = "WAIT_NEXT_PUBLIC_SNAPSHOT_AFTER_FILL"
    elif int(signal.get("availableFeatures") or 0) < MIN_AVAILABLE_FEATURES:
        reason = "INSUFFICIENT_PUBLIC_FEATURES"
    elif side not in {"UP", "DOWN"} or confidence < threshold:
        reason = "PUBLIC_SCORE_BELOW_REGIME_THRESHOLD"
    elif ask is None or not 0 < ask <= MAX_TAKER_ASK:
        reason = "ASK_UNEXECUTABLE"
    elif estimated_edge is None or estimated_edge < MIN_EDGE:
        reason = "NO_FEE_ADJUSTED_EDGE"

    trade = reason == "PUBLIC_EDGE_CONFIRMED"
    if trade:
        reason = "INVENTORY_REPAIR_PUBLIC_EDGE" if correcting else "DIRECTIONAL_PUBLIC_EDGE"

    # Preserve the existing Target-core Taker execution economics. Repair trades
    # retain the pre-existing 1.25 correction scale; directional continuation is
    # no longer hard-blocked merely because it follows the current heavy side.
    inventory_scale = 1.25 if correcting else 1.0
    same_as_heavy = bool(side in {"UP", "DOWN"} and side == inventory.get("heavySide"))

    return {
        "decision": "TRADE" if trade else "SKIP",
        "reason": reason,
        "intent": intent if trade else None,
        "side": side,
        "state": eligibility_state,
        "signalAtMs": sampled_at_ms or None,
        "sampleAgeMs": sample_age_ms,
        "predictReceiptAgeMs": predict_age_ms,
        "secondsLeft": seconds_left,
        "timeRegime": time_regime,
        "decisionThreshold": threshold,
        "signal": signal,
        "ask": ask,
        "midpoint": midpoint,
        "priceBucket": price_bucket(midpoint),
        "fairProbability": fair_probability,
        "estimatedEdgePerShare": estimated_edge,
        "inventory": inventory,
        "repairSide": repair_side,
        "materialImbalance": material_imbalance,
        "sameAsHeavySide": same_as_heavy,
        "correctingResidual": correcting,
        "inventoryScale": inventory_scale,
        "combinedDeltaBefore": float(inventory.get("combinedDelta") or 0.0),
        "eligibilityOpenedAtMs": opened_at_ms,
        "eligibilityExpiresAtMs": expires_at_ms,
        "eligibilityOpenedSnapshotNs": opened_snapshot_ns,
    }


def execution(decision: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, float] | None:
    if decision.get("decision") != "TRADE":
        return None
    return shared.target_core_taker_execution(decision, snapshot)


def policy() -> dict[str, Any]:
    return {
        "version": VERSION,
        "cohort": COHORT,
        "paperOnly": True,
        "forwardOnly": True,
        "targetEventsDriveStrategy": False,
        "liveOrdersAffected": False,
        "eligibility": {
            "trigger": "own Lifecycle V3 full-18 paper Maker fill",
            "windowMs": ELIGIBILITY_MS,
            "oneTakerMaximumPerWindow": True,
            "sameSnapshotTradeAllowed": False,
            "partialFillDurationFaked": False,
        },
        "publicSignal": {
            "features": [item[0] for item in shared.TARGET_CORE_FEATURES],
            "minimumAvailableFeatures": MIN_AVAILABLE_FEATURES,
            "earlyScoreThresholdGt200s": EARLY_SCORE_THRESHOLD,
            "midScoreThreshold60To200s": MID_SCORE_THRESHOLD,
            "lateScoreThresholdLe60s": LATE_SCORE_THRESHOLD,
            "minimumFeeAdjustedEdgePerShare": MIN_EDGE,
            "maximumAsk": MAX_TAKER_ASK,
            "thresholdStatus": "forward-test heuristic; not an EBM probability cutoff",
        },
        "inventory": {
            "materialImbalanceRatio": MATERIAL_IMBALANCE_RATIO,
            "repairSide": "opposite current combined heavy side",
            "hardDirectionalContinuationBlock": False,
            "repairIntentRequiresPublicSignalToPointAtRepairSide": True,
        },
        "execution": {
            "implementation": "existing target_core_taker_execution",
            "feeRate": TAKER_FEE_RATE,
            "minimumPrincipalUsdt": shared.TARGET_CORE_MIN_PRINCIPAL_USDT,
            "maximumPrincipalUsdt": shared.TARGET_CORE_MAX_PRINCIPAL_USDT,
            "repairScale": 1.25,
            "directionalScale": 1.0,
            "sizingChangedVsExistingTargetCoreControl": False,
        },
        "researchBoundary": {
            "purpose": "test whether Maker lifecycle gating materially improves Maker/Taker coupling before EBM promotion",
            "predictionPriceBucket": "logged as context only; no direct price-bucket gate in V4",
            "slowFill": "not tested because current paper Maker fill proxy is whole-order ask-touch",
        },
    }
