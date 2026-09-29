from __future__ import annotations
from .contracts import HandoffContext, HandoffDecision

class LegacyAlwaysAllowActiveHandoffPolicy:
    name = 'legacy_active_handoff_v64'
    def evaluate(self, ctx: HandoffContext) -> HandoffDecision:
        return HandoffDecision(True, 'LEGACY_ACTIVE_HANDOFF_ALLOW')

class RecoverabilityActiveHandoffPolicy:
    name = 'whole_portfolio_recoverability_handoff_v1'
    def evaluate(self, ctx: HandoffContext) -> HandoffDecision:
        if not ctx.recoverability_observed:
            return HandoffDecision(True, 'NO_RECOVERABILITY_CONTEXT_INHERIT')
        if ctx.recoverable:
            return HandoffDecision(True, 'RECOVERABILITY_PASS')
        return HandoffDecision(False, 'RECOVERABILITY_BLOCK')
