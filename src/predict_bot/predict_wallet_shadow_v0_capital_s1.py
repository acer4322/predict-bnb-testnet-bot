from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


COHORT = "V0_CAPITAL_S1"
CAP100_COHORT = "V0_CAPITAL_S1_CAP100_STRESS"
CAP100_USDT = 100.0
MIN1_EXEC_CAP100_COHORT = "MIN1_EXEC_CAP100"
MINIMUM_ORDER_NOTIONAL_USDT = 1.0
MIN1_WALLET_GROWTH_COHORT = "MIN1_WALLET_GROWTH"
MIN1_WALLET_GROWTH_TIME20_COHORT = "MIN1_WALLET_GROWTH_TIME20"
MIN1_BATCHED_MAKER_TAKER_RESERVE_COHORT = "MIN1_BATCHED_MAKER_TAKER_RESERVE"
INITIAL_WALLET_USDT = 100.0
MARKET_BUDGET_FRACTION = 0.20
SOURCE_MAKER_UNIT_SHARES = 18.0
RESEARCH_MAKER_UNIT_SHARES = 1.0
MAKER_BUDGET_FRACTION = 0.20
TAKER_BUDGET_FRACTION = 0.80
CORE_STABILITY_CONFIRMATIONS = 2
MARKET_DURATION_SECONDS = 300.0
TIME_TRANCHE_COUNT = 5


def planned_market_budget(available_cash_usdt: float) -> float:
    available = max(0.0, float(available_cash_usdt))
    if available < MINIMUM_ORDER_NOTIONAL_USDT:
        return 0.0
    return min(available, max(MINIMUM_ORDER_NOTIONAL_USDT, available * MARKET_BUDGET_FRACTION))


