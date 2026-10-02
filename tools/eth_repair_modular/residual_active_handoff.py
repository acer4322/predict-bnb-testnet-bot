from __future__ import annotations

from dataclasses import dataclass
import math

EPS = 1e-9


@dataclass(frozen=True)
class ResidualRepairActiveHandoffContext:
    t: int
    seconds_left: float
    responsibility_id: int | None
    responsibility_side: str | None
    prior_confirmed_payment_qty: float
    authoritative_residual_debt: float
    physical_truth_role: str
    passive_release_confirmed: bool
    passive_live: bool
    same_parent_unresolved_carriers: int
    active_already_owned: bool
    live_ask: float | None
    legal_physical_qty: float | None
    candidate_repair_allocation: float
    candidate_overflow_allocation: float
    recursive_current_coordinate_recoverable: bool


@dataclass(frozen=True)
class ResidualRepairActiveHandoffDecision:
    allow: bool
    physical_qty: float
    reason: str


class ResidualRepairActiveHandoffPolicyV1:
    """Execution-only handoff for an already-paid persistent Repair responsibility.

    This policy never re-labels the physical carrier as EXPAND.  While residual
    Repair debt exists, the carrier is authorized as REPAIR.  If the venue-min
    physical quantity is larger than residual debt, AllocationLedger remains
    responsible for Repair-first / overflow-second accounting and for birthing
    any next responsibility from confirmed overflow.
    """

    name = "residual_repair_active_handoff_v1"

    def evaluate(self, ctx: ResidualRepairActiveHandoffContext) -> ResidualRepairActiveHandoffDecision:
        if ctx.responsibility_id is None or ctx.responsibility_side not in ("UP", "DOWN"):
            return ResidualRepairActiveHandoffDecision(False, 0.0, "NO_PERSISTENT_REPAIR_RESPONSIBILITY")
        if ctx.prior_confirmed_payment_qty <= EPS:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "NO_PRIOR_CONFIRMED_REPAIR_PAYMENT")
        if ctx.authoritative_residual_debt <= EPS:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "NO_RESIDUAL_REPAIR_DEBT")
        if ctx.physical_truth_role != "REPAIR":
            return ResidualRepairActiveHandoffDecision(False, 0.0, "PHYSICAL_TRUTH_ROLE_NOT_REPAIR")
        if ctx.passive_live:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "PASSIVE_CARRIER_STILL_LIVE")
        if not ctx.passive_release_confirmed:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "PASSIVE_RELEASE_NOT_CONFIRMED")
        if ctx.same_parent_unresolved_carriers > 0:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "SAME_PARENT_CARRIER_STILL_UNRESOLVED")
        if ctx.active_already_owned:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "ACTIVE_ALREADY_OWNED")
        if ctx.live_ask is None or ctx.legal_physical_qty is None:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "NO_EXECUTABLE_ACTIVE_FRONTIER")
        qty = float(ctx.legal_physical_qty)
        if not math.isfinite(qty) or qty <= EPS or qty > 12.0 + EPS:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "ILLEGAL_PHYSICAL_SLICE")
        repair = float(ctx.candidate_repair_allocation)
        overflow = float(ctx.candidate_overflow_allocation)
        if repair <= EPS or repair > float(ctx.authoritative_residual_debt) + EPS:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "INVALID_REPAIR_FIRST_ALLOCATION")
        if abs((repair + overflow) - qty) > 1e-7:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "PHYSICAL_ALLOCATION_NONCONSERVING")
        # Preserve the frozen <=180s no-new-speculative-exposure boundary.
        # A venue-min carrier with overflow would create new exposure, so it is
        # not admitted late. Pure late existing-debt payment remains a separate
        # capability test and is not generalized here.
        if ctx.seconds_left <= 180.0:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "LATE_BOUNDARY_FROZEN_FOR_THIS_MODULE")
        if overflow > EPS and not ctx.recursive_current_coordinate_recoverable:
            return ResidualRepairActiveHandoffDecision(False, 0.0, "OVERFLOW_RECOVERY_NOT_PROVEN")
        return ResidualRepairActiveHandoffDecision(True, qty, "ALLOW_RESIDUAL_REPAIR_ACTIVE_HANDOFF")
