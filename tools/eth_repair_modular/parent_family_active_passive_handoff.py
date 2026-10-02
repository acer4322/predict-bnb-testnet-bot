from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable,Tuple

EPS=1e-9

@dataclass(frozen=True)
class FamilyCarrierState:
    key:str
    route:str
    submitted_qty:float
    actual_filled:float
    baseline_actual:float
    live:bool
    cancel_pending:bool=False

@dataclass(frozen=True)
class ParentFamilyHandoffContext:
    parent_id:int
    responsibility_cap_at_active_submit:float
    carriers:Tuple[FamilyCarrierState,...]

@dataclass(frozen=True)
class ParentFamilyHandoffDecision:
    realized_after_active:float
    unresolved_inflight:float
    worst_case_owned:float
    realized_overfill:float
    worst_case_overfill:float
    safe:bool

class ParentFamilyActivePassiveHandoffPolicyV1:
    name='parent_family_active_passive_handoff_v1'
    def evaluate(self,ctx:ParentFamilyHandoffContext)->ParentFamilyHandoffDecision:
        cap=max(0.0,float(ctx.responsibility_cap_at_active_submit))
        realized=0.0; inflight=0.0
        for c in ctx.carriers:
            sub=max(0.0,float(c.submitted_qty)); now=max(0.0,float(c.actual_filled)); base=max(0.0,min(float(c.baseline_actual),now))
            post=max(0.0,now-base); realized+=post
            # Cancel-pending remains owned until terminal; live or cancel-pending unresolved qty is in-flight.
            if bool(c.live) or bool(c.cancel_pending): inflight+=max(0.0,sub-now)
        worst=realized+inflight
        ro=max(0.0,realized-cap); wo=max(0.0,worst-cap)
        return ParentFamilyHandoffDecision(realized,inflight,worst,ro,wo,ro<=EPS and wo<=EPS)
