from __future__ import annotations
from dataclasses import dataclass

EPS=1e-9

@dataclass(frozen=True)
class ParentFamilyTransitionSafetyContext:
    initial_repair_debt:float
    remaining_repair_debt:float
    repair_paid:float
    overflow_born:float
    transition_overflow:float
    physical_confirmed_qty:float
    unresolved_inflight_qty:float=0.0
    overflow_visible_to_transition_frontier:bool=True
    prospective_overflow_authorized:bool=True
    joint_recoverable:bool=True
    zero_prebirth_leak:bool=True
    zero_duplicate_debt:bool=True

@dataclass(frozen=True)
class ParentFamilyTransitionSafetyDecision:
    physical_allocation_error:float
    repair_overpay:float
    overflow_bucket_mismatch:float
    potential_overflow_if_all_inflight_fills:float
    realized_safe:bool
    worst_case_safe:bool
    safe:bool
    reason:str

class ParentFamilyTransitionAwareSafetyPolicyV2:
    """Safety semantics for multi-carrier Repair families with Repair-first/overflow-second.

    Confirmed physical fill may exceed the original Repair debt. That is not an
    overfill when AllocationLedger V2 allocates at most the original debt to Repair
    and the exact excess to one transition-overflow bucket that is visible to the
    responsibility frontier. Unresolved physical quantity that could create future
    overflow additionally requires prospective transition authority and joint
    recoverability.
    """
    name='parent_family_transition_aware_safety_v2'

    def evaluate(self,ctx:ParentFamilyTransitionSafetyContext)->ParentFamilyTransitionSafetyDecision:
        initial=max(0.0,float(ctx.initial_repair_debt)); rem=max(0.0,float(ctx.remaining_repair_debt))
        paid=max(0.0,float(ctx.repair_paid)); overflow=max(0.0,float(ctx.overflow_born)); trans=max(0.0,float(ctx.transition_overflow))
        physical=max(0.0,float(ctx.physical_confirmed_qty)); inflight=max(0.0,float(ctx.unresolved_inflight_qty))
        alloc_err=abs(physical-(paid+overflow)); repair_over=max(0.0,paid-initial); bucket_err=abs(overflow-trans)
        overflow_visibility_ok=(overflow<=EPS) or bool(ctx.overflow_visible_to_transition_frontier)
        realized=(alloc_err<=1e-7 and repair_over<=EPS and bucket_err<=1e-7 and overflow_visibility_ok and bool(ctx.zero_prebirth_leak) and bool(ctx.zero_duplicate_debt))
        potential_over=max(0.0,inflight-rem)
        prospective_ok=(potential_over<=EPS) or (bool(ctx.prospective_overflow_authorized) and bool(ctx.joint_recoverable))
        worst=realized and prospective_ok
        if not realized:
            if alloc_err>1e-7:reason='PHYSICAL_ALLOCATION_MISMATCH'
            elif repair_over>EPS:reason='REPAIR_OVERPAY'
            elif bucket_err>1e-7:reason='OVERFLOW_BUCKET_MISMATCH'
            elif not overflow_visibility_ok:reason='OVERFLOW_NOT_VISIBLE_TO_TRANSITION_FRONTIER'
            elif not ctx.zero_prebirth_leak:reason='PREBIRTH_PAYMENT_LEAK'
            else:reason='DUPLICATE_DEBT'
        elif not prospective_ok:reason='UNAUTHORIZED_OR_UNRECOVERABLE_PROSPECTIVE_OVERFLOW'
        else:reason='SAFE_REPAIR_FIRST_OVERFLOW_SECOND'
        return ParentFamilyTransitionSafetyDecision(alloc_err,repair_over,bucket_err,potential_over,realized,worst,worst,reason)
