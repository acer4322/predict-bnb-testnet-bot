from __future__ import annotations

from typing import Any

from . import predict_wallet_reconstructed_maker_strategy_v2 as v2

COHORT = "TARGET_MAKER_LIFECYCLE_GRID18_V3"
GRID = v2.GRID
SHARES_PER_ORDER = v2.SHARES_PER_ORDER
MIN_PRICE = v2.MIN_PRICE
MAX_PRICE = v2.MAX_PRICE
MIN_ORDER_NOTIONAL_USDT = v2.MIN_ORDER_NOTIONAL_USDT
MAX_PAIR_PRICE_SUM = v2.MAX_PAIR_PRICE_SUM
OPENING_MIN_SECONDS_LEFT = v2.OPENING_MIN_SECONDS_LEFT
REFILL_STOP_SECONDS_LEFT = v2.REFILL_STOP_SECONDS_LEFT
MIN_REST_MS = v2.MIN_REST_MS
ACTIVE_BAND_LEVELS = v2.ACTIVE_BAND_LEVELS
POST_FILL_DECISION_DELAY_MS = 1_200
SAME_PRICE_REFILL_COOLDOWN_MS = 1_000
MAX_POST_FILL_WAIT_MS = 5_000

number = v2.number
snapshot_is_usable = v2.snapshot_is_usable
inventory = v2.inventory
ask_touch_fill = v2.ask_touch_fill
opening_orders = v2.opening_orders
side_top_tick = v2.side_top_tick
toxic_flow = v2.toxic_flow


def _aligned(value: Any, side: str) -> float | None:
    parsed = number(value)
    if parsed is None:
        return None
    return parsed if side == "UP" else -parsed


def _vote_counts(snapshot: dict[str, Any], side: str) -> tuple[int, int, dict[str, float | None]]:
    aligned = {
        "direction": _aligned(snapshot.get("direction_score"), side),
        "spotQueue": _aligned(snapshot.get("spot_queue_imbalance"), side),
        "futuresQueue": _aligned(snapshot.get("futures_queue_imbalance"), side),
        "spotTaker": _aligned(snapshot.get("spot_taker_imbalance_1s"), side),
        "futuresTaker": _aligned(snapshot.get("futures_taker_imbalance_1s"), side),
        "spotReturn": _aligned(snapshot.get("spot_return_3s_bps"), side),
        "futuresReturn": _aligned(snapshot.get("futures_return_3s_bps"), side),
    }
    thresholds = {
        "direction": 0.25,
        "spotQueue": 0.40,
        "futuresQueue": 0.40,
        "spotTaker": 0.60,
        "futuresTaker": 0.60,
        "spotReturn": 1.50,
        "futuresReturn": 1.50,
    }
    supportive = 0
    adverse = 0
    for key, value in aligned.items():
        if value is None:
            continue
        threshold = thresholds[key]
        supportive += int(value >= threshold)
        adverse += int(value <= -threshold)
    return supportive, adverse, aligned


def continuation_decision(
    snapshot: dict[str, Any],
    inventory_state: dict[str, Any],
    *,
    filled_side: str,
) -> dict[str, Any]:
    """Transparent public-state proxy for the V3 Continue/Stop layer.

    The offline V3 research found fill-path features most stable, but the current
    ask-touch paper engine cannot observe partial-fill paths. This runtime cohort
    therefore does *not* fake those features. It uses only public flow plus its own
    post-fill inventory and keeps the decision conservative until partial-fill
    instrumentation exists.
    """

    seconds_left = number(snapshot.get("seconds_left"))
    supportive, adverse, aligned = _vote_counts(snapshot, filled_side)
    residual_side = inventory_state.get("residualSide")
    delta = abs(float(inventory_state.get("deltaShares") or 0.0))
    worsened_inventory = residual_side == filled_side and delta >= SHARES_PER_ORDER - 1e-9
    severe_inventory = residual_side == filled_side and delta >= 2 * SHARES_PER_ORDER - 1e-9

    reasons: list[str] = []
    stop = False
    if seconds_left is None or seconds_left <= REFILL_STOP_SECONDS_LEFT:
        stop = True
        reasons.append("TAIL_FREEZE")
    if severe_inventory:
        stop = True
        reasons.append("TWO_LOT_OVERWEIGHT")
    if adverse >= 3 and adverse - supportive >= 2:
        stop = True
        reasons.append("STRONG_ADVERSE_PUBLIC_FLOW")
    elif worsened_inventory and adverse >= 2:
        stop = True
        reasons.append("OVERWEIGHT_PLUS_ADVERSE_FLOW")

    return {
        "continue": not stop,
        "supportiveVotes": supportive,
        "adverseVotes": adverse,
        "worsenedInventory": worsened_inventory,
        "severeInventory": severe_inventory,
        "alignedPublicState": aligned,
        "reasons": reasons or ["CONTINUE_CAUSAL_PUBLIC_STATE"],
    }


