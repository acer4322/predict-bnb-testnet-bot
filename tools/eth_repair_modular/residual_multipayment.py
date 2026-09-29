from __future__ import annotations

from dataclasses import dataclass
import math


EPS = 1e-9


@dataclass(frozen=True)
class ResidualCompositeMultipaymentContext:
    t: int
    seconds_left: float
    responsibility_id: int | None
    responsibility_side: str | None
    authorized_role: str
    physical_truth_role: str
    prior_confirmed_payments: int
    authoritative_residual_debt: float
    candidate_physical_qty: float
    candidate_repair_allocation: float
    candidate_overflow_allocation: float
    shared_remaining_budget: float
    ledger_snapshot_current: bool
    pending_sibling_reconciliation: bool
    same_parent_live_carrier: bool
    active_already_owned: bool
    venue_legal: bool
    recursive_current_coordinate_recoverable: bool


@dataclass(frozen=True)
class ResidualCompositeMultipaymentDecision:
    allow: bool
    physical_qty: float
    reason: str


class ResidualCompositeMultipaymentRouterPolicyV1:
    """Allow one physical Repair carrier to pay residual debt and expose overflow.

    The policy owns no fill accounting.  AllocationLedger V2 remains the sole
    authority for Repair-first and overflow-second allocation.  This policy is
    intentionally limited to an already-paid, still-authoritative Repair
    responsibility whose remaining debt is below the venue minimum.
    """

    name = "residual_composite_multipayment_router_v1"

    def evaluate(
        self, ctx: ResidualCompositeMultipaymentContext
    ) -> ResidualCompositeMultipaymentDecision:
        if ctx.responsibility_id is None or ctx.responsibility_side not in ("UP", "DOWN"):
            return self._reject("NO_AUTHORITATIVE_REPAIR_RESPONSIBILITY")
        if str(ctx.authorized_role).upper() != "REPAIR":
            return self._reject("AUTHORIZED_ROLE_NOT_REPAIR")
        if str(ctx.physical_truth_role).upper() != "REPAIR":
            return self._reject("PHYSICAL_TRUTH_ROLE_NOT_REPAIR")
        if int(ctx.prior_confirmed_payments) < 1:
            return self._reject("NO_PRIOR_CONFIRMED_PAYMENT")
        values = (
            float(ctx.authoritative_residual_debt),
            float(ctx.candidate_physical_qty),
            float(ctx.candidate_repair_allocation),
            float(ctx.candidate_overflow_allocation),
            float(ctx.shared_remaining_budget),
        )
        if not all(math.isfinite(value) for value in values):
            return self._reject("NONFINITE_QUANTITY")
        if any(value < -EPS for value in values):
            return self._reject("NEGATIVE_QUANTITY")
        debt, physical, repair, overflow, shared = (max(0.0, value) for value in values)
        if debt <= EPS:
            return self._reject("NO_RESIDUAL_REPAIR_DEBT")
        if not ctx.ledger_snapshot_current or ctx.pending_sibling_reconciliation:
            return self._reject("UNRECONCILED_LEDGER_STATE")
        if ctx.same_parent_live_carrier or ctx.active_already_owned:
            return self._reject("DUPLICATE_SAME_PARENT_EXECUTION_OWNERSHIP")
        if float(ctx.seconds_left) <= 180.0 + EPS:
            return self._reject("LATE_OVERFLOW_REMAINS_BLOCKED")
        if not ctx.venue_legal or physical <= EPS:
            return self._reject("VENUE_ILLEGAL_PHYSICAL_CARRIER")
        if abs(physical - repair - overflow) > 1e-7:
            return self._reject("PHYSICAL_ALLOCATION_NONCONSERVATION")
        if abs(repair - debt) > 1e-7:
            return self._reject("CARRIER_MUST_PAY_ALL_CURRENT_RESIDUAL_FIRST")
        if physical <= debt + EPS or overflow <= EPS:
            return self._reject("NOT_A_RESIDUAL_COMPOSITE_CARRIER")
        if physical > shared + EPS:
            return self._reject("SHARED_BUDGET_EXCEEDED")
        if not ctx.recursive_current_coordinate_recoverable:
            return self._reject("RECURSIVE_RECOVERY_NOT_PROVEN_AT_CURRENT_COORDINATE")
        return ResidualCompositeMultipaymentDecision(
            True,
            physical,
            "ALLOW_ONE_RESIDUAL_COMPOSITE_MULTIPAYMENT",
        )

    @staticmethod
    def _reject(reason: str) -> ResidualCompositeMultipaymentDecision:
        return ResidualCompositeMultipaymentDecision(False, 0.0, reason)
