from __future__ import annotations
from dataclasses import dataclass
import math

EPS=1e-9

@dataclass(frozen=True)
class RepairExecutionContextV2:
    t:int
    seconds_left:float
    parent_id:int|None
    parent_side:str|None
    overflow_born_parent:bool
    armed:bool
    churn_count:int
    payment_progress_since_arm:bool
    active_already_owned:bool
    hard_confirmed:bool
    floor:float
    manager_debt:float
    live_ask:float|None
    legal_physical_qty:float|None

@dataclass(frozen=True)
class RepairExecutionDecisionV2:
    allow_active_handoff:bool
    physical_qty:float
    reason:str

class RecursiveCompositeRepairExecutionRouterV2:
    name='recursive_composite_single_disconnect_execution_v1'
    def evaluate(self,ctx:RepairExecutionContextV2)->RepairExecutionDecisionV2:
        if ctx.parent_id is None or ctx.parent_side not in ('UP','DOWN'):
            return RepairExecutionDecisionV2(False,0.0,'NO_REPAIR_PARENT')
        if not ctx.overflow_born_parent:
            return RepairExecutionDecisionV2(False,0.0,'ORDINARY_PARENT_INHERIT_LEGACY_EXECUTION')
        if not ctx.armed:
            return RepairExecutionDecisionV2(False,0.0,'NOT_ARMED')
        if ctx.active_already_owned or ctx.hard_confirmed:
            return RepairExecutionDecisionV2(False,0.0,'ACTIVE_ALREADY_OWNED')
        if ctx.payment_progress_since_arm:
            return RepairExecutionDecisionV2(False,0.0,'PAYMENT_PROGRESS_CONTINUE_PASSIVE')
        if ctx.seconds_left<=180.0:
            return RepairExecutionDecisionV2(False,0.0,'LATE_NO_NEW_ACTIVE_EXPOSURE')
        if ctx.floor>=-EPS or ctx.manager_debt<=EPS:
            return RepairExecutionDecisionV2(False,0.0,'NO_NEGATIVE_FLOOR_REPAIR_NEED')
        if ctx.churn_count<1:
            return RepairExecutionDecisionV2(False,0.0,'WAIT_FOR_DISCONNECT_EVIDENCE')
        if ctx.live_ask is None or ctx.legal_physical_qty is None:
            return RepairExecutionDecisionV2(False,0.0,'NO_EXECUTABLE_ACTIVE_FRONTIER')
        q=float(ctx.legal_physical_qty)
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:
            return RepairExecutionDecisionV2(False,0.0,'ILLEGAL_PHYSICAL_SLICE')
        return RepairExecutionDecisionV2(True,q,'OVERFLOW_PARENT_SINGLE_DISCONNECT_ACTIVE_COMPOSITE')
