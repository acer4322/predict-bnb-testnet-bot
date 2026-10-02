from __future__ import annotations

import math
from typing import Any


COHORT = "TARGET_MAKER_RULES_GRID18_SOFTPOOL_V1"
GRID = 0.01
SHARES_PER_ORDER = 18.0
MIN_PRICE = 0.06
MAX_PRICE = 0.94
MIN_ORDER_NOTIONAL_USDT = 1.0
MAX_PAIR_PRICE_SUM = 0.99
OPENING_MIN_SECONDS_LEFT = 270.0
REFILL_STOP_SECONDS_LEFT = 30.0
MIN_REST_MS = 250
REFILL_COOLDOWN_MS = 1_000
ACTIVE_BAND_LEVELS = 28
SOFT_IMBALANCE_RATIO = 0.10
HARD_IMBALANCE_RATIO = 0.20
MAX_SAMPLE_AGE_MS = 1_000
MAX_PREDICT_AGE_MS = 2_000


def number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def floor_tick(value: Any) -> int | None:
    parsed = number(value)
    if parsed is None:
        return None
    return math.floor((parsed + 1e-9) / GRID)


def snapshot_is_usable(snapshot: dict[str, Any], *, market_id: int, now_ms: int) -> tuple[bool, str]:
    if int(number(snapshot.get("market_id")) or 0) != int(market_id):
        return False, "MARKET_MISMATCH"
    sampled_at_ms = int(number(snapshot.get("sampled_at_ms")) or 0)
    if sampled_at_ms <= 0 or now_ms - sampled_at_ms > MAX_SAMPLE_AGE_MS:
        return False, "STALE_PUBLIC_SNAPSHOT"
    predict_age_ms = number(snapshot.get("predict_receipt_age_ms"))
    if predict_age_ms is None or predict_age_ms > MAX_PREDICT_AGE_MS:
        return False, "STALE_PREDICT_BOOK"
    seconds_left = number(snapshot.get("seconds_left"))
    if seconds_left is None or seconds_left <= 0:
        return False, "MARKET_ENDED"
    return True, "OK"


def side_top_tick(snapshot: dict[str, Any], side: str) -> int | None:
    key = side.lower()
    bid_tick = floor_tick(snapshot.get(f"predict_{key}_bid"))
    ask = number(snapshot.get(f"predict_{key}_ask"))
    if bid_tick is None or ask is None:
        return None
    ask_safe_tick = floor_tick(ask - GRID)
    if ask_safe_tick is None:
        return None
    return min(round(MAX_PRICE / GRID), bid_tick, ask_safe_tick)


