from __future__ import annotations

from decimal import Decimal
from functools import wraps
from typing import Any


def loss_streak_reduced_stake(
    initial_stake: Decimal,
    minimum_stake: Decimal,
) -> Decimal:
    """Reduce risk by half without creating an invalid sub-minimum order."""
    if initial_stake <= 0:
        return initial_stake
    return min(
        initial_stake,
        max(initial_stake * Decimal("0.5"), minimum_stake),
    )


def install_loss_streak_minimum_stake_patch() -> None:
    """Keep reduced-risk live orders at or above the exchange minimum."""
    from . import live_trading as live
    from . import loss_streak_guard_patch as guard

    current_plan = live.live_strategy_execution_plan
    if not getattr(current_plan, "_loss_streak_minimum_stake_v1", False):
        original_plan = getattr(current_plan, "__wrapped__", current_plan)

        @wraps(original_plan)
        def plan_with_minimum_reduced_stake(
            rules: dict[str, Any],
            strategy: str,
        ) -> dict[str, Any]:
            plan = dict(original_plan(rules, strategy))
            context = getattr(guard._EXECUTION_CONTEXT, "value", None)
            if (
                isinstance(context, dict)
                and context.get("strategy") == guard._strategy(strategy)
                and context.get("multiplier") == guard.LOSS_STREAK_HALF_MULTIPLIER
            ):
                initial = Decimal(str(plan["initialStakeUsdt"]))
                minimum = Decimal(str(live.LIVE_MIN_CONFIGURABLE_STAKE_USDT))
                reduced = loss_streak_reduced_stake(initial, minimum)
                plan["initialStakeUsdt"] = reduced
                if plan.get("mode") == live.LIVE_EXECUTION_MODE_FIXED:
                    plan["totalCapUsdt"] = reduced
            return plan

        plan_with_minimum_reduced_stake._loss_streak_minimum_stake_v1 = True  # type: ignore[attr-defined]
        live.live_strategy_execution_plan = plan_with_minimum_reduced_stake

    engine_class = live.LiveM0WEngine
    current_process = engine_class._process_single_signal
    if getattr(current_process, "_loss_streak_minimum_stake_v1", False):
        return
    original_process = getattr(current_process, "__wrapped__", current_process)

    @wraps(original_process)
    def process_with_minimum_reduced_stake(
        self: Any,
        signal: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        strategy = guard._strategy(signal.get("strategy"))
        with self.lock:
            rules = dict(self.live_rules)
            placement_ready = bool(self.runtime_enabled and self.armed)
        if (
            not placement_ready
            or not guard._enabled(rules, strategy)
            or strategy.startswith("PAIR_ARB_")
        ):
            return original_process(self, signal, *args, **kwargs)

        state = self.ledger.loss_streak_guard_state(strategy)
        if state["mode"] == guard.LOSS_STREAK_MODE_SHADOW:
            state = self.ledger.sync_loss_streak_shadow(strategy)

        is_add = signal.get("_live_confirmation_add") is True
        reduced_risk = (
            state["mode"] == guard.LOSS_STREAK_MODE_PROBATION
            or (
                state["mode"] == guard.LOSS_STREAK_MODE_NORMAL
                and int(state["consecutiveLosses"])
                >= guard.LOSS_STREAK_HALF_AFTER
            )
        )
        if state["mode"] == guard.LOSS_STREAK_MODE_SHADOW:
            self._record_blocked_signal(
                signal,
                "SKIPPED_LOSS_STREAK_SHADOW",
                (
                    f"{strategy} is in SHADOW after three consecutive live losses; "
                    f"paper samples {state['shadowSampleCount']}, latest-three "
                    f"PnL {state['latestShadowPnlSum']}"
                ),
                diagnostics={
                    "lossStreakGuardVersion": guard.LOSS_STREAK_GUARD_VERSION,
                    "lossStreakMode": state["mode"],
                    "shadowSampleCount": state["shadowSampleCount"],
                    "latestShadowPnlSum": state["latestShadowPnlSum"],
                },
            )
            return None
        if is_add and reduced_risk:
            self._record_blocked_signal(
                signal,
                "SKIPPED_LOSS_STREAK_REDUCED_RISK_ADD",
                (
                    f"{strategy} confirmation add suppressed while loss-streak "
                    f"guard is {state['mode']} with "
                    f"{state['consecutiveLosses']} consecutive losses"
                ),
            )
            return None
        if not is_add and reduced_risk:
            base_plan = original_plan(rules, strategy)
            initial = Decimal(str(base_plan["initialStakeUsdt"]))
            minimum = Decimal(str(live.LIVE_MIN_CONFIGURABLE_STAKE_USDT))
            reduced = loss_streak_reduced_stake(initial, minimum)
            guard._EXECUTION_CONTEXT.value = {
                "strategy": strategy,
                "multiplier": guard.LOSS_STREAK_HALF_MULTIPLIER,
            }
            try:
                self.ledger.record_event(
                    "WARN",
                    "LOSS_STREAK_REDUCED_STAKE",
                    (
                        f"{strategy} market {signal.get('market_id')} uses reduced "
                        f"initial stake {reduced} USDT from {initial} USDT; "
                        f"50% rule floored at live minimum {minimum} USDT"
                    ),
                    int(signal.get("market_id") or 0),
                )
                return original_process(self, signal, *args, **kwargs)
            finally:
                guard._EXECUTION_CONTEXT.value = None
        return original_process(self, signal, *args, **kwargs)

    process_with_minimum_reduced_stake._loss_streak_minimum_stake_v1 = True  # type: ignore[attr-defined]
    engine_class._process_single_signal = process_with_minimum_reduced_stake
