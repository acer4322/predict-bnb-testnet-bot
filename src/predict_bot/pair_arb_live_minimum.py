from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any

from . import live_trading as live

PAIR_ARB_MIN_LEG_STAKE_USDT = Decimal("1.00")
PAIR_ARB_MIN_TOTAL_STAKE_USDT = Decimal("2.00")
_PATCHED = False


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _configured_pair_leg_stake(
    engine: live.LiveM0WEngine,
    signal: dict[str, Any],
) -> Decimal | None:
    strategy = str(signal.get("strategy") or "").upper()
    if not strategy.startswith("PAIR_ARB_"):
        return None
    with engine.lock:
        rules = dict(engine.live_rules)
    try:
        strategy_index = list(rules["strategies"]).index(strategy)
        configured_total = Decimal(str(rules["strategyStakesUsdt"][strategy_index]))
    except (KeyError, IndexError, TypeError, ValueError, InvalidOperation):
        configured_total = _decimal(rules.get("maxStakeUsdt"))
    if configured_total is None or configured_total <= 0:
        return None

    dynamic_leg_stake = _decimal(signal.get("_pair_dynamic_leg_stake_usdt"))
    if strategy == "PAIR_ARB_010" and dynamic_leg_stake is not None:
        return dynamic_leg_stake

    leg_price = _decimal(signal.get("entry_price"))
    pair_total_price = _decimal(signal.get("pair_total_price"))
    up_price = _decimal(signal.get("pair_up_price"))
    down_price = _decimal(signal.get("pair_down_price"))
    if leg_price is None or leg_price <= 0:
        return None
    allocation_total = pair_total_price
    if (
        strategy == "PAIR_ARB_RISK_020"
        and up_price is not None
        and down_price is not None
        and up_price > 0
        and down_price > 0
    ):
        allocation_total = up_price + down_price
    if allocation_total is None or allocation_total <= 0:
        return None
    return (configured_total * leg_price / allocation_total).quantize(
        Decimal("0.000000000000000001"), rounding=ROUND_DOWN
    )


def install_pair_arb_minimum() -> None:
    global _PATCHED
    if _PATCHED:
        return

    original_single = live.LiveM0WEngine._process_single_signal
    original_requote = live.LiveM0WEngine._requote_qc_pair
    original_depth_plan = live.LiveM0WEngine._pair_010_profitable_depth_plan

    def process_single_signal(
        self: live.LiveM0WEngine,
        signal: dict[str, Any],
        *,
        defer_placement: bool = False,
        allow_paused_quote_only: bool = False,
    ) -> dict[str, Any] | None:
        strategy = str(signal.get("strategy") or "").upper()
        if strategy.startswith("PAIR_ARB_"):
            leg_stake = _configured_pair_leg_stake(self, signal)
            if leg_stake is not None and leg_stake < PAIR_ARB_MIN_LEG_STAKE_USDT:
                self._record_blocked_signal(
                    signal,
                    "BLOCKED_PAIR_MIN_ORDER",
                    (
                        f"PAIR_ARB {signal.get('side') or 'UNKNOWN'} leg stake "
                        f"{leg_stake:.8f} USDT is below Binance's 1.00 USDT "
                        "per-order minimum"
                    ),
                )
                return None
        return original_single(
            self,
            signal,
            defer_placement=defer_placement,
            allow_paused_quote_only=allow_paused_quote_only,
        )

    def requote_qc_pair(
        self: live.LiveM0WEngine,
        accepted: list[dict[str, Any]],
        *,
        minimum_capacity: Decimal = live.PAIR_ARB_QC_MIN_QUOTE_CAPACITY_RATIO,
    ) -> tuple[list[dict[str, Any]] | None, str]:
        try:
            initial_outputs = [
                Decimal(int(item["quote"]["amountOut"])) for item in accepted
            ]
            initial_inputs = [
                Decimal(int(item["quote"]["amountIn"])) for item in accepted
            ]
            target_output = min(initial_outputs)
            revised_inputs = [
                int(
                    (current_input * target_output / current_output).to_integral_value(
                        rounding=ROUND_DOWN
                    )
                )
                for current_input, current_output in zip(
                    initial_inputs, initial_outputs
                )
            ]
        except (
            KeyError,
            TypeError,
            ValueError,
            InvalidOperation,
            ZeroDivisionError,
        ):
            return None, "initial pair quote amounts are invalid"
        minimum_wei = int(PAIR_ARB_MIN_LEG_STAKE_USDT * Decimal(10**18))
        if min(revised_inputs, default=0) < minimum_wei:
            return None, (
                "equal-share requote would leave one PAIR_ARB leg below "
                "Binance's 1.00 USDT per-order minimum"
            )
        return original_requote(
            self,
            accepted,
            minimum_capacity=minimum_capacity,
        )

    def profitable_depth_plan(
        book: dict[str, Any],
        *,
        maximum_total_stake: Decimal,
        fee_bps: int,
    ) -> tuple[dict[str, Any] | None, str]:
        plan, error = original_depth_plan(
            book,
            maximum_total_stake=maximum_total_stake,
            fee_bps=fee_bps,
        )
        if plan is not None and min(
            Decimal(str(plan["up_stake"])),
            Decimal(str(plan["down_stake"])),
        ) < PAIR_ARB_MIN_LEG_STAKE_USDT:
            return None, (
                "profitable shared depth leaves one PAIR_ARB leg below "
                "Binance's 1.00 USDT per-order minimum"
            )
        return plan, error

    live.LiveM0WEngine._process_single_signal = process_single_signal
    live.LiveM0WEngine._requote_qc_pair = requote_qc_pair
    live.LiveM0WEngine._pair_010_profitable_depth_plan = staticmethod(
        profitable_depth_plan
    )
    _PATCHED = True