def opening_orders(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    minimum_tick = round(MIN_PRICE / GRID)
    for side in ("UP", "DOWN"):
        top_tick = side_top_tick(snapshot, side)
        if top_tick is None:
            continue
        for price_tick in range(minimum_tick, top_tick + 1):
            price = round(price_tick * GRID, 2)
            if price * SHARES_PER_ORDER + 1e-9 < MIN_ORDER_NOTIONAL_USDT:
                continue
            result.append({
                "side": side,
                "priceTick": price_tick,
                "price": price,
                "shares": SHARES_PER_ORDER,
                "origin": "OPENING_RAIL",
            })
    return result


def inventory(up_shares: float, down_shares: float, up_cost: float, down_cost: float) -> dict[str, Any]:
    total = max(0.0, up_shares) + max(0.0, down_shares)
    delta = up_shares - down_shares
    ratio = abs(delta) / total if total else 0.0
    paired = 2 * min(up_shares, down_shares) / total if total else None
    return {
        "upShares": up_shares,
        "downShares": down_shares,
        "upCostUsdt": up_cost,
        "downCostUsdt": down_cost,
        "deltaShares": delta,
        "residualSide": "UP" if delta > 1e-9 else "DOWN" if delta < -1e-9 else None,
        "imbalanceRatio": ratio,
        "pairedCoverage": paired,
    }


def may_quote_side(side: str, inventory_state: dict[str, Any]) -> tuple[bool, str]:
    residual = inventory_state.get("residualSide")
    ratio = float(inventory_state.get("imbalanceRatio") or 0.0)
    if side == residual and ratio >= HARD_IMBALANCE_RATIO:
        return False, "HARD_POOLED_INVENTORY_GUARD"
    if side == residual and ratio >= SOFT_IMBALANCE_RATIO:
        return False, "SOFT_POOLED_INVENTORY_GUARD"
    return True, "POOLED_INVENTORY_OK"


def active_band_orders(snapshot: dict[str, Any], inventory_state: dict[str, Any]) -> list[dict[str, Any]]:
    seconds_left = number(snapshot.get("seconds_left"))
    if seconds_left is None or seconds_left <= REFILL_STOP_SECONDS_LEFT:
        return []
    minimum_tick = round(MIN_PRICE / GRID)
    result: list[dict[str, Any]] = []
    for side in ("UP", "DOWN"):
        allowed, _ = may_quote_side(side, inventory_state)
        if not allowed:
            continue
        top_tick = side_top_tick(snapshot, side)
        opposite_bid = number(snapshot.get(f"predict_{'down' if side == 'UP' else 'up'}_bid"))
        if top_tick is None or opposite_bid is None:
            continue
        bottom_tick = max(minimum_tick, top_tick - ACTIVE_BAND_LEVELS + 1)
        for price_tick in range(bottom_tick, top_tick + 1):
            price = round(price_tick * GRID, 2)
            if price * SHARES_PER_ORDER + 1e-9 < MIN_ORDER_NOTIONAL_USDT:
                continue
            if price + opposite_bid > MAX_PAIR_PRICE_SUM + 1e-9:
                continue
            result.append({
                "side": side,
                "priceTick": price_tick,
                "price": price,
                "shares": SHARES_PER_ORDER,
                "origin": "ACTIVE_BAND",
            })
    return result


def ask_touch_fill(order: dict[str, Any], snapshot: dict[str, Any], *, now_ms: int) -> bool:
    if now_ms - int(order.get("placedAtMs") or now_ms) < MIN_REST_MS:
        return False
    side = str(order.get("side") or "").lower()
    ask = number(snapshot.get(f"predict_{side}_ask"))
    price = number(order.get("price"))
    return ask is not None and price is not None and ask <= price + 1e-9


def policy() -> dict[str, Any]:
    return {
        "cohort": COHORT,
        "paperOnly": True,
        "forwardOnly": True,
        "historicalBackfill": False,
        "targetEventsDriveStrategy": False,
        "liveOrdersAffected": False,
        "grid": GRID,
        "sharesPerOrder": SHARES_PER_ORDER,
        "openingRails": [MIN_PRICE, MAX_PRICE],
        "minimumOrderNotionalUsdt": MIN_ORDER_NOTIONAL_USDT,
        "maximumPairPriceSum": MAX_PAIR_PRICE_SUM,
        "activeBandLevelsPerSide": ACTIVE_BAND_LEVELS,
        "softInventoryRatio": SOFT_IMBALANCE_RATIO,
        "hardInventoryRatio": HARD_IMBALANCE_RATIO,
        "refillCooldownMs": REFILL_COOLDOWN_MS,
        "stopNewAndReplacementAtSecondsLeft": REFILL_STOP_SECONDS_LEFT,
        "openingMinimumSecondsLeft": OPENING_MIN_SECONDS_LEFT,
        "fillProxy": "resting paper BID fills only after a later fresh observed ask touches it",
        "rebates": "not credited",
        "evidence": {
            "priceGrid": "100% integer-cent retained target Maker fills",
            "unit": "18-share parent mode and median",
            "breadth": "28 unique filled levels per market-side median, 48 p90",
            "refill": "same-price target replacement frequently re-fills within 1-5 seconds",
            "inventory": "about 90.8% final paired coverage with soft, not event-by-event, balancing",
            "cutoff": "last-30-second parent activity fell about 40.8%",
        },
    }
