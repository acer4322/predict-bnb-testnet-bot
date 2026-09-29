from __future__ import annotations
import math

from tools.eth_repair_modular.parallel_repair_execution_budget import (
    EPS,
    ParallelRepairExecutionContext,
    ParallelRepairExecutionDecision,
    SameParentAggregateParallelRepairPolicyV1,
)


class AllocationAwareCompositeParallelRepairPolicyV2(SameParentAggregateParallelRepairPolicyV1):
    """Allow one venue-min Active Repair carrier to exceed parent debt only
    when the excess is purely a venue indivisibility artifact and there are no
    unresolved sibling reservations.

    Manager debt remains authoritative. Confirmed physical fill is allocated by
    AllocationLedger V2 Repair-first, then any physical excess becomes explicit
    overflow responsibility. This policy never relaxes payment-progress,
    economic-ceiling, late-fence, active-ownership, or sibling-reservation
    guards inherited from V1.
    """

    name = "allocation_aware_composite_parallel_repair_v2"

    def evaluate(self, ctx: ParallelRepairExecutionContext) -> ParallelRepairExecutionDecision:
        base = super().evaluate(ctx)
        if base.reason != "INSUFFICIENT_UNRESERVED_PARENT_CAPACITY":
            return base

        reserved = max(0.0, float(ctx.passive_reserved_qty)) + max(
            0.0, float(ctx.other_same_parent_reserved_qty)
        )
        if reserved > EPS:
            return base

        debt = max(0.0, float(ctx.same_parent_debt))
        ask = ctx.live_ask
        if debt <= EPS or ask is None or not math.isfinite(float(ask)) or float(ask) <= EPS:
            return base
        ask = float(ask)
        legal = 1.0 / ask
        if not math.isfinite(legal) or legal <= debt + EPS:
            return base

        # The current execution epoch must own the whole still-unpaid parent
        # debt. Do not use composite overflow to bridge debt belonging to a
        # different generation/epoch.
        attached = max(0.0, float(ctx.epoch_attached_debt))
        if attached + EPS < debt:
            return ParallelRepairExecutionDecision(
                False,
                0.0,
                None,
                "COMPOSITE_OVERFLOW_EPOCH_DOES_NOT_OWN_FULL_DEBT",
                reserved_qty=reserved,
                unreserved_repair_capacity=debt,
            )

        if ctx.floor_after_epoch_slice_at_live_ask is None:
            return ParallelRepairExecutionDecision(
                False,
                0.0,
                None,
                "NO_CONSERVATIVE_FLOOR_REFERENCE",
                reserved_qty=reserved,
                unreserved_repair_capacity=debt,
            )

        # floor_after_epoch_slice_at_live_ask already credits the debt-sized
        # Repair slice. The venue-min excess receives zero Repair benefit and
        # is charged fully at the live ask.
        overflow = max(0.0, legal - debt)
        lower = float(ctx.floor_after_epoch_slice_at_live_ask) - overflow * ask
        if lower + EPS < float(ctx.floor_before):
            return ParallelRepairExecutionDecision(
                False,
                0.0,
                lower,
                "COMPOSITE_OVERFLOW_CONSERVATIVE_FLOOR_DAMAGE",
                reserved_qty=reserved,
                unreserved_repair_capacity=debt,
            )

        return ParallelRepairExecutionDecision(
            True,
            legal,
            lower,
            "ALLOW_MIN_LEGAL_ACTIVE_COMPOSITE_OVERFLOW",
            reserved_qty=0.0,
            unreserved_repair_capacity=debt,
        )
