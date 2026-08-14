from __future__ import annotations

import math
from typing import Any, Iterable


TAKER_FEE_RATE = 0.02
MAX_BOOK_AGE_MS = 2_000
HORIZONS_MS = (0, 250, 500, 1_000, 2_000)
EXECUTION_PROFILES = (
    {"profile": "MIN1_USDT", "kind": "BUDGET_USDT", "amount": 1.0},
    {"profile": "FIXED5_USDT", "kind": "BUDGET_USDT", "amount": 5.0},
    {"profile": "FIXED18_SHARES", "kind": "SHARES", "amount": 18.0},
    {"profile": "TARGET_OBSERVED_SHARES", "kind": "TARGET_SHARES", "amount": None},
)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def executable_levels(orderbook: dict[str, Any], side: str) -> list[tuple[float, float]]:
    data = orderbook.get("data") if isinstance(orderbook.get("data"), dict) else orderbook
    raw = data.get("asks") if side == "UP" else data.get("bids")
    levels: list[tuple[float, float]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        yes_price = _number(item[0])
        shares = _number(item[1])
        if yes_price is None or shares is None or shares <= 0:
            continue
        price = yes_price if side == "UP" else 1.0 - yes_price
        if 0 < price < 1:
            levels.append((round(price, 10), shares))
    levels.sort(key=lambda item: item[0])
    return levels


def fill_by_shares(levels: Iterable[tuple[float, float]], requested_shares: float) -> dict[str, Any]:
    requested = max(0.0, float(requested_shares))
    remaining = requested
    filled = principal = 0.0
    consumed = 0
    best_ask = None
    worst_ask = None
    for price, available in levels:
        if best_ask is None:
            best_ask = price
        take = min(remaining, max(0.0, float(available)))
        if take <= 0:
            continue
        filled += take
        principal += take * price
        remaining -= take
        consumed += 1
        worst_ask = price
        if remaining <= 1e-9:
            break
    fee = principal * TAKER_FEE_RATE
    return {
        "requestedKind": "SHARES",
        "requestedAmount": requested,
        "filledShares": filled,
        "principalUsdt": principal,
        "feeUsdt": fee,
        "totalCostUsdt": principal + fee,
        "vwap": principal / filled if filled else None,
        "bestAsk": best_ask,
        "worstAsk": worst_ask,
        "levelsConsumed": consumed,
        "fullyExecutable": filled + 1e-9 >= requested and principal + 1e-9 >= 1.0,
        "minimumNotionalMet": principal + 1e-9 >= 1.0,
    }


def fill_by_budget(levels: Iterable[tuple[float, float]], budget_usdt: float) -> dict[str, Any]:
    requested = max(0.0, float(budget_usdt))
    remaining = requested
    filled = principal = 0.0
    consumed = 0
    best_ask = None
    worst_ask = None
    for price, available in levels:
        if best_ask is None:
            best_ask = price
        capacity = price * max(0.0, float(available))
        spend = min(remaining, capacity)
        if spend <= 0:
            continue
        take = spend / price
        filled += take
        principal += spend
        remaining -= spend
        consumed += 1
        worst_ask = price
        if remaining <= 1e-9:
            break
    fee = principal * TAKER_FEE_RATE
    return {
        "requestedKind": "BUDGET_USDT",
        "requestedAmount": requested,
        "filledShares": filled,
        "principalUsdt": principal,
        "feeUsdt": fee,
        "totalCostUsdt": principal + fee,
        "vwap": principal / filled if filled else None,
        "bestAsk": best_ask,
        "worstAsk": worst_ask,
        "levelsConsumed": consumed,
        "fullyExecutable": principal + 1e-9 >= requested and requested + 1e-9 >= 1.0,
        "minimumNotionalMet": principal + 1e-9 >= 1.0,
    }


def simulate_profiles(orderbook: dict[str, Any], side: str, target_shares: float) -> list[dict[str, Any]]:
    levels = executable_levels(orderbook, side)
    result = []
    for profile in EXECUTION_PROFILES:
        kind = str(profile["kind"])
        amount = float(target_shares) if kind == "TARGET_SHARES" else float(profile["amount"] or 0.0)
        fill = fill_by_budget(levels, amount) if kind == "BUDGET_USDT" else fill_by_shares(levels, amount)
        result.append({"profile": profile["profile"], **fill})
    return result


def settled_economics(fill: dict[str, Any], *, side: str, winner: str, stress_ticks: int = 0) -> dict[str, Any]:
    shares = float(fill.get("filledShares") or 0.0)
    principal = float(fill.get("principalUsdt") or 0.0) + shares * 0.01 * max(0, int(stress_ticks))
    fee = principal * TAKER_FEE_RATE
    cost = principal + fee
    payout = shares if side == winner else 0.0
    pnl = payout - cost
    return {
        "payoutUsdt": payout,
        "costUsdt": cost,
        "pnlUsdt": pnl,
        "roi": pnl / cost if cost else None,
    }


def policy() -> dict[str, Any]:
    return {
        "cohort": "TARGET_TAKER_MIRROR_AUDIT_V1",
        "paperOnly": True,
        "forwardOnly": True,
        "liveOrdersAffected": False,
        "targetEventsDriveAutonomousStrategy": False,
        "purpose": "post-detection execution audit and strict pre-event mechanism research; not proof of the target's private logic",
        "horizonsMsAfterFirstDetection": list(HORIZONS_MS),
        "profiles": list(EXECUTION_PROFILES),
        "feeBps": TAKER_FEE_RATE * 10_000,
        "maximumOrderbookAgeMs": MAX_BOOK_AGE_MS,
        "downBookConversion": "buying DOWN consumes YES bids transformed to price 1-yesBid",
        "settlement": "official winner; payout equals filled winning shares",
        "stressTicks": [1, 2],
    }
