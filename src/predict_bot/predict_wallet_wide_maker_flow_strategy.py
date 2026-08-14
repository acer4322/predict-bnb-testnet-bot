from __future__ import annotations

import math
from typing import Any


COHORTS = (
    {
        "cohort": "WIDE_STATIC_06_99",
        "label": "Wide static",
        "refillMode": "NONE",
    },
    {
        "cohort": "WIDE_REFILL_UNTIL_30S",
        "label": "Wide refill until 30s",
        "refillMode": "ALL_UNTIL_30S",
    },
    {
        "cohort": "WIDE_RAILS_INNER_REFILL",
        "label": "Wide rails + inner 7 refill",
        "refillMode": "INNER_7_UNTIL_30S",
    },
)

GRID = 0.01
SHARES_PER_ORDER = 18.0
MIN_PRICE = 0.06
MAX_PRICE = 0.99
MIN_ORDER_NOTIONAL_USDT = 1.0
OPENING_MIN_SECONDS_LEFT = 270.0
REFILL_STOP_SECONDS_LEFT = 30.0
REFILL_COOLDOWN_MS = 1_000
INNER_REFILL_LEVELS = 7
MIN_REST_MS = 250
MAX_SAMPLE_AGE_MS = 1_000
MAX_PREDICT_AGE_MS = 2_000

FLOW_MIN_AGE_MS = 1_000
FLOW_MAX_AGE_MS = 5_000
FLOW_MIN_ABS_SHARES = SHARES_PER_ORDER
PRIMARY_BASE_PRINCIPAL_USDT = 5.0
PRIMARY_MIN_PRINCIPAL_USDT = 1.0
PRIMARY_FEE_RATE = 0.02
PRIMARY_MAX_ASK = 0.95
PRIMARY_MIN_SECONDS_LEFT = 5.0
PRIMARY_COOLDOWN_MS = 10_000
MICRO_MIN_SUPPORT_VOTES = 2

INVENTORY_SOFT_SHARE_RATIO = 0.10
INVENTORY_HARD_SHARE_RATIO = 0.20
INVENTORY_HARD_ABS_SHARES = 180.0
INVENTORY_SOFT_CAPITAL_RATIO = 0.30
INVENTORY_HARD_CAPITAL_RATIO = 0.50
INVENTORY_SOFT_SIZE_SCALE = 0.50

TAIL_SECONDS = 60.0
TAIL_MAX_ASK = 0.12
TAIL_PRINCIPAL_USDT = 1.0
TAIL_BUDGET_RATE = 0.02
TAIL_MINIMUM_BUDGET_USDT = 1.0
TAIL_COOLDOWN_MS = 15_000
TAIL_MAX_PARENTS = 4


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _side(value: float, threshold: float = 1e-9) -> str | None:
    return "UP" if value > threshold else "DOWN" if value < -threshold else None


def _floor_tick(value: Any) -> int | None:
    parsed = _number(value)
    if parsed is None:
        return None
    return math.floor((parsed + 1e-9) / GRID)


def snapshot_is_usable(snapshot: dict[str, Any], *, market_id: int, now_ms: int) -> tuple[bool, str]:
    if int(_number(snapshot.get("market_id")) or 0) != int(market_id):
        return False, "MARKET_MISMATCH"
    sampled_at_ms = int(_number(snapshot.get("sampled_at_ms")) or 0)
    if sampled_at_ms <= 0 or now_ms - sampled_at_ms > MAX_SAMPLE_AGE_MS:
        return False, "STALE_PUBLIC_SNAPSHOT"
    predict_age_ms = _number(snapshot.get("predict_receipt_age_ms"))
    if predict_age_ms is None or predict_age_ms > MAX_PREDICT_AGE_MS:
        return False, "STALE_PREDICT_BOOK"
    seconds_left = _number(snapshot.get("seconds_left"))
    if seconds_left is None:
        return False, "NO_MARKET_CLOCK"
    if seconds_left <= 0:
        return False, "MARKET_ENDED"
    return True, "OK"


