from __future__ import annotations
from dataclasses import dataclass

EPS = 1e-9


@dataclass(frozen=True)
class PaymentAwareQueueLeaseContext:
    objective_role: str | None
    parent_id: int | None
    age_ms: int
    base_ttl_ms: int
    parent_paid_qty: float
    actual_filled_qty: float
    terminal_confirmed: bool
    queue_progress_events: int


@dataclass(frozen=True)
class PaymentAwareQueueLeaseDecision:
    revoke_progress_extension: bool
    reason: str


class PaymentAwareRepairQueueLeasePolicyV1:
    """Queue progress may preserve a Repair passive carrier only after it has
    produced confirmed payment for the parent.

    Before the first confirmed Repair payment, reaching the already-existing
    base TTL means the route must be allowed to terminalize and re-arbitrate.
    This changes no TTL and does not itself authorize Active execution.
    """

    name = "payment_aware_repair_queue_lease_v1"

    def evaluate(self, ctx: PaymentAwareQueueLeaseContext) -> PaymentAwareQueueLeaseDecision:
        if str(ctx.objective_role or "").upper() != "REPAIR":
            return PaymentAwareQueueLeaseDecision(False, "NON_REPAIR_ROUTE_UNCHANGED")
        if ctx.parent_id is None:
            return PaymentAwareQueueLeaseDecision(False, "NO_PARENT_UNCHANGED")
        if bool(ctx.terminal_confirmed):
            return PaymentAwareQueueLeaseDecision(False, "ALREADY_TERMINAL")
        if float(ctx.actual_filled_qty) > EPS or float(ctx.parent_paid_qty) > EPS:
            return PaymentAwareQueueLeaseDecision(False, "CONFIRMED_PAYMENT_MAY_RETAIN_QUEUE_LEASE")
        if int(ctx.age_ms) < int(ctx.base_ttl_ms):
            return PaymentAwareQueueLeaseDecision(False, "BASE_TTL_NOT_REACHED")
        if int(ctx.queue_progress_events) <= 0:
            return PaymentAwareQueueLeaseDecision(False, "NO_QUEUE_EXTENSION_TO_REVOKE")
        return PaymentAwareQueueLeaseDecision(True, "ZERO_PAYMENT_AT_BASE_TTL_REVOKE_QUEUE_EXTENSION")
