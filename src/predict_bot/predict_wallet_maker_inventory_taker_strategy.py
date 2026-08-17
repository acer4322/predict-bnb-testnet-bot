from __future__ import annotations

import math
from typing import Any

from . import predict_wallet_maker_grid_strategy as maker_grid


COHORTS = (
    {
        "cohort": "MAKER_INV_TAKER_SHARED_V1",
        "label": "Shared fixed 7",
        "dynamicDepth": False,
        "fixedLevels": 7,
    },
    {
        "cohort": "MAKER_DYNAMIC_DEPTH_SHARED_V1",
        "label": "Shared dynamic 3/7/15",
        "dynamicDepth": True,
        "fixedLevels": None,
        "reservationSkew": False,
    },
    {
        "cohort": "MAKER_RESERVATION_SKEW_SHARED_V1",
        "label": "Reservation skew research",
        "dynamicDepth": True,
        "fixedLevels": None,
        "reservationSkew": True,
    },
    {
        "cohort": "RECENTERED_POOLED_INVENTORY_V1",
        "label": "Recentered pooled inventory",
        "dynamicDepth": False,
        "fixedLevels": 3,
        "reservationSkew": True,
        "pooledInventory": True,
    },
    {
        "cohort": "BALANCE_FIRST_POOLED_V2",
        "label": "Balance-first pooled inventory",
        "dynamicDepth": False,
        "fixedLevels": 1,
        "reservationSkew": True,
        "pooledInventory": True,
        "balanceFirst": True,
    },
    {
        "cohort": "TARGET_CORE_INTEGRATED_V1",
        "label": "Target-core Maker + Taker integrated",
        "dynamicDepth": False,
        "fixedLevels": 15,
        "reservationSkew": True,
        "targetCoreMaker": True,
        "targetCoreIntegrated": True,
    },
)

MIN_BASELINE_SHARES_PER_SIDE = 2 * maker_grid.SHARES
MIN_MAKER_DELTA_SHARES = maker_grid.SHARES
MIN_COMBINED_DELTA_SHARES = maker_grid.SHARES / 2
MIN_MAKER_IMBALANCE_RATIO = 0.03
TAKER_FEE_RATE = 0.02
MAX_TAKER_ASK = 0.95
MIN_TAKER_SECONDS_LEFT = 5.0
TAKER_COOLDOWN_MS = 15_000
TAKER_MAX_SHARES = maker_grid.SHARES
TAKER_CORRECTION_FRACTION = 0.50
MIN_DIRECTION_SCORE = 0.05
VOLATILE_RETURN_BPS = 3.0
CALM_RETURN_BPS = 0.75
TIGHT_SPREAD = 0.02
RESERVATION_SKEW_STEP_RATIO = 0.10
MAX_RESERVATION_SKEW_TICKS = 2
REDUCE_ONLY_IMBALANCE_RATIO = 0.12
LATE_DEPTH_SECONDS = 60.0
MID_DEPTH_SECONDS = 150.0
POOLED_LEVELS_PER_SIDE = 3
POOLED_REDUCE_ONLY_IMBALANCE_RATIO = 0.08
POOLED_IMMEDIATE_DELTA_SHARES = maker_grid.SHARES
POOLED_MAX_SKEW_TICKS = 2
BALANCE_FIRST_BALANCED_LEVELS = 1
BALANCE_FIRST_MAX_REPAIR_LEVELS = 3
BALANCE_FIRST_STOP_NEW_RISK_SECONDS = 60.0
BALANCE_FIRST_TARGET_PAIRED_COVERAGE = 0.90
BALANCE_FIRST_TARGET_IMBALANCE = 0.10
TARGET_CORE_LEVELS_PER_SIDE = 15
TARGET_CORE_TILTED_HEAVY_LEVELS = 7
TARGET_CORE_MAX_REPAIR_LEVELS = 7
TARGET_CORE_SOFT_DELTA_SHARES = 3 * maker_grid.SHARES
TARGET_CORE_HARD_DELTA_SHARES = 6 * maker_grid.SHARES
TARGET_CORE_TAIL_SHALLOW_SECONDS = 60.0
TARGET_CORE_TAIL_LEVELS = 7
TARGET_CORE_SCORE_THRESHOLD = 0.40
TARGET_CORE_MIN_EDGE = 0.003
TARGET_CORE_ALPHA_PROBABILITY = 0.06
TARGET_CORE_REPEAT_COOLDOWN_MS = 8_000
TARGET_CORE_FLIP_COOLDOWN_MS = 3_000
TARGET_CORE_MIN_SECONDS_LEFT = 10.0
TARGET_CORE_MIN_AVAILABLE_FEATURES = 4
TARGET_CORE_MIN_PRINCIPAL_USDT = 1.0
TARGET_CORE_MAX_PRINCIPAL_USDT = 15.0
TARGET_CORE_FEATURES = (
    ("direction_score", 0.15, 1.0, "LINEAR"),
    ("futures_return_1s_bps", 0.25, 0.50, "TANH"),
    ("futures_taker_imbalance_1s", 0.25, 1.0, "LINEAR"),
    ("futures_queue_imbalance", 0.15, 1.0, "LINEAR"),
    ("spot_return_1s_bps", 0.10, 0.50, "TANH"),
    ("spot_taker_imbalance_1s", 0.10, 1.0, "LINEAR"),
)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _side(value: float | None, *, threshold: float = 0.0) -> str | None:
    if value is None or abs(value) <= threshold:
        return None
    return "UP" if value > 0 else "DOWN"


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
    maker_total = maker_up + maker_down
    maker_delta = maker_up - maker_down
    taker_delta = taker_up - taker_down
    combined_delta = maker_delta + taker_delta
    maker_ratio = abs(maker_delta) / maker_total if maker_total else 0.0
    paired_coverage = 2 * min(maker_up, maker_down) / maker_total if maker_total else None
    baseline_ready = min(maker_up, maker_down) >= MIN_BASELINE_SHARES_PER_SIDE
    maker_residual_side = _side(maker_delta)
    combined_residual_side = _side(combined_delta)
    correction_side = None
    if (
        baseline_ready
        and abs(maker_delta) >= MIN_MAKER_DELTA_SHARES
        and maker_ratio >= MIN_MAKER_IMBALANCE_RATIO
        and abs(combined_delta) >= MIN_COMBINED_DELTA_SHARES
        and maker_residual_side == combined_residual_side
    ):
        correction_side = "DOWN" if maker_residual_side == "UP" else "UP"
    return {
        "makerUpShares": maker_up,
        "makerDownShares": maker_down,
        "takerUpShares": taker_up,
        "takerDownShares": taker_down,
        "makerDelta": maker_delta,
        "takerDelta": taker_delta,
        "combinedDelta": combined_delta,
        "makerImbalanceRatio": maker_ratio,
        "makerPairedCoverage": paired_coverage,
        "baselineReady": baseline_ready,
        "makerResidualSide": maker_residual_side,
        "combinedResidualSide": combined_residual_side,
        "correctionSide": correction_side,
    }


