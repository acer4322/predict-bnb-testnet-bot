from __future__ import annotations

import math
from typing import Any


COHORTS = (
    {"cohort": "MAKER_GRID18_SMALL_3", "label": "少層", "levels": 3},
    {"cohort": "MAKER_GRID18_MEDIUM_7", "label": "中層", "levels": 7},
    {"cohort": "MAKER_GRID18_LARGE_15", "label": "大層", "levels": 15},
)
GRID = 0.01
SHARES = 18.0
MIN_NOTIONAL_USDT = 1.0
MAX_PAIR_PRICE_SUM = 0.99
MIN_SECONDS_LEFT = 30.0
MIN_REST_MS = 250
MIN_RECENTER_INTERVAL_MS = 5_000
RECENTER_TICKS = 2
REFILL_COOLDOWN_MS = 1_000
MAX_SAMPLE_AGE_MS = 1_000
MAX_PREDICT_AGE_MS = 2_000


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _floor_cent(value: Any) -> float | None:
    price = _number(value)
    if price is None or price <= 0 or price >= 1:
        return None
    return round(math.floor((price + 1e-9) * 100) / 100, 2)


def snapshot_is_usable(snapshot: dict[str, Any], *, market_id: int, now_ms: int) -> tuple[bool, str]:
    if int(snapshot.get("market_id") or 0) != int(market_id):
        return False, "MARKET_MISMATCH"
    sampled_at = int(snapshot.get("sampled_at_ms") or 0)
    if sampled_at <= 0 or now_ms - sampled_at > MAX_SAMPLE_AGE_MS:
        return False, "STALE_PUBLIC_SNAPSHOT"
    predict_age = _number(snapshot.get("predict_receipt_age_ms"))
    if predict_age is None or predict_age > MAX_PREDICT_AGE_MS:
        return False, "STALE_PREDICT_BOOK"
    seconds_left = _number(snapshot.get("seconds_left"))
    if seconds_left is None:
        return False, "NO_MARKET_CLOCK"
    if seconds_left <= MIN_SECONDS_LEFT:
        return False, "AFTER_MAKER_ACTIVE_WINDOW"
    return True, "OK"


def anchors(snapshot: dict[str, Any]) -> tuple[float, float] | None:
    up = _floor_cent(snapshot.get("predict_up_bid"))
    down = _floor_cent(snapshot.get("predict_down_bid"))
    up_ask = _number(snapshot.get("predict_up_ask"))
    down_ask = _number(snapshot.get("predict_down_ask"))
    if None in (up, down, up_ask, down_ask):
        return None
    # Never create a paper order that would already cross the observed ask.
    up = min(up, round(float(up_ask) - GRID, 2))
    down = min(down, round(float(down_ask) - GRID, 2))
    overflow_cents = max(0, math.ceil(((up + down) - MAX_PAIR_PRICE_SUM - 1e-9) * 100))
    while overflow_cents > 0:
        if up >= down:
            up = round(up - GRID, 2)
        else:
            down = round(down - GRID, 2)
        overflow_cents -= 1
    if up <= 0 or down <= 0:
        return None
    return up, down


def desired_grid(snapshot: dict[str, Any], levels: int) -> list[dict[str, Any]]:
    pair = anchors(snapshot)
    if pair is None:
        return []
    result: list[dict[str, Any]] = []
    up_anchor, down_anchor = pair
    for level in range(int(levels)):
        up = round(up_anchor - level * GRID, 2)
        down = round(down_anchor - level * GRID, 2)
        # The venue's 1 USDT minimum applies to each order. Keeping pairs atomic
        # avoids fabricating a one-sided deep tail that the requested ladder did not fund.
        if min(up, down) <= 0 or min(up, down) * SHARES + 1e-9 < MIN_NOTIONAL_USDT:
            continue
        if up + down > MAX_PAIR_PRICE_SUM + 1e-9:
            continue
        result.extend((
            {"side": "UP", "level": level, "price": up, "shares": SHARES},
            {"side": "DOWN", "level": level, "price": down, "shares": SHARES},
        ))
    return result


def should_recenter(
    previous_anchors: tuple[float, float] | None,
    current_anchors: tuple[float, float] | None,
    *,
    last_recenter_ms: int | None,
    now_ms: int,
) -> bool:
    if previous_anchors is None:
        return True
    if current_anchors is None or last_recenter_ms is None:
        return False
    if now_ms - last_recenter_ms < MIN_RECENTER_INTERVAL_MS:
        return False
    return max(abs(current_anchors[0] - previous_anchors[0]), abs(current_anchors[1] - previous_anchors[1])) >= RECENTER_TICKS * GRID - 1e-9


def ask_touch_fill(order: dict[str, Any], snapshot: dict[str, Any], *, now_ms: int) -> bool:
    if str(order.get("status")) != "ACTIVE":
        return False
    if now_ms - int(order.get("placed_at_ms") or now_ms) < MIN_REST_MS:
        return False
    side = str(order.get("side") or "").lower()
    ask = _number(snapshot.get(f"predict_{side}_ask"))
    price = _number(order.get("price"))
    return ask is not None and price is not None and ask <= price + 1e-9


def policy(levels: int) -> dict[str, Any]:
    return {
        "levelsPerSide": int(levels),
        "sharesPerOrder": SHARES,
        "grid": GRID,
        "minimumOrderNotionalUsdt": MIN_NOTIONAL_USDT,
        "maximumPairPriceSum": MAX_PAIR_PRICE_SUM,
        "stopNewQuotesAtSecondsLeft": MIN_SECONDS_LEFT,
        "minimumRestMsBeforeFill": MIN_REST_MS,
        "recenterMinimumIntervalMs": MIN_RECENTER_INTERVAL_MS,
        "recenterTicks": RECENTER_TICKS,
        "refillCooldownMs": REFILL_COOLDOWN_MS,
        "fillProxy": "full 18-share fill only after a previously resting bid is touched/crossed by a later observed ask",
        "fees": "observed target Maker fee is zero; rebates are not credited",
    }
