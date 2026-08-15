from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_UP
from typing import Any

from . import xpair_btc_eth_canary as canary
from . import xpair_canary_autopilot_server as base
from . import xpair_canary_autopilot_server_v2 as v2

MIN_EXCHANGE_LEG_STAKE_USDT = Decimal("1.00")
MIN_PAIR_BUDGET_USDT = Decimal("2.00")
DEFAULT_PAIR_BUDGET_USDT = Decimal("3.00")

_original_validate = base.MonitorConfig.validate
_original_quote_equal_share_pair = v2.quote_equal_share_pair
_original_state_payload = base.state_payload


def _ceil_cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_UP)


def validate_monitor_config(self: base.MonitorConfig) -> None:
    _original_validate(self)
    if self.pair_budget_usdt < MIN_PAIR_BUDGET_USDT:
        raise ValueError(
            "pair budget must be at least 2.00 USDT because Binance requires "
            "at least 1.00 USDT on each leg"
        )


def quote_equal_share_pair_min_one(
    client: canary.BinancePredictionTradingClient,
    *,
    wallet_address: str,
    plan: canary.CanaryPlan,
    pair_budget: Decimal,
    max_total_cost_per_share: Decimal,
    slippage_bps: int,
) -> tuple[list[dict[str, Any]], Decimal]:
    smallest_leg = min(plan.legs, key=lambda item: item.requested_stake)
    if smallest_leg.requested_stake < MIN_EXCHANGE_LEG_STAKE_USDT:
        scale = MIN_EXCHANGE_LEG_STAKE_USDT / smallest_leg.requested_stake
        required_pair_budget = _ceil_cents(pair_budget * scale)
        raise ValueError(
            "MIN_ORDER_1_USDT: "
            f"{smallest_leg.symbol}_{smallest_leg.side} modeled stake "
            f"{smallest_leg.requested_stake:.6f} USDT is below Binance's "
            "1.00 USDT per-order threshold; equal-share sizing requires "
            f"pair_budget >= {required_pair_budget:.2f} USDT at current prices"
        )

    # The original requote can reduce one leg slightly. Enforce the real
    # exchange floor during the final equal-share requote as well, then restore
    # the legacy module value so paper sizing remains independent.
    previous_floor = canary.MIN_LEG_STAKE_USDT
    canary.MIN_LEG_STAKE_USDT = MIN_EXCHANGE_LEG_STAKE_USDT
    try:
        return _original_quote_equal_share_pair(
            client,
            wallet_address=wallet_address,
            plan=plan,
            pair_budget=pair_budget,
            max_total_cost_per_share=max_total_cost_per_share,
            slippage_bps=slippage_bps,
        )
    finally:
        canary.MIN_LEG_STAKE_USDT = previous_floor


def state_payload() -> dict[str, Any]:
    payload = _original_state_payload()
    payload["defaults"]["minimumLegStakeUsdt"] = float(
        MIN_EXCHANGE_LEG_STAKE_USDT
    )
    payload["defaults"]["minimumPairBudgetUsdt"] = float(MIN_PAIR_BUDGET_USDT)
    payload["policy"]["exchangeMinimumPerLegUsdt"] = float(
        MIN_EXCHANGE_LEG_STAKE_USDT
    )
    payload["policy"]["dynamicRequiredBudgetOnSkew"] = True
    return payload


def install_patches() -> None:
    base.MonitorConfig.validate = validate_monitor_config
    v2.quote_equal_share_pair = quote_equal_share_pair_min_one
    base.state_payload = state_payload
    with base.STATE.lock:
        if base.STATE.config.pair_budget_usdt < DEFAULT_PAIR_BUDGET_USDT:
            base.STATE.config = replace(
                base.STATE.config,
                pair_budget_usdt=DEFAULT_PAIR_BUDGET_USDT,
            )


def main() -> None:
    install_patches()
    v2.main()


if __name__ == "__main__":
    main()
