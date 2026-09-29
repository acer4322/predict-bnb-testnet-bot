from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

EPS=1e-9

@dataclass(frozen=True)
class FirstCarrierLivenessContext:
    authorized_role: str
    objective_id: Optional[int]
    terminal_confirmed: bool
    actual_filled: float
    submitted_qty: float
    active_fallback_already_attempted: bool
    seconds_left: float
    live_ask: Optional[float]

@dataclass(frozen=True)
class FirstCarrierLivenessDecision:
    allow_active_fallback: bool
    release_unmaterialized_builder: bool
    active_qty: float
    reason: str


class FirstCarrierExecutionLivenessPolicyV1:
    """Execution-only liveness for an already-authorized initial EXPAND carrier.

    This policy never creates speculative authority.  It only changes the
    execution route of the same first-leg objective after the passive carrier
    is terminal with zero confirmed fill.  One Active attempt is allowed.  If
    that Active carrier also becomes terminal with zero fill, the unmaterialized
    builder may be released so a future independently-authorized first-leg
    objective is not blocked forever.
    """

    name = 'first_carrier_execution_liveness_v1'

    def evaluate(self, x: FirstCarrierLivenessContext) -> FirstCarrierLivenessDecision:
        role=str(x.authorized_role or '').upper()
        if role!='EXPAND':
            return FirstCarrierLivenessDecision(False,False,0.0,'NOT_EXPAND')
        if x.objective_id is None:
            return FirstCarrierLivenessDecision(False,False,0.0,'NO_OBJECTIVE_ID')
        if float(x.actual_filled)>EPS:
            return FirstCarrierLivenessDecision(False,False,0.0,'ALREADY_MATERIALIZED')
        if not bool(x.terminal_confirmed):
            return FirstCarrierLivenessDecision(False,False,0.0,'CARRIER_NOT_TERMINAL')
        if float(x.seconds_left)<=180.0+EPS:
            return FirstCarrierLivenessDecision(False,False,0.0,'LATE_NO_NEW_EXPOSURE')
        if bool(x.active_fallback_already_attempted):
            # No materialized exposure exists; do not let an exhausted execution
            # attempt own the lifecycle forever.
            return FirstCarrierLivenessDecision(False,True,0.0,'ACTIVE_FALLBACK_EXHAUSTED_RELEASE_BUILDER')
        ask=x.live_ask
        if ask is None or float(ask)<=EPS or float(ask)>=1.0-EPS:
            return FirstCarrierLivenessDecision(False,False,0.0,'NO_VALID_ACTIVE_PRICE')
        rem=max(0.0,float(x.submitted_qty))
        if rem<=EPS:
            return FirstCarrierLivenessDecision(False,False,0.0,'NO_AUTHORIZED_REMAINDER')
        legal=1.0/float(ask)
        qty=min(legal,rem)
        if qty<=EPS or qty+EPS<legal:
            return FirstCarrierLivenessDecision(False,False,0.0,'AUTHORIZED_REMAINDER_BELOW_VENUE_MIN')
        return FirstCarrierLivenessDecision(True,False,float(qty),'PASSIVE_TERMINAL_ZERO_FILL_ACTIVE_RELAY')