def reprice_direction(
    snapshot: dict[str, Any],
    inventory_state: dict[str, Any],
    *,
    filled_side: str,
) -> dict[str, Any]:
    supportive, adverse, aligned = _vote_counts(snapshot, filled_side)
    residual_side = inventory_state.get("residualSide")
    overweight = residual_side == filled_side and abs(float(inventory_state.get("deltaShares") or 0.0)) >= SHARES_PER_ORDER - 1e-9

    # Offline V3 base rate was approximately 60% AWAY / 40% TOWARD. TOWARD is
    # therefore reserved for coherent supportive public state with no inventory
    # reason to back off; all ambiguous states fall back to AWAY.
    toward = supportive >= 2 and supportive > adverse and not overweight
    direction = "TOWARD_TOUCH" if toward else "AWAY_FROM_TOUCH"
    strength = supportive if toward else adverse + int(overweight)
    ticks = 1 if strength <= 2 else 2 if strength <= 4 else 3
    return {
        "direction": direction,
        "ticks": ticks,
        "supportiveVotes": supportive,
        "adverseVotes": adverse,
        "overweight": overweight,
        "alignedPublicState": aligned,
    }


def _same_price_refill_allowed(
    snapshot: dict[str, Any],
    inventory_state: dict[str, Any],
    *,
    filled_side: str,
) -> bool:
    supportive, adverse, _ = _vote_counts(snapshot, filled_side)
    residual_side = inventory_state.get("residualSide")
    delta = abs(float(inventory_state.get("deltaShares") or 0.0))
    repairing_deficient_side = residual_side is not None and residual_side != filled_side and delta >= SHARES_PER_ORDER - 1e-9
    price = number(snapshot.get(f"predict_{filled_side.lower()}_bid"))
    return bool(
        repairing_deficient_side
        and adverse == 0
        and supportive <= 1
        and price is not None
        and MIN_PRICE <= price <= 0.80
    )