def opening_grid(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    minimum_tick = round(MIN_PRICE / GRID)
    maximum_tick = round(MAX_PRICE / GRID)
    for side in ("UP", "DOWN"):
        key = side.lower()
        bid_tick = _floor_tick(snapshot.get(f"predict_{key}_bid"))
        ask_tick = _floor_tick((_number(snapshot.get(f"predict_{key}_ask")) or 0.0) - GRID)
        if bid_tick is None or ask_tick is None:
            continue
        top_tick = min(maximum_tick, bid_tick, ask_tick)
        for tick in range(minimum_tick, top_tick + 1):
            price = round(tick * GRID, 2)
            if price * SHARES_PER_ORDER + 1e-9 < MIN_ORDER_NOTIONAL_USDT:
                continue
            orders.append({"side": side, "priceTick": tick, "price": price, "shares": SHARES_PER_ORDER})
    return orders


def may_refill(
    variant: dict[str, Any],
    *,
    side: str,
    price_tick: int,
    snapshot: dict[str, Any],
) -> tuple[bool, str]:
    mode = str(variant.get("refillMode") or "NONE")
    if mode == "NONE":
        return False, "STATIC_NO_REFILL"
    seconds_left = _number(snapshot.get("seconds_left"))
    if seconds_left is None or seconds_left <= REFILL_STOP_SECONDS_LEFT:
        return False, "REFILL_CUTOFF_30S"
    key = side.lower()
    bid_tick = _floor_tick(snapshot.get(f"predict_{key}_bid"))
    ask = _number(snapshot.get(f"predict_{key}_ask"))
    if bid_tick is None or ask is None:
        return False, "NO_EXECUTABLE_BOOK"
    price = round(price_tick * GRID, 2)
    if price_tick > bid_tick or price >= ask - 1e-9:
        return False, "WAIT_PRICE_RECOVERY"
    if mode == "INNER_7_UNTIL_30S" and price_tick < bid_tick - (INNER_REFILL_LEVELS - 1):
        return False, "OUTSIDE_INNER_7"
    return True, "REFILL_ALLOWED"


def maker_flow(fill_events: list[dict[str, Any]], *, now_ms: int) -> dict[str, Any]:
    selected = [
        event for event in fill_events
        if FLOW_MIN_AGE_MS <= now_ms - int(event.get("filledAtMs") or 0) <= FLOW_MAX_AGE_MS
    ]
    up = sum(float(event.get("shares") or 0.0) for event in selected if event.get("side") == "UP")
    down = sum(float(event.get("shares") or 0.0) for event in selected if event.get("side") == "DOWN")
    delta = up - down
    return {
        "windowMs": [FLOW_MIN_AGE_MS, FLOW_MAX_AGE_MS],
        "events": len(selected),
        "upShares": up,
        "downShares": down,
        "deltaShares": delta,
        "side": _side(delta, FLOW_MIN_ABS_SHARES - 1e-9),
    }


def inventory_state(
    *,
    maker_up_shares: float = 0.0,
    maker_down_shares: float = 0.0,
    maker_up_cost: float = 0.0,
    maker_down_cost: float = 0.0,
    primary_up_shares: float = 0.0,
    primary_down_shares: float = 0.0,
    primary_up_principal: float = 0.0,
    primary_down_principal: float = 0.0,
    tail_up_shares: float = 0.0,
    tail_down_shares: float = 0.0,
    tail_principal: float = 0.0,
) -> dict[str, Any]:
    maker_total = maker_up_shares + maker_down_shares
    maker_delta = maker_up_shares - maker_down_shares
    maker_cost_total = maker_up_cost + maker_down_cost
    maker_capital_delta = maker_up_cost - maker_down_cost
    main_up_capital = maker_up_cost + primary_up_principal
    main_down_capital = maker_down_cost + primary_down_principal
    main_capital_delta = main_up_capital - main_down_capital
    return {
        "makerUpShares": maker_up_shares,
        "makerDownShares": maker_down_shares,
        "makerUpCostUsdt": maker_up_cost,
        "makerDownCostUsdt": maker_down_cost,
        "makerDeltaShares": maker_delta,
        "makerResidualSide": _side(maker_delta),
        "makerShareImbalanceRatio": abs(maker_delta) / maker_total if maker_total else 0.0,
        "makerCapitalImbalanceRatio": abs(maker_capital_delta) / maker_cost_total if maker_cost_total else 0.0,
        "makerPairedCoverage": 2 * min(maker_up_shares, maker_down_shares) / maker_total if maker_total else None,
        "primaryUpShares": primary_up_shares,
        "primaryDownShares": primary_down_shares,
        "primaryUpPrincipalUsdt": primary_up_principal,
        "primaryDownPrincipalUsdt": primary_down_principal,
        "primaryPrincipalUsdt": primary_up_principal + primary_down_principal,
        "mainCapitalSide": _side(main_capital_delta),
        "mainUpCapitalUsdt": main_up_capital,
        "mainDownCapitalUsdt": main_down_capital,
        "tailUpShares": tail_up_shares,
        "tailDownShares": tail_down_shares,
        "tailPrincipalUsdt": tail_principal,
    }


def microstructure_confirmation(snapshot: dict[str, Any], side: str | None) -> dict[str, Any]:
    sign = 1.0 if side == "UP" else -1.0 if side == "DOWN" else 0.0
    features = {
        "directionScore": _number(snapshot.get("direction_score")),
        "futuresTaker1s": _number(snapshot.get("futures_taker_imbalance_1s")),
        "spotTaker1s": _number(snapshot.get("spot_taker_imbalance_1s")),
        "futuresQueue": _number(snapshot.get("futures_queue_imbalance")),
    }
    valid = {name: value for name, value in features.items() if value is not None and abs(value) > 1e-9}
    supporting = [name for name, value in valid.items() if float(value) * sign > 0]
    opposing = [name for name, value in valid.items() if float(value) * sign < 0]
    return {
        "side": side,
        "validVotes": len(valid),
        "supportVotes": len(supporting),
        "opposingVotes": len(opposing),
        "supporting": supporting,
        "opposing": opposing,
        "features": features,
        "confirmed": side in {"UP", "DOWN"} and len(supporting) >= MICRO_MIN_SUPPORT_VOTES,
    }


def primary_decision(
    snapshot: dict[str, Any],
    inventory: dict[str, Any],
    flow: dict[str, Any],
    *,
    expected_market_id: int,
    now_ms: int,
    last_trade_ms: int | None,
) -> dict[str, Any]:
    sampled_at_ms = int(_number(snapshot.get("sampled_at_ms")) or 0)
    seconds_left = _number(snapshot.get("seconds_left"))
    side = flow.get("side")
    confirmation = microstructure_confirmation(snapshot, str(side) if side else None)
    maker_side = inventory.get("makerResidualSide")
    share_ratio = float(inventory.get("makerShareImbalanceRatio") or 0.0)
    capital_ratio = float(inventory.get("makerCapitalImbalanceRatio") or 0.0)
    same_as_inventory = side in {"UP", "DOWN"} and side == maker_side
    guard = "NONE"
    size_scale = 1.0
    if same_as_inventory and (
        abs(float(inventory.get("makerDeltaShares") or 0.0)) >= INVENTORY_HARD_ABS_SHARES
        or share_ratio >= INVENTORY_HARD_SHARE_RATIO
        or capital_ratio >= INVENTORY_HARD_CAPITAL_RATIO
    ):
        guard = "HARD_SKIP_SAME_DIRECTION"
        size_scale = 0.0
    elif same_as_inventory and (
        share_ratio >= INVENTORY_SOFT_SHARE_RATIO or capital_ratio >= INVENTORY_SOFT_CAPITAL_RATIO
    ):
        guard = "SOFT_HALF_SIZE"
        size_scale = INVENTORY_SOFT_SIZE_SCALE
    elif side in {"UP", "DOWN"} and maker_side in {"UP", "DOWN"} and side != maker_side:
        guard = "OPPOSITE_FLOW_REDUCES_INVENTORY"

    reason = "MAKER_FLOW_MICRO_CONFIRMED"
    usable, usable_reason = snapshot_is_usable(snapshot, market_id=expected_market_id, now_ms=now_ms)
    if not usable:
        reason = usable_reason
    elif seconds_left is None or seconds_left <= PRIMARY_MIN_SECONDS_LEFT:
        reason = "PRIMARY_TOO_LATE"
    elif side not in {"UP", "DOWN"}:
        reason = "NO_STRICT_PRIOR_MAKER_FLOW"
    elif not confirmation["confirmed"]:
        reason = "NO_PUBLIC_MICRO_CONFIRMATION"
    elif guard == "HARD_SKIP_SAME_DIRECTION":
        reason = guard
    elif last_trade_ms is not None and now_ms - last_trade_ms < PRIMARY_COOLDOWN_MS:
        reason = "PRIMARY_COOLDOWN"
    else:
        ask = _number(snapshot.get(f"predict_{str(side).lower()}_ask"))
        if ask is None or not 0 < ask <= PRIMARY_MAX_ASK:
            reason = "PRIMARY_ASK_UNEXECUTABLE"

    return {
        "decision": "TRADE" if reason == "MAKER_FLOW_MICRO_CONFIRMED" else "SKIP",
        "reason": reason,
        "side": side,
        "signalAtMs": sampled_at_ms or None,
        "secondsLeft": seconds_left,
        "flow": flow,
        "microstructure": confirmation,
        "inventoryGuard": guard,
        "sizeScale": size_scale,
    }


def primary_execution(side: str, snapshot: dict[str, Any], size_scale: float) -> dict[str, float] | None:
    ask = _number(snapshot.get(f"predict_{side.lower()}_ask"))
    if ask is None or not 0 < ask <= PRIMARY_MAX_ASK:
        return None
    principal = max(PRIMARY_MIN_PRINCIPAL_USDT, PRIMARY_BASE_PRINCIPAL_USDT * float(size_scale))
    fee = principal * PRIMARY_FEE_RATE
    return {
        "ask": ask,
        "shares": principal / ask,
        "principalUsdt": principal,
        "feeUsdt": fee,
        "totalCostUsdt": principal + fee,
    }


def tail_decision(
    snapshot: dict[str, Any],
    inventory: dict[str, Any],
    *,
    expected_market_id: int,
    now_ms: int,
    last_trade_ms: int | None,
    tail_parents: int,
) -> dict[str, Any]:
    seconds_left = _number(snapshot.get("seconds_left"))
    main_side = inventory.get("mainCapitalSide")
    hedge_side = "DOWN" if main_side == "UP" else "UP" if main_side == "DOWN" else None
    primary_principal = float(inventory.get("primaryPrincipalUsdt") or 0.0)
    spent = float(inventory.get("tailPrincipalUsdt") or 0.0)
    budget = max(TAIL_MINIMUM_BUDGET_USDT, primary_principal * TAIL_BUDGET_RATE) if primary_principal > 0 else 0.0
    ask = _number(snapshot.get(f"predict_{str(hedge_side).lower()}_ask")) if hedge_side else None

    reason = "TAIL_INSURANCE_MIN1"
    usable, usable_reason = snapshot_is_usable(snapshot, market_id=expected_market_id, now_ms=now_ms)
    if not usable:
        reason = usable_reason
    elif seconds_left is None or not 0 < seconds_left <= TAIL_SECONDS:
        reason = "OUTSIDE_TAIL_WINDOW"
    elif hedge_side is None or primary_principal <= 0:
        reason = "NO_PRIMARY_CAPITAL_CONVICTION"
    elif ask is None or not 0 < ask <= TAIL_MAX_ASK:
        reason = "TAIL_ASK_ABOVE_012"
    elif tail_parents >= TAIL_MAX_PARENTS:
        reason = "TAIL_PARENT_LIMIT"
    elif last_trade_ms is not None and now_ms - last_trade_ms < TAIL_COOLDOWN_MS:
        reason = "TAIL_COOLDOWN"
    elif spent + TAIL_PRINCIPAL_USDT > budget + 1e-9:
        reason = "TAIL_BUDGET_EXHAUSTED"

    return {
        "decision": "TRADE" if reason == "TAIL_INSURANCE_MIN1" else "SKIP",
        "reason": reason,
        "side": hedge_side,
        "mainSide": main_side,
        "secondsLeft": seconds_left,
        "ask": ask,
        "budgetUsdt": budget,
        "spentPrincipalUsdt": spent,
        "availablePrincipalUsdt": max(0.0, budget - spent),
        "effectiveBudgetRate": budget / primary_principal if primary_principal else None,
    }


def tail_execution(side: str, snapshot: dict[str, Any]) -> dict[str, float] | None:
    ask = _number(snapshot.get(f"predict_{side.lower()}_ask"))
    if ask is None or not 0 < ask <= TAIL_MAX_ASK:
        return None
    principal = TAIL_PRINCIPAL_USDT
    fee = principal * PRIMARY_FEE_RATE
    return {
        "ask": ask,
        "shares": principal / ask,
        "principalUsdt": principal,
        "feeUsdt": fee,
        "totalCostUsdt": principal + fee,
    }


def reserved_capital(orders: list[dict[str, Any]]) -> float:
    return sum(float(order.get("price") or 0.0) * float(order.get("shares") or 0.0) for order in orders)


def policy(variant: dict[str, Any]) -> dict[str, Any]:
    return {
        "cohort": variant["cohort"],
        "paperOnly": True,
        "forwardOnly": True,
        "targetEventsUsed": False,
        "maker": {
            "globalPriceRange": [MIN_PRICE, MAX_PRICE],
            "actualOpeningRange": "0.06 through each side's observed best bid; never cross observed ask",
            "grid": GRID,
            "sharesPerOrder": SHARES_PER_ORDER,
            "refillMode": variant["refillMode"],
            "innerRefillLevels": INNER_REFILL_LEVELS if variant["refillMode"] == "INNER_7_UNTIL_30S" else None,
            "refillStopSecondsLeft": REFILL_STOP_SECONDS_LEFT,
            "openingMinimumSecondsLeft": OPENING_MIN_SECONDS_LEFT,
            "fillProxy": "full 18-share fill after a bid rested >=250ms and a later observed ask touches it",
        },
        "makerFlowAlpha": {
            "strictPriorWindowMs": [FLOW_MIN_AGE_MS, FLOW_MAX_AGE_MS],
            "minimumAbsoluteShares": FLOW_MIN_ABS_SHARES,
            "publicMicroMinimumSupportVotes": MICRO_MIN_SUPPORT_VOTES,
            "basePrincipalUsdt": PRIMARY_BASE_PRINCIPAL_USDT,
            "feeBps": PRIMARY_FEE_RATE * 10_000,
            "cooldownMs": PRIMARY_COOLDOWN_MS,
        },
        "inventoryGuard": {
            "softShareRatio": INVENTORY_SOFT_SHARE_RATIO,
            "hardShareRatio": INVENTORY_HARD_SHARE_RATIO,
            "hardAbsoluteShares": INVENTORY_HARD_ABS_SHARES,
            "softCapitalRatio": INVENTORY_SOFT_CAPITAL_RATIO,
            "hardCapitalRatio": INVENTORY_HARD_CAPITAL_RATIO,
            "softSizeScale": INVENTORY_SOFT_SIZE_SCALE,
        },
        "tailInsurance": {
            "secondsLeftMaximum": TAIL_SECONDS,
            "oppositeAskMaximum": TAIL_MAX_ASK,
            "principalPerParentUsdt": TAIL_PRINCIPAL_USDT,
            "budgetRateOfPrimaryTakerPrincipal": TAIL_BUDGET_RATE,
            "minimumBudgetUsdt": TAIL_MINIMUM_BUDGET_USDT,
            "cooldownMs": TAIL_COOLDOWN_MS,
            "maximumParents": TAIL_MAX_PARENTS,
        },
    }
