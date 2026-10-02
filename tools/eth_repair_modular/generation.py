from __future__ import annotations
from .contracts import GenerationContext, GenerationDecision, EPS

class LegacyRecursiveGenerationPolicy:
    name = 'legacy_recursive_generation_v70f'
    def evaluate(self, ctx: GenerationContext) -> GenerationDecision:
        return GenerationDecision(True, True, 'LEGACY_RECURSIVE_AUTHORITY')

class SingleResponsibilityGenerationPolicy:
    name = 'single_responsibility_per_generation_v70g'
    def evaluate(self, ctx: GenerationContext) -> GenerationDecision:
        if not ctx.generation_authorized:
            return GenerationDecision(True, ctx.responsibility_count <= 0, 'GENERATION_OPEN')
        unlocked = ctx.debt_increment > EPS and (ctx.debt_increment - ctx.paid_increment) <= EPS
        if unlocked:
            return GenerationDecision(True, True, 'PRIOR_GENERATION_DEBT_DISCHARGED')
        return GenerationDecision(False, False, 'PRIOR_GENERATION_STILL_OWNED')

class PersistentDebtChildEpochGenerationPolicy:
    """Experimental Management seam for Target-like overlapping generations.

    Old debt remains authoritative in AllocationLedger. A new physical Expand
    child epoch may be considered once the prior equivalent Expand carrier is
    no longer live and the prior responsibility has observed confirmed Repair
    payment progress. This policy never erases debt and never authorizes an
    order by itself.
    """
    name = 'persistent_debt_child_epoch_generation_v1'
    def evaluate(self, ctx: GenerationContext) -> GenerationDecision:
        if not ctx.generation_authorized:
            return GenerationDecision(True, ctx.responsibility_count <= 0, 'GENERATION_OPEN')
        if ctx.equivalent_expand_owned or ctx.physical_expand_live:
            return GenerationDecision(False, False, 'EQUIVALENT_EXPAND_CHILD_STILL_OWNED')
        if ctx.debt_increment <= EPS:
            return GenerationDecision(False, False, 'NO_MATERIALIZED_PRIOR_EXPAND_DEBT')
        remaining = max(0.0, float(ctx.debt_increment) - float(ctx.paid_increment))
        if remaining <= EPS:
            return GenerationDecision(True, True, 'PRIOR_GENERATION_DEBT_DISCHARGED')
        if ctx.payment_progress_observed and ctx.paid_increment > EPS:
            return GenerationDecision(True, True, 'PARTIAL_REPAIR_PROGRESS_OPENS_NEW_CHILD_EPOCH')
        return GenerationDecision(False, False, 'WAIT_CONFIRMED_REPAIR_PROGRESS')