def _volatility_alert(snapshot: dict[str, Any]) -> bool:
    value = snapshot.get("volatility_alert")
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "normal", "off"}
    return bool(value)


def dynamic_depths(snapshot: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    returns = [
        abs(value)
        for value in (
            _number(snapshot.get("spot_return_1s_bps")),
            _number(snapshot.get("futures_return_1s_bps")),
        )
        if value is not None
    ]
    maximum_return = max(returns) if returns else None
    spreads = []
    for side in ("up", "down"):
        bid = _number(snapshot.get(f"predict_{side}_bid"))
        ask = _number(snapshot.get(f"predict_{side}_ask"))
        if bid is not None and ask is not None and ask >= bid:
            spreads.append(ask - bid)
    maximum_spread = max(spreads) if spreads else None

    if _volatility_alert(snapshot) or (maximum_return is not None and maximum_return >= VOLATILE_RETURN_BPS):
        base = 3
        regime = "VOLATILE_SHALLOW"
    elif (
        maximum_return is not None
        and maximum_return <= CALM_RETURN_BPS
        and maximum_spread is not None
        and maximum_spread <= TIGHT_SPREAD + 1e-9
    ):
        base = 15
        regime = "CALM_TIGHT_DEEP"
    else:
        base = 7
        regime = "NORMAL_MEDIUM"

    up_levels = base
    down_levels = base
    maker_delta = float(inventory.get("makerDelta") or 0.0)
    if abs(maker_delta) >= MIN_MAKER_DELTA_SHARES:
        if maker_delta > 0:
            up_levels = max(3, base - 4)
            down_levels = min(15, base + 4)
        else:
            up_levels = min(15, base + 4)
            down_levels = max(3, base - 4)
        regime += "_INVENTORY_TILT"
    return {
        "upLevels": up_levels,
        "downLevels": down_levels,
        "baseLevels": base,
        "regime": regime,
        "maximumReturn1sBps": maximum_return,
        "maximumPredictSpread": maximum_spread,
    }


def depth_plan(variant: dict[str, Any], snapshot: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    if variant.get("dynamicDepth"):
        plan = dynamic_depths(snapshot, inventory)
    else:
        levels = int(variant.get("fixedLevels") or 7)
        plan = {
            "upLevels": levels,
            "downLevels": levels,
            "baseLevels": levels,
            "regime": "FIXED_CONTROL",
            "maximumReturn1sBps": None,
            "maximumPredictSpread": None,
        }
    plan.update({
        "reservationSkewEnabled": False,
        "upPriceOffsetTicks": 0,
        "downPriceOffsetTicks": 0,
        "suspendUp": False,
        "suspendDown": False,
    })
    if variant.get("targetCoreMaker"):
        plan.update({
            "upLevels": TARGET_CORE_LEVELS_PER_SIDE,
            "downLevels": TARGET_CORE_LEVELS_PER_SIDE,
            "baseLevels": TARGET_CORE_LEVELS_PER_SIDE,
            "regime": "TARGET_CORE_SOFT_CORRIDOR_TWO_SIDED",
            "reservationSkewEnabled": True,
            "targetCoreMakerEnabled": True,
            "softCorridorShares": TARGET_CORE_SOFT_DELTA_SHARES,
            "hardCorridorShares": TARGET_CORE_HARD_DELTA_SHARES,
        })
        delta = float(inventory.get("makerDelta") or 0.0)
        residual = inventory.get("makerResidualSide")
        absolute = abs(delta)
        seconds_left = _number(snapshot.get("seconds_left"))
        if residual and absolute >= TARGET_CORE_HARD_DELTA_SHARES - 1e-9:
            repair_levels = min(
                TARGET_CORE_MAX_REPAIR_LEVELS,
                max(1, int(math.ceil(absolute / maker_grid.SHARES - 1e-9))),
            )
            if residual == "UP":
                plan.update({
                    "upLevels": 0, "downLevels": repair_levels,
                    "suspendUp": True, "upPriceOffsetTicks": -2, "downPriceOffsetTicks": 2,
                    "regime": "TARGET_CORE_UP_HARD_DEFICIT_DOWN_ONLY",
                })
            else:
                plan.update({
                    "upLevels": repair_levels, "downLevels": 0,
                    "suspendDown": True, "upPriceOffsetTicks": 2, "downPriceOffsetTicks": -2,
                    "regime": "TARGET_CORE_DOWN_HARD_DEFICIT_UP_ONLY",
                })
        elif residual and absolute >= TARGET_CORE_SOFT_DELTA_SHARES - 1e-9:
            if residual == "UP":
                plan.update({
                    "upLevels": TARGET_CORE_TILTED_HEAVY_LEVELS,
                    "downLevels": TARGET_CORE_LEVELS_PER_SIDE,
                    "upPriceOffsetTicks": -1, "downPriceOffsetTicks": 1,
                    "regime": "TARGET_CORE_UP_SOFT_TILT_DOWN",
                })
            else:
                plan.update({
                    "upLevels": TARGET_CORE_LEVELS_PER_SIDE,
                    "downLevels": TARGET_CORE_TILTED_HEAVY_LEVELS,
                    "upPriceOffsetTicks": 1, "downPriceOffsetTicks": -1,
                    "regime": "TARGET_CORE_DOWN_SOFT_TILT_UP",
                })
        if seconds_left is not None and seconds_left <= TARGET_CORE_TAIL_SHALLOW_SECONDS:
            plan["upLevels"] = min(int(plan["upLevels"]), TARGET_CORE_TAIL_LEVELS)
            plan["downLevels"] = min(int(plan["downLevels"]), TARGET_CORE_TAIL_LEVELS)
            plan["regime"] += "_TAIL_SHALLOW"
        return plan
    if variant.get("pooledInventory"):
        balance_first = bool(variant.get("balanceFirst"))
        plan.update({
            "upLevels": BALANCE_FIRST_BALANCED_LEVELS if balance_first else POOLED_LEVELS_PER_SIDE,
            "downLevels": BALANCE_FIRST_BALANCED_LEVELS if balance_first else POOLED_LEVELS_PER_SIDE,
            "baseLevels": BALANCE_FIRST_BALANCED_LEVELS if balance_first else POOLED_LEVELS_PER_SIDE,
            "regime": "BALANCE_FIRST_ONE_PAIR" if balance_first else "POOLED_BALANCED_THREE_LEVEL",
            "reservationSkewEnabled": True,
            "pooledInventoryEnabled": True,
            "balanceFirstEnabled": balance_first,
            "forceImmediateRebalance": False,
            "lateRiskFreeze": False,
        })
        ratio = float(inventory.get("makerImbalanceRatio") or 0.0)
        delta = float(inventory.get("makerDelta") or 0.0)
        residual = inventory.get("makerResidualSide")
        seconds_left = _number(snapshot.get("seconds_left"))
        if balance_first:
            repair_levels = min(
                BALANCE_FIRST_MAX_REPAIR_LEVELS,
                max(1, int(math.ceil(abs(delta) / maker_grid.SHARES - 1e-9))),
            ) if residual else 0
            skew_ticks = POOLED_MAX_SKEW_TICKS if residual else 0
            if residual == "UP":
                plan.update({
                    "upLevels": 0,
                    "downLevels": repair_levels,
                    "upPriceOffsetTicks": -skew_ticks,
                    "downPriceOffsetTicks": skew_ticks,
                    "suspendUp": True,
                    "regime": "BALANCE_FIRST_UP_HEAVY_REPAIR_DOWN",
                })
            elif residual == "DOWN":
                plan.update({
                    "upLevels": repair_levels,
                    "downLevels": 0,
                    "upPriceOffsetTicks": skew_ticks,
                    "downPriceOffsetTicks": -skew_ticks,
                    "suspendDown": True,
                    "regime": "BALANCE_FIRST_DOWN_HEAVY_REPAIR_UP",
                })
            if seconds_left is not None and seconds_left <= BALANCE_FIRST_STOP_NEW_RISK_SECONDS:
                plan["lateRiskFreeze"] = True
                plan["forceImmediateRebalance"] = True
                if residual is None:
                    plan.update({
                        "upLevels": 0,
                        "downLevels": 0,
                        "suspendUp": True,
                        "suspendDown": True,
                        "regime": "BALANCE_FIRST_LATE_FLAT_FREEZE",
                    })
                else:
                    plan["regime"] += "_LATE_REPAIR_ONLY"
            return plan
        severe = (
            abs(delta) >= POOLED_IMMEDIATE_DELTA_SHARES - 1e-9
            or ratio >= POOLED_REDUCE_ONLY_IMBALANCE_RATIO - 1e-9
        )
        skew_ticks = min(
            POOLED_MAX_SKEW_TICKS,
            max(1, int(math.ceil(ratio / POOLED_REDUCE_ONLY_IMBALANCE_RATIO))),
        ) if residual else 0
        if residual == "UP":
            plan["upLevels"] = 0 if severe else 1
            plan["downLevels"] = POOLED_LEVELS_PER_SIDE
            plan["upPriceOffsetTicks"] = -skew_ticks
            plan["downPriceOffsetTicks"] = skew_ticks
            plan["suspendUp"] = severe
            plan["regime"] = "POOLED_UP_HEAVY_DEFICIT_DOWN_ONLY" if severe else "POOLED_UP_HEAVY_TILT"
        elif residual == "DOWN":
            plan["upLevels"] = POOLED_LEVELS_PER_SIDE
            plan["downLevels"] = 0 if severe else 1
            plan["upPriceOffsetTicks"] = skew_ticks
            plan["downPriceOffsetTicks"] = -skew_ticks
            plan["suspendDown"] = severe
            plan["regime"] = "POOLED_DOWN_HEAVY_DEFICIT_UP_ONLY" if severe else "POOLED_DOWN_HEAVY_TILT"
        return plan
    if not variant.get("reservationSkew"):
        return plan

    seconds_left = _number(snapshot.get("seconds_left"))
    if seconds_left is not None and seconds_left <= LATE_DEPTH_SECONDS:
        plan["upLevels"] = min(int(plan["upLevels"]), 3)
        plan["downLevels"] = min(int(plan["downLevels"]), 3)
        plan["regime"] += "_LATE_SHALLOW"
    elif seconds_left is not None and seconds_left <= MID_DEPTH_SECONDS:
        plan["upLevels"] = min(int(plan["upLevels"]), 7)
        plan["downLevels"] = min(int(plan["downLevels"]), 7)
        plan["regime"] += "_MIDTIME_CAP"

    ratio = float(inventory.get("makerImbalanceRatio") or 0.0)
    residual = inventory.get("makerResidualSide")
    skew_ticks = min(MAX_RESERVATION_SKEW_TICKS, int(math.ceil(ratio / RESERVATION_SKEW_STEP_RATIO))) if ratio > 0 else 0
    if residual == "UP" and skew_ticks:
        plan["upPriceOffsetTicks"] = -skew_ticks
        plan["downPriceOffsetTicks"] = skew_ticks
        plan["suspendUp"] = ratio >= REDUCE_ONLY_IMBALANCE_RATIO
        plan["regime"] += "_UP_HEAVY_REDUCE"
    elif residual == "DOWN" and skew_ticks:
        plan["upPriceOffsetTicks"] = skew_ticks
        plan["downPriceOffsetTicks"] = -skew_ticks
        plan["suspendDown"] = ratio >= REDUCE_ONLY_IMBALANCE_RATIO
        plan["regime"] += "_DOWN_HEAVY_REDUCE"

    if str(plan["regime"]).startswith("VOLATILE_SHALLOW"):
        plan["upPriceOffsetTicks"] = int(plan["upPriceOffsetTicks"]) - 1
        plan["downPriceOffsetTicks"] = int(plan["downPriceOffsetTicks"]) - 1
        plan["regime"] += "_VOL_WIDEN"
    plan["reservationSkewEnabled"] = True
    return plan


def pooled_rebalance_required(
    variant: dict[str, Any],
    previous_plan: dict[str, Any] | None,
    next_plan: dict[str, Any],
    *,
    fills_in_batch: int,
) -> bool:
    if not (variant.get("pooledInventory") or variant.get("targetCoreMaker")) or previous_plan is None:
        return False
    keys = (
        "upLevels", "downLevels", "upPriceOffsetTicks", "downPriceOffsetTicks",
        "suspendUp", "suspendDown", "regime",
    )
    changed = tuple(previous_plan.get(key) for key in keys) != tuple(next_plan.get(key) for key in keys)
    return changed and (fills_in_batch > 0 or bool(next_plan.get("forceImmediateRebalance")))


def target_core_signal(snapshot: dict[str, Any]) -> dict[str, Any]:
    contributions: dict[str, Any] = {}
    weighted = available_weight = 0.0
    for feature, weight, scale, mode in TARGET_CORE_FEATURES:
        raw = _number(snapshot.get(feature))
        if raw is None:
            contributions[feature] = {"raw": None, "normalized": None, "weight": weight}
            continue
        normalized = math.tanh(raw / scale) if mode == "TANH" else max(-1.0, min(1.0, raw / scale))
        weighted += weight * normalized
        available_weight += weight
        contributions[feature] = {"raw": raw, "normalized": normalized, "weight": weight}
    score = weighted / available_weight if available_weight else 0.0
    side = "UP" if score > 0 else "DOWN" if score < 0 else None
    return {
        "score": score,
        "confidence": abs(score),
        "side": side,
        "availableFeatures": sum(item["normalized"] is not None for item in contributions.values()),
        "availableWeight": available_weight,
        "contributions": contributions,
    }


def decide_target_core_taker(
    snapshot: dict[str, Any],
    inventory: dict[str, Any],
    *,
    expected_market_id: int,
    now_ms: int,
    last_trade_ms: int | None,
    last_trade_side: str | None,
) -> dict[str, Any]:
    signal_ms = int(_number(snapshot.get("sampled_at_ms")) or 0)
    sample_market_id = int(_number(snapshot.get("market_id")) or 0)
    sample_age_ms = max(0, now_ms - signal_ms) if signal_ms else None
    predict_age_ms = _number(snapshot.get("predict_receipt_age_ms"))
    seconds_left = _number(snapshot.get("seconds_left"))
    signal = target_core_signal(snapshot)
    side = signal["side"]
    confidence = float(signal["confidence"])
    ask = _number(snapshot.get("predict_up_ask" if side == "UP" else "predict_down_ask")) if side else None
    bid = _number(snapshot.get("predict_up_bid" if side == "UP" else "predict_down_bid")) if side else None
    midpoint = (ask + bid) / 2 if ask is not None and bid is not None else None
    signed_alpha = TARGET_CORE_ALPHA_PROBABILITY * float(signal["score"])
    fair_probability = None
    if midpoint is not None and side:
        fair_probability = max(0.01, min(0.99, midpoint + signed_alpha if side == "UP" else midpoint - signed_alpha))
    estimated_edge = (
        fair_probability - ask * (1 + TAKER_FEE_RATE)
        if fair_probability is not None and ask is not None else None
    )
    combined_delta = float(inventory.get("combinedDelta") or 0.0)
    residual_side = inventory.get("combinedResidualSide")
    same_as_residual = side in {"UP", "DOWN"} and side == residual_side
    correcting_residual = side in {"UP", "DOWN"} and residual_side in {"UP", "DOWN"} and side != residual_side
    inventory_scale = 1.0
    if same_as_residual and abs(combined_delta) >= TARGET_CORE_HARD_DELTA_SHARES - 1e-9:
        inventory_scale = 0.0
    elif same_as_residual and abs(combined_delta) >= TARGET_CORE_SOFT_DELTA_SHARES - 1e-9:
        inventory_scale = 0.50
    elif correcting_residual:
        inventory_scale = 1.25

    reason = "TARGET_CORE_PUBLIC_EDGE"
    if sample_market_id != int(expected_market_id):
        reason = "MARKET_MISMATCH"
    elif sample_age_ms is None or sample_age_ms > maker_grid.MAX_SAMPLE_AGE_MS:
        reason = "STALE_PUBLIC_SNAPSHOT"
    elif predict_age_ms is None or predict_age_ms > maker_grid.MAX_PREDICT_AGE_MS:
        reason = "STALE_PREDICT_BOOK"
    elif seconds_left is None or seconds_left <= TARGET_CORE_MIN_SECONDS_LEFT:
        reason = "TOO_LATE"
    elif int(signal["availableFeatures"]) < TARGET_CORE_MIN_AVAILABLE_FEATURES:
        reason = "INSUFFICIENT_PUBLIC_FEATURES"
    elif side not in {"UP", "DOWN"} or confidence < TARGET_CORE_SCORE_THRESHOLD:
        reason = "PUBLIC_SCORE_TOO_WEAK"
    elif ask is None or not 0 < ask <= MAX_TAKER_ASK:
        reason = "ASK_UNEXECUTABLE"
    elif estimated_edge is None or estimated_edge < TARGET_CORE_MIN_EDGE:
        reason = "NO_FEE_ADJUSTED_EDGE"
    elif inventory_scale <= 0:
        reason = "INVENTORY_HARD_BLOCK"
    elif last_trade_ms is not None:
        cooldown = (
            TARGET_CORE_REPEAT_COOLDOWN_MS
            if side == last_trade_side else TARGET_CORE_FLIP_COOLDOWN_MS
        )
        if now_ms - last_trade_ms < cooldown:
            reason = "COOLDOWN"

    return {
        "decision": "TRADE" if reason == "TARGET_CORE_PUBLIC_EDGE" else "SKIP",
        "reason": reason,
        "side": side,
        "signalAtMs": signal_ms or None,
        "sampleAgeMs": sample_age_ms,
        "predictReceiptAgeMs": predict_age_ms,
        "secondsLeft": seconds_left,
        "signal": signal,
        "ask": ask,
        "midpoint": midpoint,
        "fairProbability": fair_probability,
        "estimatedEdgePerShare": estimated_edge,
        "inventoryScale": inventory_scale,
        "sameAsResidual": same_as_residual,
        "correctingResidual": correcting_residual,
        "combinedDeltaBefore": combined_delta,
    }


def target_core_taker_execution(decision: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, float] | None:
    side = str(decision.get("side") or "")
    ask = _number(snapshot.get("predict_up_ask" if side == "UP" else "predict_down_ask"))
    if side not in {"UP", "DOWN"} or ask is None or not 0 < ask <= MAX_TAKER_ASK:
        return None
    confidence = float(decision.get("signal", {}).get("confidence") or 0.0)
    edge = max(0.0, float(decision.get("estimatedEdgePerShare") or 0.0))
    confidence_strength = max(0.0, min(1.0, (confidence - TARGET_CORE_SCORE_THRESHOLD) / (1 - TARGET_CORE_SCORE_THRESHOLD)))
    edge_strength = max(0.0, min(1.0, edge / 0.05))
    strength = 0.65 * confidence_strength + 0.35 * edge_strength
    principal = TARGET_CORE_MIN_PRINCIPAL_USDT + 9.0 * strength
    principal *= float(decision.get("inventoryScale") or 0.0)
    if decision.get("correctingResidual"):
        correction_notional = min(5.0, abs(float(decision.get("combinedDeltaBefore") or 0.0)) * ask * 0.10)
        principal += correction_notional
    principal = max(TARGET_CORE_MIN_PRINCIPAL_USDT, min(TARGET_CORE_MAX_PRINCIPAL_USDT, principal))
    shares = principal / ask
    fee = principal * TAKER_FEE_RATE
    return {
        "ask": ask,
        "shares": shares,
        "principalUsdt": principal,
        "feeUsdt": fee,
        "totalCostUsdt": principal + fee,
        "signalStrength": strength,
    }


def desired_grid(snapshot: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    up_levels = max(0, min(15, int(plan.get("upLevels") or 0)))
    down_levels = max(0, min(15, int(plan.get("downLevels") or 0)))
    if plan.get("reservationSkewEnabled"):
        pair = maker_grid.anchors(snapshot)
        if pair is None:
            return []
        up_anchor = round(pair[0] + int(plan.get("upPriceOffsetTicks") or 0) * maker_grid.GRID, 2)
        down_anchor = round(pair[1] + int(plan.get("downPriceOffsetTicks") or 0) * maker_grid.GRID, 2)
        up_ask = _number(snapshot.get("predict_up_ask"))
        down_ask = _number(snapshot.get("predict_down_ask"))
        if up_ask is None or down_ask is None:
            return []
        up_anchor = min(up_anchor, round(up_ask - maker_grid.GRID, 2))
        down_anchor = min(down_anchor, round(down_ask - maker_grid.GRID, 2))
        while up_anchor + down_anchor > maker_grid.MAX_PAIR_PRICE_SUM + 1e-9:
            if up_anchor >= down_anchor:
                up_anchor = round(up_anchor - maker_grid.GRID, 2)
            else:
                down_anchor = round(down_anchor - maker_grid.GRID, 2)
        result: list[dict[str, Any]] = []
        for side, levels, anchor, suspended in (
            ("UP", up_levels, up_anchor, bool(plan.get("suspendUp"))),
            ("DOWN", down_levels, down_anchor, bool(plan.get("suspendDown"))),
        ):
            if suspended:
                continue
            for level in range(levels):
                price = round(anchor - level * maker_grid.GRID, 2)
                if price <= 0 or price * maker_grid.SHARES + 1e-9 < maker_grid.MIN_NOTIONAL_USDT:
                    continue
                result.append({"side": side, "level": level, "price": price, "shares": maker_grid.SHARES})
        return result
    candidates = maker_grid.desired_grid(snapshot, max(up_levels, down_levels))
    return [
        order
        for order in candidates
        if int(order["level"]) < (up_levels if order["side"] == "UP" else down_levels)
    ]


def microstructure_confluence(snapshot: dict[str, Any], correction_side: str | None) -> dict[str, Any]:
    direction_score = _number(snapshot.get("direction_score"))
    direction_side = _side(direction_score, threshold=MIN_DIRECTION_SCORE)
    support = {
        "futuresQueue": _side(_number(snapshot.get("futures_queue_imbalance"))),
        "futuresTaker1s": _side(_number(snapshot.get("futures_taker_imbalance_1s"))),
    }
    supporting_votes = sum(value == correction_side for value in support.values()) if correction_side else 0
    opposing_votes = sum(value not in {None, correction_side} for value in support.values()) if correction_side else 0
    agreed = bool(correction_side and direction_side == correction_side and supporting_votes >= 1)
    return {
        "correctionSide": correction_side,
        "directionSide": direction_side,
        "directionScore": direction_score,
        "supportVotes": support,
        "supportingVotes": supporting_votes,
        "opposingVotes": opposing_votes,
        "agreed": agreed,
    }


def decide_taker(
    snapshot: dict[str, Any],
    inventory: dict[str, Any],
    *,
    expected_market_id: int,
    now_ms: int,
    last_trade_ms: int | None,
) -> dict[str, Any]:
    sampled_at_ms = int(_number(snapshot.get("sampled_at_ms")) or 0)
    sample_market_id = int(_number(snapshot.get("market_id")) or 0)
    sample_age_ms = max(0, now_ms - sampled_at_ms) if sampled_at_ms else None
    predict_age_ms = _number(snapshot.get("predict_receipt_age_ms"))
    seconds_left = _number(snapshot.get("seconds_left"))
    correction_side = inventory.get("correctionSide")
    confluence = microstructure_confluence(snapshot, str(correction_side) if correction_side else None)

    reason = "INVENTORY_MICRO_CONFLUENCE"
    if sample_market_id != int(expected_market_id):
        reason = "MARKET_MISMATCH"
    elif sample_age_ms is None or sample_age_ms > maker_grid.MAX_SAMPLE_AGE_MS:
        reason = "STALE_PUBLIC_SNAPSHOT"
    elif predict_age_ms is None or predict_age_ms > maker_grid.MAX_PREDICT_AGE_MS:
        reason = "STALE_PREDICT_BOOK"
    elif seconds_left is None or seconds_left <= MIN_TAKER_SECONDS_LEFT:
        reason = "TOO_LATE"
    elif not inventory.get("baselineReady"):
        reason = "MAKER_BASELINE_NOT_ESTABLISHED"
    elif correction_side not in {"UP", "DOWN"}:
        reason = "NO_UNCORRECTED_MAKER_IMBALANCE"
    elif not confluence["agreed"]:
        reason = "NO_INVENTORY_MICRO_CONFLUENCE"
    else:
        ask = _number(snapshot.get("predict_up_ask" if correction_side == "UP" else "predict_down_ask"))
        if ask is None or not 0 < ask <= MAX_TAKER_ASK:
            reason = "ASK_UNEXECUTABLE"
        elif last_trade_ms is not None and now_ms - last_trade_ms < TAKER_COOLDOWN_MS:
            reason = "COOLDOWN"

    return {
        "decision": "TRADE" if reason == "INVENTORY_MICRO_CONFLUENCE" else "SKIP",
        "reason": reason,
        "side": correction_side,
        "signalAtMs": sampled_at_ms or None,
        "sampleAgeMs": sample_age_ms,
        "predictReceiptAgeMs": predict_age_ms,
        "secondsLeft": seconds_left,
        "inventory": inventory,
        "confluence": confluence,
    }


def taker_execution(side: str, snapshot: dict[str, Any], inventory: dict[str, Any]) -> dict[str, float] | None:
    ask = _number(snapshot.get("predict_up_ask" if side == "UP" else "predict_down_ask"))
    if ask is None or not 0 < ask <= MAX_TAKER_ASK:
        return None
    residual = abs(float(inventory.get("combinedDelta") or 0.0))
    minimum_shares = maker_grid.MIN_NOTIONAL_USDT / ask
    shares = min(TAKER_MAX_SHARES, residual * TAKER_CORRECTION_FRACTION)
    shares = max(minimum_shares, shares)
    shares = min(shares, residual)
    if shares <= 0 or shares * ask + 1e-9 < maker_grid.MIN_NOTIONAL_USDT:
        return None
    principal = shares * ask
    fee = principal * TAKER_FEE_RATE
    return {
        "ask": ask,
        "shares": shares,
        "principalUsdt": principal,
        "feeUsdt": fee,
        "totalCostUsdt": principal + fee,
    }


def policy(variant: dict[str, Any]) -> dict[str, Any]:
    return {
        "cohort": variant["cohort"],
        "paperOnly": True,
        "targetEventsUsed": False,
        "maker": {
            **maker_grid.policy(int(variant.get("fixedLevels") or 15)),
            "depthMode": (
                "15-level two-sided cent grid with a 54/108-share soft/hard inventory corridor"
                if variant.get("targetCoreMaker")
                else
                "one balanced pair with missing-lot-matched repair and a 60-second risk freeze"
                if variant.get("balanceFirst")
                else "fixed 3 with immediate pooled inventory rebalancing"
                if variant.get("pooledInventory")
                else "dynamic 3/7/15 with inventory tilt"
                if variant.get("dynamicDepth")
                else f"fixed {int(variant.get('fixedLevels') or 7)} control"
            ),
        },
        "inventoryBaseline": {
            "minimumMakerSharesPerSide": MIN_BASELINE_SHARES_PER_SIDE,
            "minimumMakerDeltaShares": MIN_MAKER_DELTA_SHARES,
            "minimumCombinedDeltaShares": MIN_COMBINED_DELTA_SHARES,
            "minimumMakerImbalanceRatio": MIN_MAKER_IMBALANCE_RATIO,
            "interpretation": "Maker paired inventory is the baseline; Taker may only reduce a same-sided uncorrected residual.",
        },
        "taker": {
            "feeBps": TAKER_FEE_RATE * 10_000,
            "maximumAsk": MAX_TAKER_ASK,
            "cooldownMs": TAKER_COOLDOWN_MS,
            "maximumShares": TAKER_MAX_SHARES,
            "correctionFraction": TAKER_CORRECTION_FRACTION,
            "signal": "maker residual + uncorrected combined residual + direction score + at least one futures support vote",
        },
        "dynamicDepth": {
            "anchors": [3, 7, 15],
            "volatileAtReturn1sBps": VOLATILE_RETURN_BPS,
            "calmAtReturn1sBps": CALM_RETURN_BPS,
            "tightSpread": TIGHT_SPREAD,
            "inventoryTiltLevels": 4,
        } if variant.get("dynamicDepth") else None,
        "reservationSkew": {
            "enabled": bool(variant.get("reservationSkew")),
            "maximumTicks": POOLED_MAX_SKEW_TICKS if variant.get("pooledInventory") else MAX_RESERVATION_SKEW_TICKS,
            "oneTickPerMakerImbalanceRatio": RESERVATION_SKEW_STEP_RATIO,
            "reduceOnlyAtMakerImbalanceRatio": (
                POOLED_REDUCE_ONLY_IMBALANCE_RATIO
                if variant.get("pooledInventory") else REDUCE_ONLY_IMBALANCE_RATIO
            ),
            "lateDepthCap": {"secondsLeft": LATE_DEPTH_SECONDS, "levels": 3},
            "midDepthCap": {"secondsLeft": MID_DEPTH_SECONDS, "levels": 7},
            "volatileWidenTicks": 1,
            "sharesPerOrderPreserved": maker_grid.SHARES,
            "evidenceBoundary": "frozen from public inventory-market-making literature and pre-existing target structural evidence; not tuned to shared V1 forward outcomes",
        },
        "pooledInventory": {
            "enabled": bool(variant.get("pooledInventory")),
            "maximumConcurrentLevelsPerSide": (
                BALANCE_FIRST_MAX_REPAIR_LEVELS
                if variant.get("balanceFirst") else POOLED_LEVELS_PER_SIDE
            ),
            "balancedLevelsPerSide": (
                BALANCE_FIRST_BALANCED_LEVELS if variant.get("balanceFirst") else POOLED_LEVELS_PER_SIDE
            ),
            "immediateReduceOnlyAtShares": POOLED_IMMEDIATE_DELTA_SHARES,
            "immediateReduceOnlyAtRatio": POOLED_REDUCE_ONLY_IMBALANCE_RATIO,
            "maximumReservationSkewTicks": POOLED_MAX_SKEW_TICKS,
            "batchRule": "after each fill snapshot, cancel the stale generation when the Maker inventory plan changes; quote only the deficient side until pooled balance recovers",
            "balanceFirst": bool(variant.get("balanceFirst")),
            "repairLevelsMatchMissingLots": bool(variant.get("balanceFirst")),
            "stopNewBalancedRiskAtSecondsLeft": (
                BALANCE_FIRST_STOP_NEW_RISK_SECONDS if variant.get("balanceFirst") else None
            ),
            "targetPairedCoverage": (
                BALANCE_FIRST_TARGET_PAIRED_COVERAGE if variant.get("balanceFirst") else None
            ),
            "targetImbalance": (
                BALANCE_FIRST_TARGET_IMBALANCE if variant.get("balanceFirst") else None
            ),
            "evidenceBoundary": "frozen before deployment from 299 retained target markets: 91% median final pairing, 18-share parent cap, low same-price refill and price recenter evidence",
        } if variant.get("pooledInventory") else None,
        "targetCoreIntegrated": {
            "enabled": bool(variant.get("targetCoreIntegrated")),
            "evidenceFreeze": {
                "makerMarkets": 327,
                "makerParents": 33873,
                "pairCompletionMedianMs": 24_000,
                "pairCompletionP90Ms": 77_000,
                "pairedPriceSumMedian": 0.96,
                "finalPairedCoverageMedian": 0.9053,
                "finalImbalanceMedian": 0.0947,
                "interpretation": "allow a pooled absolute residual corridor while preserving complement-price quality; do not force immediate one-lot flattening",
            },
            "maker": {
                "levelsPerSide": TARGET_CORE_LEVELS_PER_SIDE,
                "sharesPerOrder": maker_grid.SHARES,
                "softCorridorShares": TARGET_CORE_SOFT_DELTA_SHARES,
                "hardCorridorShares": TARGET_CORE_HARD_DELTA_SHARES,
                "hardRepairMaximumLevels": TARGET_CORE_MAX_REPAIR_LEVELS,
                "tailShallowSecondsLeft": TARGET_CORE_TAIL_SHALLOW_SECONDS,
                "stopNewQuotesSecondsLeft": maker_grid.MIN_SECONDS_LEFT,
                "maximumPairPriceSum": maker_grid.MAX_PAIR_PRICE_SUM,
            },
            "taker": {
                "features": [feature for feature, _, _, _ in TARGET_CORE_FEATURES],
                "continuousScoreThreshold": TARGET_CORE_SCORE_THRESHOLD,
                "minimumFeeAdjustedEdgePerShare": TARGET_CORE_MIN_EDGE,
                "alphaProbabilityScale": TARGET_CORE_ALPHA_PROBABILITY,
                "repeatCooldownMs": TARGET_CORE_REPEAT_COOLDOWN_MS,
                "flipCooldownMs": TARGET_CORE_FLIP_COOLDOWN_MS,
                "principalUsdtRange": [TARGET_CORE_MIN_PRINCIPAL_USDT, TARGET_CORE_MAX_PRINCIPAL_USDT],
                "sizing": "continuous public-signal confidence + fee-adjusted edge + shared Maker/Taker inventory correction",
            },
            "causality": "uses only fresh public signal snapshots and this paper cohort's own inventory; target events never enter Maker quotes, Taker decisions or sizing",
            "promotionBoundary": "forward paper evidence only; no live allowlist or runtime activation",
        } if variant.get("targetCoreIntegrated") else None,
    }
