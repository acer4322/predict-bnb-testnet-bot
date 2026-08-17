from __future__ import annotations

import math
from typing import Any, Iterable


COHORT = "TARGET_MAKER_RULES_GRID18_SOFTPOOL_V2"
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
RECENTER_MIN_INTERVAL_MS = 3_000
ACTIVE_BAND_LEVELS = 5
PAIR_FIRST_DELTA_SHARES = SHARES_PER_ORDER
TOXIC_HOLD_MS = 4_000
TOXIC_MIN_VOTES = 3
TOXIC_MIN_VOTE_MARGIN = 2
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


def inventory(up_shares: float, down_shares: float, up_cost: float, down_cost: float) -> dict[str, Any]:
    up = max(0.0, float(up_shares))
    down = max(0.0, float(down_shares))
    total = up + down
    delta = up - down
    ratio = abs(delta) / total if total else 0.0
    paired = 2 * min(up, down) / total if total else None
    return {
        "upShares": up,
        "downShares": down,
        "upCostUsdt": float(up_cost),
        "downCostUsdt": float(down_cost),
        "deltaShares": delta,
        "residualSide": "UP" if delta > 1e-9 else "DOWN" if delta < -1e-9 else None,
        "imbalanceRatio": ratio,
        "pairedCoverage": paired,
    }


def _vote(value: Any, threshold: float) -> str | None:
    parsed = number(value)
    if parsed is None or abs(parsed) < threshold:
        return None
    return "UP" if parsed > 0 else "DOWN"


def toxic_flow(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Detect directional public flow that makes bids on the opposite token toxic.

    Positive BTC/public pressure implies the DOWN token is the falling knife; negative
    pressure implies the UP token is the falling knife. This is deliberately a
    conservative multi-vote guard, not a directional trading signal.
    """

    votes = {
        "directionScore": _vote(snapshot.get("direction_score"), 0.25),
        "futuresReturn3s": _vote(snapshot.get("futures_return_3s_bps"), 1.5),
        "spotReturn3s": _vote(snapshot.get("spot_return_3s_bps"), 1.5),
        "futuresTaker1s": _vote(snapshot.get("futures_taker_imbalance_1s"), 0.60),
        "spotTaker1s": _vote(snapshot.get("spot_taker_imbalance_1s"), 0.60),
        "futuresQueue": _vote(snapshot.get("futures_queue_imbalance"), 0.40),
        "spotQueue": _vote(snapshot.get("spot_queue_imbalance"), 0.40),
    }
    up = sum(side == "UP" for side in votes.values())
    down = sum(side == "DOWN" for side in votes.values())
    pressure = None
    if up >= TOXIC_MIN_VOTES and up - down >= TOXIC_MIN_VOTE_MARGIN:
        pressure = "UP"
    elif down >= TOXIC_MIN_VOTES and down - up >= TOXIC_MIN_VOTE_MARGIN:
        pressure = "DOWN"
    cancel_side = "DOWN" if pressure == "UP" else "UP" if pressure == "DOWN" else None
    return {
        "toxic": pressure is not None,
        "pressureSide": pressure,
        "cancelSide": cancel_side,
        "upVotes": up,
        "downVotes": down,
        "votes": votes,
    }


def quote_levels_by_side(inventory_state: dict[str, Any]) -> dict[str, int]:
    delta = float(inventory_state.get("deltaShares") or 0.0)
    if abs(delta) < PAIR_FIRST_DELTA_SHARES - 1e-9:
        return {"UP": ACTIVE_BAND_LEVELS, "DOWN": ACTIVE_BAND_LEVELS}
    repair_levels = max(1, min(ACTIVE_BAND_LEVELS, int(math.ceil(abs(delta) / SHARES_PER_ORDER - 1e-9))))
    if delta > 0:
        return {"UP": 0, "DOWN": repair_levels}
    return {"UP": repair_levels, "DOWN": 0}


def desired_orders(
    snapshot: dict[str, Any],
    inventory_state: dict[str, Any],
    *,
    blocked_sides: Iterable[str] = (),
) -> list[dict[str, Any]]:
    seconds_left = number(snapshot.get("seconds_left"))
    if seconds_left is None or seconds_left <= REFILL_STOP_SECONDS_LEFT:
        return []
    blocked = {str(side).upper() for side in blocked_sides}
    levels_by_side = quote_levels_by_side(inventory_state)
    minimum_tick = round(MIN_PRICE / GRID)
    result: list[dict[str, Any]] = []
    for side in ("UP", "DOWN"):
        levels = int(levels_by_side[side])
        if levels <= 0 or side in blocked:
            continue
        top_tick = side_top_tick(snapshot, side)
        opposite_bid = number(snapshot.get(f"predict_{'down' if side == 'UP' else 'up'}_bid"))
        if top_tick is None or opposite_bid is None:
            continue
        bottom_tick = max(minimum_tick, top_tick - levels + 1)
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
                "origin": "PAIR_FIRST_ACTIVE_BAND",
            })
    return result


def opening_orders(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return desired_orders(snapshot, inventory(0.0, 0.0, 0.0, 0.0))


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
        "controlCohort": "TARGET_MAKER_RULES_GRID18_SOFTPOOL_V1",
        "grid": GRID,
        "sharesPerOrder": SHARES_PER_ORDER,
        "minimumOrderNotionalUsdt": MIN_ORDER_NOTIONAL_USDT,
        "maximumPairPriceSum": MAX_PAIR_PRICE_SUM,
        "activeBandLevelsPerSide": ACTIVE_BAND_LEVELS,
        "openingMode": "near-touch active band only; cumulative unique prices may expand only through causal recentering",
        "pairFirstDeltaShares": PAIR_FIRST_DELTA_SHARES,
        "refillCooldownMs": REFILL_COOLDOWN_MS,
        "recenterMinimumIntervalMs": RECENTER_MIN_INTERVAL_MS,
        "toxicFlowHoldMs": TOXIC_HOLD_MS,
        "toxicFlowMinimumVotes": TOXIC_MIN_VOTES,
        "toxicFlowMinimumVoteMargin": TOXIC_MIN_VOTE_MARGIN,
        "stopNewAndReplacementAtSecondsLeft": REFILL_STOP_SECONDS_LEFT,
        "openingMinimumSecondsLeft": OPENING_MIN_SECONDS_LEFT,
        "fillProxy": "same strict later-ask-touch proxy as V1 so the A/B isolates quoting, cancellation and inventory control",
        "rebates": "not credited",
        "correctionsVsV1": {
            "concurrentDepth": "28-wide inferred filled-level breadth is no longer treated as 28 simultaneous resting levels",
            "pairFirst": "after a one-lot-or-larger residual appears, stop adding the residual side and quote only the deficient side until paired",
            "toxicCancel": "cancel bids on the token being pressured lower when at least three independent public-flow votes agree with margin >=2",
            "recenter": "cancel stale out-of-band resting quotes and rebuild a five-level near-touch band no faster than every 3 seconds",
        },
        "successCriteria": {
            "pairedCoverageMedian": ">=0.85",
            "adverseResidualPattern": "winner-opposite residual rate materially below V1",
            "netRoi": "materially above V1 under the identical ask-touch fill proxy",
        },
    }