def time20_unlocked_fraction(seconds_left: float | None) -> float:
    """Unlock one cumulative 20% tranche during each elapsed market minute."""
    if seconds_left is None:
        return 0.0
    elapsed = max(0.0, MARKET_DURATION_SECONDS - float(seconds_left))
    tranche = min(TIME_TRANCHE_COUNT, max(1, int(elapsed // 60.0) + 1))
    return tranche / TIME_TRANCHE_COUNT
SHARE_SCALE = 1.0 / 18.0
TAKER_SOURCE_CAP_SHARES = 36.0
MAX_TAKER_SIDE_SWITCHES = 1
TAKER_FEE_BPS = 200.0


@dataclass(frozen=True)
class CapitalS1State:
    last_taker_side: str | None = None
    taker_side_switches: int = 0
    taker_blocked: bool = False


def cap100_fill(
    *,
    used_capital_usdt: float,
    role: str,
    price: float,
    requested_shares: float,
) -> dict[str, Any]:
    """Causally shrink a fill so fee-inclusive market capital never exceeds $100."""
    remaining = max(0.0, CAP100_USDT - max(0.0, float(used_capital_usdt)))
    fee_rate = TAKER_FEE_BPS / 10_000.0 if str(role).upper() == "TAKER" else 0.0
    unit_capital = max(0.0, float(price)) * (1.0 + fee_rate)
    requested = max(0.0, float(requested_shares))
    if unit_capital <= 0.0:
        executed = requested
    else:
        executed = min(requested, remaining / unit_capital)
    principal = executed * max(0.0, float(price))
    fee = principal * fee_rate
    total = principal + fee
    return {
        "requestedShares": requested,
        "executedShares": executed,
        "principalCostUsdt": principal,
        "feeUsdt": fee,
        "capitalCostUsdt": total,
        "remainingBeforeUsdt": remaining,
        "remainingAfterUsdt": max(0.0, remaining - total),
        "truncated": executed + 1e-9 < requested,
        "blocked": executed <= 1e-12 and requested > 0.0,
    }


def min1_exec_cap100_fill(
    *,
    used_capital_usdt: float,
    role: str,
    price: float,
    desired_shares: float,
    capital_limit_usdt: float = CAP100_USDT,
) -> dict[str, Any]:
    """Apply the $1 principal floor and a caller-supplied fee-inclusive capital limit."""
    px = max(0.0, float(price))
    desired = max(0.0, float(desired_shares))
    remaining = max(0.0, float(capital_limit_usdt) - max(0.0, float(used_capital_usdt)))
    fee_rate = TAKER_FEE_BPS / 10_000.0 if str(role).upper() == "TAKER" else 0.0
    if px <= 0.0:
        return {
            "desiredShares": desired,
            "minimumShares": None,
            "requestedShares": 0.0,
            "executedShares": 0.0,
            "principalCostUsdt": 0.0,
            "feeUsdt": 0.0,
            "capitalCostUsdt": 0.0,
            "remainingBeforeUsdt": remaining,
            "remainingAfterUsdt": remaining,
            "minimumUplift": False,
            "capTruncated": False,
            "blocked": True,
            "reason": "INVALID_PRICE",
        }
    minimum_shares = MINIMUM_ORDER_NOTIONAL_USDT / px
    requested = max(desired, minimum_shares)
    unit_capital = px * (1.0 + fee_rate)
    full_capital = requested * unit_capital
    if full_capital <= remaining + 1e-9:
        executed = requested
        reason = "FULL_EXECUTION"
    else:
        affordable = remaining / unit_capital if unit_capital > 0.0 else 0.0
        if affordable * px + 1e-9 >= MINIMUM_ORDER_NOTIONAL_USDT:
            executed = affordable
            reason = "CAP_TRUNCATED_ABOVE_MINIMUM"
        else:
            executed = 0.0
            reason = "INSUFFICIENT_CAPITAL_FOR_MINIMUM_ORDER"
    principal = executed * px
    fee = principal * fee_rate
    capital = principal + fee
    return {
        "desiredShares": desired,
        "minimumShares": minimum_shares,
        "requestedShares": requested,
        "executedShares": executed,
        "principalCostUsdt": principal,
        "feeUsdt": fee,
        "capitalCostUsdt": capital,
        "remainingBeforeUsdt": remaining,
        "remainingAfterUsdt": max(0.0, remaining - capital),
        "minimumUplift": requested > desired + 1e-9,
        "capTruncated": executed > 0.0 and executed + 1e-9 < requested,
        "blocked": executed <= 1e-12,
        "reason": reason,
    }


def reduce_source_event(
    state: CapitalS1State,
    *,
    event_type: str,
    side: str,
    original_shares: float,
) -> tuple[CapitalS1State, dict[str, Any]]:
    """Apply the locked causal S1 sizing/churn policy to one new V0 event."""
    if event_type == "MAKER_FILL_PROXY":
        return state, {
            "accepted": True,
            "scaledShares": max(0.0, float(original_shares)) * SHARE_SCALE,
            "reason": "MAKER_SCALE_18_TO_1",
        }

    if event_type != "TAKER_INTENT":
        return state, {"accepted": False, "scaledShares": 0.0, "reason": "NOT_A_FILL_EVENT"}

    if state.taker_blocked:
        return state, {"accepted": False, "scaledShares": 0.0, "reason": "TAKER_BLOCKED_AFTER_SECOND_SWITCH"}

    switches = state.taker_side_switches
    if state.last_taker_side in {"UP", "DOWN"} and side != state.last_taker_side:
        switches += 1
    if switches > MAX_TAKER_SIDE_SWITCHES:
        next_state = replace(state, taker_side_switches=switches, taker_blocked=True)
        return next_state, {"accepted": False, "scaledShares": 0.0, "reason": "SECOND_TAKER_SIDE_SWITCH"}

    next_state = CapitalS1State(
        last_taker_side=side,
        taker_side_switches=switches,
        taker_blocked=False,
    )
    return next_state, {
        "accepted": True,
        "scaledShares": min(max(0.0, float(original_shares)), TAKER_SOURCE_CAP_SHARES) * SHARE_SCALE,
        "reason": "TAKER_CAP_36_SCALE_18_TO_1",
    }


def market_economics(events: list[dict[str, Any]], winner: str) -> dict[str, Any]:
    fills: list[dict[str, Any]] = []
    for event in events:
        if not bool(event.get("accepted")):
            continue
        side = str(event.get("side") or "").upper()
        role = str(event.get("role") or "").upper()
        try:
            price = float(event.get("price"))
            shares = float(event.get("scaled_shares", event.get("scaledShares")))
        except (TypeError, ValueError):
            continue
        if side not in {"UP", "DOWN"} or role not in {"MAKER", "TAKER"}:
            continue
        if not (0.0 <= price <= 1.0) or shares <= 0:
            continue
        fills.append({"side": side, "role": role, "price": price, "shares": shares})

    payout = sum(item["shares"] for item in fills if item["side"] == winner)
    maker_cost = sum(item["price"] * item["shares"] for item in fills if item["role"] == "MAKER")
    taker_cost = sum(item["price"] * item["shares"] for item in fills if item["role"] == "TAKER")
    gross_cost = maker_cost + taker_cost
    gross_pnl = payout - gross_cost
    fee = taker_cost * TAKER_FEE_BPS / 10_000.0

    def stressed(ticks: int) -> tuple[float, float]:
        stressed_taker = sum(
            min(1.0, item["price"] + ticks * 0.01) * item["shares"]
            for item in fills
            if item["role"] == "TAKER"
        )
        cost = maker_cost + stressed_taker + fee
        return cost, payout - cost

    stress1_cost, stress1_pnl = stressed(1)
    stress2_cost, stress2_pnl = stressed(2)
    status = "NO_TRADE" if not fills else "WIN" if gross_pnl > 1e-9 else "LOSS" if gross_pnl < -1e-9 else "FLAT"
    return {
        "traded": bool(fills),
        "status": status,
        "fillCount": len(fills),
        "costUsdt": gross_cost,
        "payoutUsdt": payout,
        "grossPnlUsdt": gross_pnl,
        "grossRoi": gross_pnl / gross_cost if gross_cost else None,
        "makerCostUsdt": maker_cost,
        "takerCostUsdt": taker_cost,
        "takerFeeUsdt": fee,
        "feeNetPnlUsdt": gross_pnl - fee,
        "feeNetRoi": (gross_pnl - fee) / (gross_cost + fee) if gross_cost + fee else None,
        "stress1CostUsdt": stress1_cost,
        "stress1PnlUsdt": stress1_pnl,
        "stress1Roi": stress1_pnl / stress1_cost if stress1_cost else None,
        "stress2CostUsdt": stress2_cost,
        "stress2PnlUsdt": stress2_pnl,
        "stress2Roi": stress2_pnl / stress2_cost if stress2_cost else None,
    }
