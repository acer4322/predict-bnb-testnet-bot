from __future__ import annotations
import math
from .contracts import RepairExecutionContext, RepairExecutionDecision, EPS

class LegacyV36RepairExecutionPolicy:
    name = 'legacy_v36_two_disconnect_repair_execution'
    def evaluate(self, ctx: RepairExecutionContext) -> RepairExecutionDecision:
        if ctx.parent_id is None or ctx.parent_side not in ('UP','DOWN'):
            return RepairExecutionDecision(False,0.0,'NO_REPAIR_PARENT')
        if not ctx.armed:
            return RepairExecutionDecision(False,0.0,'NOT_ARMED')
        if ctx.active_already_owned or ctx.hard_confirmed:
            return RepairExecutionDecision(False,0.0,'ACTIVE_ALREADY_OWNED')
        if ctx.payment_progress_since_arm:
            return RepairExecutionDecision(False,0.0,'PAYMENT_PROGRESS_CONTINUE_PASSIVE')
        if ctx.seconds_left <= 180.0:
            return RepairExecutionDecision(False,0.0,'LATE_NO_NEW_ACTIVE_EXPOSURE')
        if ctx.floor >= -EPS or ctx.manager_debt <= EPS:
            return RepairExecutionDecision(False,0.0,'NO_NEGATIVE_FLOOR_REPAIR_NEED')
        if ctx.churn_count < 2:
            return RepairExecutionDecision(False,0.0,'LEGACY_NEEDS_TWO_DISCONNECTS')
        q = float(ctx.manager_debt)
        return RepairExecutionDecision(True,q,'LEGACY_V36_ACTIVE_REPAIR')

class RecursiveCompositeRepairExecutionPolicy:
    """Route-neutral execution policy for a real overflow-born Repair responsibility.

    Management owns only the responsibility debt.  The execution adapter may use
    one legal physical carrier that is larger than debt; confirmed fills are
    allocated Repair-first and any excess is a new responsibility.  There is no
    global handoff-count limit: every genuinely new responsibility is evaluated
    independently, while one parent may own at most one active child.
    """
    name = 'recursive_composite_single_disconnect_execution_v1'

    def evaluate(self, ctx: RepairExecutionContext) -> RepairExecutionDecision:
        if ctx.parent_id is None or ctx.parent_side not in ('UP','DOWN'):
            return RepairExecutionDecision(False,0.0,'NO_REPAIR_PARENT')
        if not ctx.overflow_born_parent:
            return RepairExecutionDecision(False,0.0,'ORDINARY_PARENT_INHERIT_LEGACY_EXECUTION')
        if not ctx.armed:
            return RepairExecutionDecision(False,0.0,'NOT_ARMED')
        if ctx.active_already_owned or ctx.hard_confirmed:
            return RepairExecutionDecision(False,0.0,'ACTIVE_ALREADY_OWNED')
        if ctx.payment_progress_since_arm:
            return RepairExecutionDecision(False,0.0,'PAYMENT_PROGRESS_CONTINUE_PASSIVE')
        if ctx.seconds_left <= 180.0:
            return RepairExecutionDecision(False,0.0,'LATE_NO_NEW_ACTIVE_EXPOSURE')
        if ctx.floor >= -EPS or ctx.manager_debt <= EPS:
            return RepairExecutionDecision(False,0.0,'NO_NEGATIVE_FLOOR_REPAIR_NEED')
        if ctx.churn_count < 1:
            return RepairExecutionDecision(False,0.0,'WAIT_FOR_DISCONNECT_EVIDENCE')
        if ctx.live_ask is None or ctx.legal_physical_qty is None:
            return RepairExecutionDecision(False,0.0,'NO_EXECUTABLE_ACTIVE_FRONTIER')
        q=float(ctx.legal_physical_qty)
        if not math.isfinite(q) or q <= EPS or q > 12.0 + EPS:
            return RepairExecutionDecision(False,0.0,'ILLEGAL_PHYSICAL_SLICE')
        return RepairExecutionDecision(True,q,'OVERFLOW_PARENT_SINGLE_DISCONNECT_ACTIVE_COMPOSITE')
