from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class ProspectiveOwnershipTransitionContext:
    candidate_side: Optional[str]
    ownership_wants_birth: bool
    transition_allows: bool
    transition_bind_role: str
    transition_reason: str

@dataclass(frozen=True)
class ProspectiveOwnershipTransitionDecision:
    allow_thesis_birth: bool
    reason: str

class ProspectiveOwnershipTransitionGuardV1:
    """Ordering guard between DirectionalOwnership and ResponsibilityTransition.

    It does not choose a side and does not implement Repair-first itself. The
    candidate side comes from the frozen Ownership policy; the Transition
    decision comes from the frozen RepairFirstResponsibilityTransition policy.
    This guard only prevents a thesis from being materialized before that
    candidate side has passed Transition.
    """
    name='prospective_ownership_transition_guard_v1'

    def evaluate(self, x: ProspectiveOwnershipTransitionContext) -> ProspectiveOwnershipTransitionDecision:
        if not x.ownership_wants_birth:
            return ProspectiveOwnershipTransitionDecision(False,'OWNERSHIP_DID_NOT_REQUEST_BIRTH')
        if not x.transition_allows:
            return ProspectiveOwnershipTransitionDecision(False,f'TRANSITION_BLOCK:{x.transition_bind_role}:{x.transition_reason}')
        return ProspectiveOwnershipTransitionDecision(True,'OWNERSHIP_AND_TRANSITION_ALLOW')
