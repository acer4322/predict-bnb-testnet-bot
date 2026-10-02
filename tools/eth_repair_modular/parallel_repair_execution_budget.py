from __future__ import annotations
from dataclasses import dataclass
import math

EPS = 1e-9

@dataclass(frozen=True)
class ParallelRepairExecutionContext:
    t: int
    seconds_left: float
    parent_id: int | None
    parent_side: str | None
    same_parent_debt: float
    epoch_attached_debt: float
    live_ask: float | None
    economic_ceiling: float | None
    floor_before: float
    floor_after_epoch_slice_at_live_ask: float | None
    passive_live: bool
    payment_progress_since_epoch: bool
    active_already_owned: bool
    hard_confirmed: bool
    passive_reserved_qty: float = 0.0
    other_same_parent_reserved_qty: float = 0.0

@dataclass(frozen=True)
class ParallelRepairExecutionDecision:
    allow_active_parallel_child: bool
    physical_qty: float
    conservative_floor_lower_bound: float | None
    reason: str
    reserved_qty: float = 0.0
    unreserved_repair_capacity: float = 0.0

class SameParentAggregateParallelRepairPolicyV1:
    """Research-only candidate for ordinary/existing Repair parents.

    Manager debt is authoritative at parent scope. Passive and Active carriers
    may coexist only inside one prospective shared execution budget: unresolved
    sibling quantity is reserved before a new Active venue-min tranche is
    admitted. Confirmed fills are still allocated solely by AllocationLedger V2
    using Repair-first/overflow-second semantics.
    """
    name = 'same_parent_aggregate_parallel_repair_execution_v1'

    def evaluate(self, ctx: ParallelRepairExecutionContext) -> ParallelRepairExecutionDecision:
        if ctx.parent_id is None or ctx.parent_side not in ('UP', 'DOWN'):
            return ParallelRepairExecutionDecision(False, 0.0, None, 'NO_REPAIR_PARENT')
        if ctx.active_already_owned or ctx.hard_confirmed:
            return ParallelRepairExecutionDecision(False, 0.0, None, 'ACTIVE_ALREADY_OWNED')
        if ctx.payment_progress_since_epoch:
            return ParallelRepairExecutionDecision(False, 0.0, None, 'PAYMENT_PROGRESS_REASSESS_SHARED_BUDGET')
        if ctx.seconds_left <= 180.0:
            return ParallelRepairExecutionDecision(False, 0.0, None, 'LATE_NO_NEW_ACTIVE_EXPOSURE')
        debt = max(0.0, float(ctx.same_parent_debt))
        if debt <= EPS:
            return ParallelRepairExecutionDecision(False, 0.0, None, 'NO_PARENT_REPAIR_DEBT')
        ask = ctx.live_ask
        if ask is None or not math.isfinite(float(ask)) or float(ask) <= EPS:
            return ParallelRepairExecutionDecision(False, 0.0, None, 'NO_EXECUTABLE_ACTIVE_FRONTIER')
        ask = float(ask)
        if ctx.economic_ceiling is None or ask > float(ctx.economic_ceiling) + EPS:
            return ParallelRepairExecutionDecision(False, 0.0, None, 'ABOVE_INHERITED_ECONOMIC_CEILING')
        legal = 1.0 / ask
        if not math.isfinite(legal) or legal <= EPS:
            return ParallelRepairExecutionDecision(False, 0.0, None, 'ILLEGAL_PHYSICAL_SLICE')

        reserved = max(0.0, float(ctx.passive_reserved_qty)) + max(0.0, float(ctx.other_same_parent_reserved_qty))
        available = max(0.0, debt - reserved)
        if legal > available + EPS:
            return ParallelRepairExecutionDecision(
                False, 0.0, None, 'INSUFFICIENT_UNRESERVED_PARENT_CAPACITY',
                reserved_qty=reserved, unreserved_repair_capacity=available,
            )

        # Conservative floor bound: credit only the already-measured epoch slice
        # with Repair benefit. Any additional quantity needed to reach venue
        # minimum is charged at ask with zero floor benefit.
        if ctx.floor_after_epoch_slice_at_live_ask is None:
            return ParallelRepairExecutionDecision(False, 0.0, None, 'NO_CONSERVATIVE_FLOOR_REFERENCE', reserved_qty=reserved, unreserved_repair_capacity=available)
        extra = max(0.0, legal - max(0.0, float(ctx.epoch_attached_debt)))
        lower = float(ctx.floor_after_epoch_slice_at_live_ask) - extra * ask
        if lower + EPS < float(ctx.floor_before):
            return ParallelRepairExecutionDecision(False, 0.0, lower, 'CONSERVATIVE_FLOOR_DAMAGE', reserved_qty=reserved, unreserved_repair_capacity=available)
        return ParallelRepairExecutionDecision(True, legal, lower, 'ALLOW_MIN_LEGAL_ACTIVE_SHARED_PARENT_BUDGET', reserved_qty=reserved, unreserved_repair_capacity=available)
