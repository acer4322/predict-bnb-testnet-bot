from __future__ import annotations
from dataclasses import dataclass

EPS=1e-9

@dataclass(frozen=True)
class GenerationActiveOwnershipContext:
    parent_id:int
    new_epoch:int
    old_active_key:str|None
    old_active_live:bool
    old_active_terminal_confirmed:bool
    old_active_actual_filled:float
    paid_at_new_epoch:float

@dataclass(frozen=True)
class GenerationActiveOwnershipDecision:
    allow_archive_old_ownership:bool
    reason:str

class GenerationScopedActiveOwnershipPolicyV1:
    """Re-arm Active execution authority for a new responsibility generation
    without changing physical parent identity.

    Old Active ownership may be archived only after its carrier is no longer
    live, terminal confirmation is present, and its confirmed fill is already
    represented by the payment baseline captured at the new epoch. Live or
    cancel-pending old carriers continue to own capacity.
    """
    name='generation_scoped_active_ownership_v1'

    def evaluate(self,ctx:GenerationActiveOwnershipContext)->GenerationActiveOwnershipDecision:
        if ctx.old_active_key is None:
            return GenerationActiveOwnershipDecision(False,'NO_OLD_ACTIVE_OWNERSHIP')
        if ctx.old_active_live:
            return GenerationActiveOwnershipDecision(False,'OLD_ACTIVE_STILL_LIVE')
        if not ctx.old_active_terminal_confirmed:
            return GenerationActiveOwnershipDecision(False,'OLD_ACTIVE_NOT_TERMINAL_CONFIRMED')
        if float(ctx.paid_at_new_epoch)+EPS < float(ctx.old_active_actual_filled):
            return GenerationActiveOwnershipDecision(False,'OLD_ACTIVE_FILL_NOT_IN_NEW_EPOCH_PAYMENT_BASE')
        return GenerationActiveOwnershipDecision(True,'ARCHIVE_TERMINAL_OLD_ACTIVE_FOR_NEW_GENERATION')
