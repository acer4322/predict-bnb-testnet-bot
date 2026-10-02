from __future__ import annotations
from dataclasses import dataclass

EPS=1e-9

@dataclass(frozen=True)
class ResidualExecutionActiveRearmContext:
    parent_id:int
    active_key:str|None
    active_live:bool
    active_cancel_pending:bool
    active_terminal_confirmed:bool
    active_actual_filled:float
    active_fill_reconciled_qty:float
    authoritative_residual_debt:float
    same_parent_other_live_carrier:bool
    pending_sibling_reconciliation:bool

@dataclass(frozen=True)
class ResidualExecutionActiveRearmDecision:
    allow_rearm:bool
    reason:str

class ResidualExecutionActiveRearmPolicyV1:
    """Release stale Active execution ownership for residual debt on the same Repair responsibility.

    This is not a new economic responsibility or generation. It only archives the
    terminal Active execution carrier after its confirmed fill is fully represented
    in authoritative payment accounting. Parent debt, objective, generation and
    responsibility identity remain unchanged.
    """
    name='residual_execution_active_rearm_v1'
    def evaluate(self,ctx:ResidualExecutionActiveRearmContext)->ResidualExecutionActiveRearmDecision:
        if ctx.active_key is None:return ResidualExecutionActiveRearmDecision(False,'NO_ACTIVE_OWNERSHIP')
        if ctx.active_live:return ResidualExecutionActiveRearmDecision(False,'ACTIVE_STILL_LIVE')
        if ctx.active_cancel_pending:return ResidualExecutionActiveRearmDecision(False,'ACTIVE_CANCEL_PENDING')
        if not ctx.active_terminal_confirmed:return ResidualExecutionActiveRearmDecision(False,'ACTIVE_NOT_TERMINAL_CONFIRMED')
        if float(ctx.active_fill_reconciled_qty)+EPS < float(ctx.active_actual_filled):return ResidualExecutionActiveRearmDecision(False,'ACTIVE_FILL_NOT_RECONCILED')
        if float(ctx.authoritative_residual_debt)<=EPS:return ResidualExecutionActiveRearmDecision(False,'NO_RESIDUAL_REPAIR_DEBT')
        if ctx.same_parent_other_live_carrier:return ResidualExecutionActiveRearmDecision(False,'OTHER_SAME_PARENT_CARRIER_LIVE')
        if ctx.pending_sibling_reconciliation:return ResidualExecutionActiveRearmDecision(False,'SIBLING_RECONCILIATION_PENDING')
        return ResidualExecutionActiveRearmDecision(True,'ARCHIVE_TERMINAL_ACTIVE_KEEP_SAME_RESIDUAL_RESPONSIBILITY')
