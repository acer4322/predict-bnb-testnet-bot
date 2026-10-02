from __future__ import annotations
from .contracts import CompletionContext, CompletionDecision, EPS

class LegacyShareGapCompletionPolicy:
    name = 'legacy_share_gap_completion_v6'
    def evaluate(self, ctx: CompletionContext) -> CompletionDecision:
        settled = bool(ctx.share_gap_settled)
        return CompletionDecision(
            share_repair_settled=settled,
            management_complete=settled,
            close_share_parent=settled and not ctx.parent_unresolved,
            open_economic_deficit=False,
            reason='LEGACY_SHARE_GAP_COMPLETE' if settled else 'LEGACY_SHARE_GAP_OPEN',
        )

class EconomicResponsibilityCompletionPolicy:
    name = 'economic_responsibility_completion_v1'
    def evaluate(self, ctx: CompletionContext) -> CompletionDecision:
        settled = bool(ctx.share_gap_settled)
        if not settled:
            return CompletionDecision(False, False, False, ctx.floor < -EPS, 'SHARE_REPAIR_OPEN')
        if ctx.parent_unresolved:
            return CompletionDecision(True, False, False, ctx.floor < -EPS, 'SHARE_GAP_SETTLED_CARRIER_UNRESOLVED')
        if ctx.floor < -EPS:
            return CompletionDecision(True, False, True, True, 'SHARE_GAP_SETTLED_ECONOMIC_DEFICIT_OPEN')
        return CompletionDecision(True, True, True, False, 'ECONOMIC_RESPONSIBILITY_COMPLETE')
