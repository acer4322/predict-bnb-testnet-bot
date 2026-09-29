from __future__ import annotations
from .contracts import SchedulerContext, SchedulerDecision


class EventCompletedOnlySchedulerPolicy:
    """Control policy matching the current V83-style event-completion clock."""
    name = 'event_completed_only_scheduler_v1'

    def evaluate(self, ctx: SchedulerContext) -> SchedulerDecision:
        if not ctx.responsibility_live:
            return SchedulerDecision(False, False, 'NO_LIVE_RESPONSIBILITY')
        if ctx.after_kind != 'REPAIR':
            return SchedulerDecision(False, False, 'WAIT_REPAIR_EVENT_CLOCK')
        return SchedulerDecision(True, True, 'REPAIR_EVENT_CLOCK')


class ContinuousResponsibilitySchedulerPolicy:
    """R2/R3 cadence primitive salvaged without inheriting their economics.

    This module owns only the strict-past reevaluation clock. It never owns a
    late-entry boundary, economic admission, side, quantity, price or physical
    submission. Those remain downstream module responsibilities.
    """
    name = 'continuous_live_responsibility_scheduler_v1'

    def evaluate(self, ctx: SchedulerContext) -> SchedulerDecision:
        if not ctx.responsibility_live:
            return SchedulerDecision(False, False, 'NO_LIVE_RESPONSIBILITY')
        if not ctx.receipt_advanced:
            return SchedulerDecision(False, False, 'NO_NEW_RECEIPT')
        return SchedulerDecision(True, False, 'CONTINUOUS_RESPONSIBILITY_REEVALUATION')