def post_fill_plan(
    snapshot: dict[str, Any],
    inventory_state: dict[str, Any],
    filled_order: dict[str, Any],
) -> dict[str, Any]:
    side = str(filled_order.get("side") or "").upper()
    tick = int(filled_order.get("priceTick") or round(float(filled_order.get("price") or 0.0) / GRID))
    decision = continuation_decision(snapshot, inventory_state, filled_side=side)
    if not decision["continue"]:
        return {
            "action": "STOP",
            "decisionDelayMs": POST_FILL_DECISION_DELAY_MS,
            "filledSide": side,
            "sourcePriceTick": tick,
            "continuation": decision,
        }

    if _same_price_refill_allowed(snapshot, inventory_state, filled_side=side):
        return {
            "action": "SAME_PRICE_REFILL",
            "decisionDelayMs": POST_FILL_DECISION_DELAY_MS,
            "filledSide": side,
            "sourcePriceTick": tick,
            "targetPriceTick": tick,
            "targetPrice": round(tick * GRID, 2),
            "continuation": decision,
        }

    direction = reprice_direction(snapshot, inventory_state, filled_side=side)
    signed_ticks = int(direction["ticks"]) if direction["direction"] == "TOWARD_TOUCH" else -int(direction["ticks"])
    minimum_tick = round(MIN_PRICE / GRID)
    maximum_tick = round(MAX_PRICE / GRID)
    target_tick = max(minimum_tick, min(maximum_tick, tick + signed_ticks))

    # Do not cross the public ask or violate the paired-price ceiling. Moving
    # toward touch is capped at the current safe top; AWAY never needs that cap.
    if signed_ticks > 0:
        top = side_top_tick(snapshot, side)
        if top is not None:
            target_tick = min(target_tick, top)
    opposite = "down" if side == "UP" else "up"
    opposite_bid = number(snapshot.get(f"predict_{opposite}_bid"))
    while target_tick >= minimum_tick and opposite_bid is not None and target_tick * GRID + opposite_bid > MAX_PAIR_PRICE_SUM + 1e-9:
        target_tick -= 1

    if target_tick < minimum_tick or target_tick == tick:
        return {
            "action": "STOP",
            "decisionDelayMs": POST_FILL_DECISION_DELAY_MS,
            "filledSide": side,
            "sourcePriceTick": tick,
            "continuation": decision,
            "reprice": direction,
            "stopReason": "NO_SAFE_DISTINCT_REPRICE_LEVEL",
        }

    price = round(target_tick * GRID, 2)
    if price * SHARES_PER_ORDER + 1e-9 < MIN_ORDER_NOTIONAL_USDT:
        return {
            "action": "STOP",
            "decisionDelayMs": POST_FILL_DECISION_DELAY_MS,
            "filledSide": side,
            "sourcePriceTick": tick,
            "continuation": decision,
            "reprice": direction,
            "stopReason": "BELOW_MINIMUM_NOTIONAL",
        }

    return {
        "action": "REPRICE",
        "decisionDelayMs": POST_FILL_DECISION_DELAY_MS,
        "filledSide": side,
        "sourcePriceTick": tick,
        "targetPriceTick": target_tick,
        "targetPrice": price,
        "signedTicksTowardTouch": target_tick - tick,
        "reprice": direction,
        "continuation": decision,
    }


def planned_order(plan: dict[str, Any]) -> dict[str, Any] | None:
    if plan.get("action") not in {"SAME_PRICE_REFILL", "REPRICE"}:
        return None
    return {
        "side": str(plan["filledSide"]),
        "priceTick": int(plan["targetPriceTick"]),
        "price": float(plan["targetPrice"]),
        "shares": SHARES_PER_ORDER,
        "origin": f"LIFECYCLE_{plan['action']}",
    }


def policy() -> dict[str, Any]:
    return {
        "cohort": COHORT,
        "paperOnly": True,
        "forwardOnly": True,
        "historicalBackfill": False,
        "targetEventsDriveStrategy": False,
        "liveOrdersAffected": False,
        "controlCohort": v2.COHORT,
        "grid": GRID,
        "sharesPerOrder": SHARES_PER_ORDER,
        "openingLevelsPerSide": ACTIVE_BAND_LEVELS,
        "postFillDecisionDelayMs": POST_FILL_DECISION_DELAY_MS,
        "samePriceRefillCooldownMs": SAME_PRICE_REFILL_COOLDOWN_MS,
        "maximumPostFillWaitMs": MAX_POST_FILL_WAIT_MS,
        "lifecycle": {
            "stopContinue": "public-flow + own post-fill inventory only; partial-fill path is deliberately not faked",
            "continueDefault": "REPRICE unless a narrow inventory-repair/neutral-flow same-price refill condition holds",
            "repriceDirection": "TOWARD only on coherent supportive public flow without overweight inventory; ambiguous states default AWAY",
            "repriceTicks": "1/2/3 ticks from transparent public-flow vote strength; no target-event input",
        },
        "researchEvidence": {
            "continueStop": "V3 walk-forward found fill-path-only most stable, but current ask-touch paper engine lacks partial-fill observability",
            "refillVsReprice": "conditional continuation was dominated by reprice; refill prediction was weak",
            "signedReprice": "toward-vs-away was the strongest hierarchical V3 task",
            "timing": "observed fill-to-next-placement median was about 1.2 seconds; this replaces global 3-second recenter cadence",
        },
        "fillProxy": "identical strict later-ask-touch full-18 proxy as Maker Rules V2 for clean A/B",
        "limitations": "This cohort tests event-driven lifecycle mechanics, not the slow-partial-fill hypothesis. Slow-fill/Taker linkage is evaluated separately with 8778 high-resolution evidence.",
    }
