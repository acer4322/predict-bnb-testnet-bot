from __future__ import annotations
from dataclasses import dataclass

EPS=1e-9

@dataclass(frozen=True)
class TransitionAwareParentFamilySafetyContext:
    initial_parent_debt: float
    repair_paid: float
    remaining_parent_debt: float
    occupancy_over_reserved: float
    overflow_born: float
    transition_overflow: float
    overflow_birth_observed: bool
    overflow_visible_to_transition_frontier: bool
    pre_birth_leak: float
    duplicate_debt: float

@dataclass(frozen=True)
class TransitionAwareParentFamilySafetyDecision:
    repair_overpay: float
    remaining_debt_negative: float
    occupancy_over_reserved: float
    overflow_bucket_mismatch: float
    pre_birth_leak: float
    duplicate_debt: float
    overflow_chain_valid: bool
    safe: bool

class TransitionAwareParentFamilySafetyPolicyV2:
    """Safety semantics for multi-carrier Repair families with composite overflow.

    Physical fills are not capped by the old Repair debt because a Repair carrier
    may legally pay the remaining old debt first and materialize confirmed excess
    as transition overflow. Safety is instead the conjunction of old-parent Repair
    conservation, concurrent execution occupancy, and an explicit overflow birth /
    transition chain with no pre-birth payment or duplicate debt.
    """
    name='transition_aware_parent_family_safety_v2'

    def evaluate(self,ctx:TransitionAwareParentFamilySafetyContext)->TransitionAwareParentFamilySafetyDecision:
        debt=max(0.0,float(ctx.initial_parent_debt)); paid=max(0.0,float(ctx.repair_paid)); rem=float(ctx.remaining_parent_debt)
        repair_over=max(0.0,paid-debt); rem_neg=max(0.0,-rem); occ=max(0.0,float(ctx.occupancy_over_reserved))
        ob=max(0.0,float(ctx.overflow_born)); to=max(0.0,float(ctx.transition_overflow)); mismatch=abs(ob-to)
        leak=max(0.0,float(ctx.pre_birth_leak)); dup=max(0.0,float(ctx.duplicate_debt))
        overflow_valid=(ob<=EPS) or (bool(ctx.overflow_birth_observed) and bool(ctx.overflow_visible_to_transition_frontier) and mismatch<=EPS)
        safe=(repair_over<=EPS and rem_neg<=EPS and occ<=EPS and mismatch<=EPS and leak<=EPS and dup<=EPS and overflow_valid)
        return TransitionAwareParentFamilySafetyDecision(repair_over,rem_neg,occ,mismatch,leak,dup,overflow_valid,safe)
