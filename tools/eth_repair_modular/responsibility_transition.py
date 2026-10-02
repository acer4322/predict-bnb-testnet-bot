from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

EPS=1e-9

@dataclass(frozen=True)
class ResponsibilityTransitionContext:
    thesis_side: Optional[str]
    repair_debt_up: float
    repair_debt_down: float

@dataclass(frozen=True)
class ResponsibilityTransitionDecision:
    allow_expand_ownership: bool
    bind_role: str
    side: Optional[str]
    reason: str
    live_repair_debt: float

class RepairFirstResponsibilityTransitionV1:
    name='repair_first_existing_thesis_transition_v1'
    def evaluate(self,ctx:ResponsibilityTransitionContext)->ResponsibilityTransitionDecision:
        side=str(ctx.thesis_side).upper() if ctx.thesis_side is not None else None
        if side not in ('UP','DOWN'):
            return ResponsibilityTransitionDecision(False,'NONE',None,'NO_THESIS_SIDE',0.0)
        debt=float(ctx.repair_debt_up if side=='UP' else ctx.repair_debt_down)
        if debt>EPS:
            # Existing directional belief may persist, but it cannot own an EXPAND carrier
            # on a side that is currently obligated to pay live Repair debt.
            return ResponsibilityTransitionDecision(False,'REPAIR',side,'LIVE_REPAIR_DEBT_OWNS_SIDE_BEFORE_EXPAND',debt)
        return ResponsibilityTransitionDecision(True,'EXPAND',side,'NO_LIVE_REPAIR_DEBT_ON_THESIS_SIDE',0.0)
