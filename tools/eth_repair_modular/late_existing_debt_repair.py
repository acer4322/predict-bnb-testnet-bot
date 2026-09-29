from __future__ import annotations

from dataclasses import dataclass
import math

EPS = 1e-9


@dataclass(frozen=True)
class LateExistingDebtRepairContext:
    """Inputs needed to distinguish debt payment from new late exposure.

    All quantities must come from the current authoritative responsibility and
    shared AllocationLedger snapshot.  This policy does not choose price, size,
    route, or side and cannot birth a new responsibility.
    """

    seconds_left: float
    responsibility_id: int | None
    responsibility_role: str
    authoritative_existing_debt: float
    candidate_physical_qty: float
    candidate_repair_allocation: float
    candidate_overflow: float
    shared_remaining_budget: float
    venue_legal: bool
    economic_repair_price_legal: bool
    reduces_existing_downside: bool
    ledger_snapshot_current: bool
    pending_sibling_fill_reconciliation: bool
    active_already_owned: bool
    would_birth_new_responsibility: bool


@dataclass(frozen=True)
class LateExistingDebtRepairDecision:
    allow_existing_debt_payment: bool
    delegate_to_default_router: bool
    reason: str


class LateExistingDebtRepairFencePolicyV1:
    """Research-only semantic fence for final-180s Repair routing.

    The frozen contract remains: no new speculative exposure at ``<=180s``.
    This policy can only classify a carrier as payment of already-authoritative
    Repair debt when the entire physical quantity is allocated to that debt and
    confirmed overflow/new-responsibility birth is impossible.
    """

    name = "late_existing_debt_payment_not_new_exposure_v1"

    def evaluate(self, ctx: LateExistingDebtRepairContext) -> LateExistingDebtRepairDecision:
        if float(ctx.seconds_left) > 180.0 + EPS:
            return LateExistingDebtRepairDecision(False, True, "NOT_LATE_DELEGATE_TO_DEFAULT_ROUTER")
        if str(ctx.responsibility_role).upper() != "REPAIR":
            return LateExistingDebtRepairDecision(False, False, "LATE_NON_REPAIR_REMAINS_BLOCKED")
        if ctx.responsibility_id is None:
            return LateExistingDebtRepairDecision(False, False, "NO_AUTHORITATIVE_REPAIR_RESPONSIBILITY")
        raw_quantities = (
            float(ctx.authoritative_existing_debt),
            float(ctx.candidate_physical_qty),
            float(ctx.candidate_repair_allocation),
            float(ctx.candidate_overflow),
            float(ctx.shared_remaining_budget),
        )
        if not all(math.isfinite(value) for value in raw_quantities):
            return LateExistingDebtRepairDecision(False, False, "NONFINITE_REPAIR_QUANTITY")
        if any(value < -EPS for value in raw_quantities):
            return LateExistingDebtRepairDecision(False, False, "NEGATIVE_REPAIR_QUANTITY")
        debt, physical, repair, overflow, shared = (
            max(0.0, value) for value in raw_quantities
        )
        if debt <= EPS:
            return LateExistingDebtRepairDecision(False, False, "NO_EXISTING_REPAIR_DEBT")
        if not ctx.ledger_snapshot_current or ctx.pending_sibling_fill_reconciliation:
            return LateExistingDebtRepairDecision(False, False, "UNRECONCILED_SHARED_LEDGER_STATE")
        if ctx.active_already_owned:
            return LateExistingDebtRepairDecision(False, False, "ACTIVE_ALREADY_OWNED")
        if physical <= EPS or repair <= EPS:
            return LateExistingDebtRepairDecision(False, False, "NO_POSITIVE_DEBT_PAYMENT")
        if abs(physical - repair - overflow) > 1e-7:
            return LateExistingDebtRepairDecision(False, False, "PHYSICAL_ALLOCATION_NONCONSERVATION")
        if repair > debt + EPS:
            return LateExistingDebtRepairDecision(False, False, "REPAIR_ALLOCATION_EXCEEDS_EXISTING_DEBT")
        if physical > shared + EPS:
            return LateExistingDebtRepairDecision(False, False, "SHARED_RESPONSIBILITY_BUDGET_EXCEEDED")
        if overflow > EPS or physical > debt + EPS or ctx.would_birth_new_responsibility:
            return LateExistingDebtRepairDecision(False, False, "LATE_OVERFLOW_OR_NEW_EXPOSURE_BLOCKED")
        if not ctx.venue_legal:
            return LateExistingDebtRepairDecision(False, False, "VENUE_ILLEGAL_REPAIR_CARRIER")
        if not ctx.economic_repair_price_legal:
            return LateExistingDebtRepairDecision(False, False, "REPAIR_PRICE_OUTSIDE_INHERITED_ENVELOPE")
        if not ctx.reduces_existing_downside:
            return LateExistingDebtRepairDecision(False, False, "DOES_NOT_REDUCE_EXISTING_DOWNSIDE")
        return LateExistingDebtRepairDecision(True, False, "ALLOW_LATE_EXISTING_DEBT_PAYMENT_ONLY")
