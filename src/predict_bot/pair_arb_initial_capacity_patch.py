from __future__ import annotations

from decimal import Decimal
from functools import wraps
from typing import Any


PAIR_ARB_010_INITIAL_QUOTE_CAPACITY_RATIO = Decimal("0.65")
PAIR_ARB_INITIAL_CAPACITY_POLICY_VERSION = "PAIR_ARB_010_INITIAL_CAPACITY_V1"


def _is_pair_arb_010_requote(
    accepted: list[dict[str, Any]],
    minimum_capacity: Decimal,
    *,
    live: Any,
) -> bool:
    if minimum_capacity != live.PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO:
        return False
    strategies = {
        str(item.get("selected_strategy") or "").strip().upper()
        for item in accepted
    }
    return strategies == {"PAIR_ARB_010"}


def install_pair_arb_initial_capacity_patch() -> None:
    from . import live_trading as live

    engine = live.LiveM0WEngine
    original_requote = engine._requote_qc_pair
    if getattr(original_requote, "_pair_arb_010_initial_capacity_v1", False):
        return

    @wraps(original_requote)
    def requote_with_lower_initial_capacity(
        self: Any,
        accepted: list[dict[str, Any]],
        *,
        minimum_capacity: Decimal = live.PAIR_ARB_QC_MIN_QUOTE_CAPACITY_RATIO,
    ) -> tuple[list[dict[str, Any]] | None, str]:
        effective_minimum = (
            PAIR_ARB_010_INITIAL_QUOTE_CAPACITY_RATIO
            if _is_pair_arb_010_requote(
                accepted,
                minimum_capacity,
                live=live,
            )
            else minimum_capacity
        )
        return original_requote(
            self,
            accepted,
            minimum_capacity=effective_minimum,
        )

    requote_with_lower_initial_capacity._pair_arb_010_initial_capacity_v1 = True  # type: ignore[attr-defined]
    engine._requote_qc_pair = requote_with_lower_initial_capacity

    original_state = engine.state
    if getattr(original_state, "_pair_arb_010_initial_capacity_state_v1", False):
        return

    @wraps(original_state)
    def state_with_initial_capacity_policy(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original_state(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        policy = payload.get("policy")
        if isinstance(policy, dict):
            policy["pairInitialQuoteCapacityPct"] = float(
                PAIR_ARB_010_INITIAL_QUOTE_CAPACITY_RATIO * Decimal(100)
            )
            policy["pairInitialQuoteCapacityStrategy"] = "PAIR_ARB_010"
            policy["pairInitialQuoteCapacityPolicyVersion"] = (
                PAIR_ARB_INITIAL_CAPACITY_POLICY_VERSION
            )
            policy["pairFinalQuoteCapacityPct"] = float(
                live.PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO * Decimal(100)
            )
            policy["pairInsuranceQuoteCapacityPct"] = float(
                live.PAIR_ARB_MIN_QUOTE_CAPACITY_RATIO * Decimal(100)
            )
        return payload

    state_with_initial_capacity_policy._pair_arb_010_initial_capacity_state_v1 = True  # type: ignore[attr-defined]
    engine.state = state_with_initial_capacity_policy
