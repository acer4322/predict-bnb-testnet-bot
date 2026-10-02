from __future__ import annotations
from .contracts import OwnershipContext, OwnershipDecision

class LegacyExistingThesisOnlyPolicy:
    name = 'legacy_existing_thesis_only_v1'
    def evaluate(self, ctx: OwnershipContext) -> OwnershipDecision:
        if ctx.has_thesis:
            return OwnershipDecision(False, None, 'EXISTING_THESIS')
        return OwnershipDecision(False, None, 'NO_THESIS_LEGACY')

class RecoverablePreSafeOwnershipPolicy:
    name = 'recoverable_pre_safe_ownership_v1'
    def __init__(self, p_expand_threshold: float = 0.5, min_seconds_left: float = 180.0):
        self.p_expand_threshold = float(p_expand_threshold)
        self.min_seconds_left = float(min_seconds_left)
    def evaluate(self, ctx: OwnershipContext) -> OwnershipDecision:
        if ctx.has_thesis:
            return OwnershipDecision(False, None, 'EXISTING_THESIS')
        if ctx.seconds_left <= self.min_seconds_left:
            return OwnershipDecision(False, None, 'LATE')
        if ctx.p_expand < self.p_expand_threshold:
            return OwnershipDecision(False, None, 'P_EXPAND_BELOW_FROZEN_THRESHOLD')
        if ctx.signal_side not in ('UP','DOWN'):
            return OwnershipDecision(False, None, 'NO_SIGNAL_SIDE')
        if not ctx.recoverable:
            return OwnershipDecision(False, None, 'UNRECOVERABLE_PRE_SAFE_OWNER')
        return OwnershipDecision(True, ctx.signal_side, 'CREATE_RECOVERABLE_PRE_SAFE_THESIS')
