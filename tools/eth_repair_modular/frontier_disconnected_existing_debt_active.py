from __future__ import annotations
from dataclasses import dataclass
import math

EPS = 1e-9


@dataclass(frozen=True)
class FrontierDisconnectedExistingDebtActiveContext:
    parent_id: int | None
    parent_side: str | None
    seconds_left: float
    same_parent_debt: float
    live_bid: float | None
    live_ask: float | None
    inherited_economic_ceiling: float | None
    floor_before: float
    floor_after_venue_min_repair: float | None
    passive_reserved_qty: float
    other_same_parent_reserved_qty: float
    payment_progress_since_epoch: bool
    active_already_owned: bool
    hard_confirmed: bool


@dataclass(frozen=True)
class FrontierDisconnectedExistingDebtActiveDecision:
    allow_active_existing_debt: bool
    physical_qty: float
    projected_floor: float | None
    reason: str
    unreserved_debt: float = 0.0


class FrontierDisconnectedExistingDebtActivePolicyV1:
    """Existing-debt Active Repair fallback after passive economic frontier disconnects.

    This policy does not authorize a new speculative responsibility. It only
    services an already-existing Repair parent. The structural trigger is that
    the live best bid itself is above the inherited economic ceiling, meaning
    no competitive passive quote can simultaneously remain at/inside that
    ceiling. A venue-min Active child is admitted only after sibling execution
    reservations are gone and the exact projected worst-case floor strictly
    improves versus doing nothing.

    V1 intentionally preserves the legacy <=180s Active fence. Late existing-
    debt service is a separate future capability and is not silently enabled.
    """

    name = "frontier_disconnected_existing_debt_active_v1"

    def evaluate(self, ctx: FrontierDisconnectedExistingDebtActiveContext) -> FrontierDisconnectedExistingDebtActiveDecision:
        if ctx.parent_id is None or ctx.parent_side not in ("UP", "DOWN"):
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "NO_REPAIR_PARENT")
        if ctx.active_already_owned or ctx.hard_confirmed:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "ACTIVE_ALREADY_OWNED")
        if ctx.payment_progress_since_epoch:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "PAYMENT_PROGRESS_REASSESS")
        if float(ctx.seconds_left) <= 180.0:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "LATE_ACTIVE_FENCE_PRESERVED")

        debt = max(0.0, float(ctx.same_parent_debt))
        if debt <= EPS:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "NO_PARENT_REPAIR_DEBT")

        bid = ctx.live_bid
        ask = ctx.live_ask
        ceil = ctx.inherited_economic_ceiling
        if bid is None or ask is None or ceil is None:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "NO_EXECUTION_FRONTIER")
        bid = float(bid); ask = float(ask); ceil = float(ceil)
        if not all(math.isfinite(x) for x in (bid, ask, ceil)) or ask <= EPS or bid < 0:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "INVALID_EXECUTION_FRONTIER")
        if bid <= ceil + EPS:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "PASSIVE_COMPETITIVE_PRICE_STILL_WITHIN_CEILING")

        reserved = max(0.0, float(ctx.passive_reserved_qty)) + max(0.0, float(ctx.other_same_parent_reserved_qty))
        available = max(0.0, debt - reserved)
        legal = 1.0 / ask
        if not math.isfinite(legal) or legal <= EPS:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "ILLEGAL_VENUE_MIN")
        if reserved > EPS:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "SIBLING_RESERVATION_STILL_OWNS_DEBT", available)
        # V1 keeps this salvage tranche pure Repair: no venue-min overflow.
        if legal > available + EPS:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "VENUE_MIN_EXCEEDS_UNRESERVED_DEBT", available)

        projected = ctx.floor_after_venue_min_repair
        if projected is None or not math.isfinite(float(projected)):
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, None, "NO_EXACT_FLOOR_PROJECTION", available)
        projected = float(projected)
        if projected <= float(ctx.floor_before) + EPS:
            return FrontierDisconnectedExistingDebtActiveDecision(False, 0.0, projected, "ACTIVE_REPAIR_DOES_NOT_STRICTLY_IMPROVE_FLOOR", available)

        return FrontierDisconnectedExistingDebtActiveDecision(
            True,
            float(legal),
            projected,
            "ALLOW_FLOOR_IMPROVING_EXISTING_DEBT_ACTIVE_AFTER_PASSIVE_FRONTIER_DISCONNECT",
            available,
        )
