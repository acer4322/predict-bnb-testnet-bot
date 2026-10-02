from __future__ import annotations
from dataclasses import dataclass
import math

EPS=1e-9

@dataclass(frozen=True)
class PortfolioEconomicDeficitPriorityContext:
    economic_deficit_open: bool
    floor_before: float
    projected_floor_after_candidate_and_owned_repair: float | None
    candidate_role: str

@dataclass(frozen=True)
class PortfolioEconomicDeficitPriorityDecision:
    allow_candidate: bool
    reason: str
    floor_before: float
    projected_floor: float | None
    incremental_deficit: float

class PortfolioEconomicDeficitPriorityV1:
    """Central scheduler priority for an already-open economic deficit.

    This is not an accounting invariant and does not create/erase responsibility.
    Repair is never blocked. Expand remains free when no economic deficit is open.
    While a deficit is open, a new Expand may run in parallel only when already-owned
    Repair capacity makes the joint prospective state non-worsening versus the
    current floor. This converts serial local vetoes into one portfolio-level
    responsibility-priority decision without thresholds or time cooldowns.
    """
    name='portfolio_economic_deficit_priority_v1'

    def evaluate(self,ctx:PortfolioEconomicDeficitPriorityContext)->PortfolioEconomicDeficitPriorityDecision:
        role=str(ctx.candidate_role or '').upper()
        fb=float(ctx.floor_before)
        if role!='EXPAND':
            return PortfolioEconomicDeficitPriorityDecision(True,'NON_EXPAND_NEVER_BLOCKED',fb,ctx.projected_floor_after_candidate_and_owned_repair,0.0)
        if not bool(ctx.economic_deficit_open) or fb>=-EPS:
            return PortfolioEconomicDeficitPriorityDecision(True,'NO_OPEN_ECONOMIC_DEFICIT',fb,ctx.projected_floor_after_candidate_and_owned_repair,0.0)
        pf=ctx.projected_floor_after_candidate_and_owned_repair
        if pf is None or not math.isfinite(float(pf)):
            return PortfolioEconomicDeficitPriorityDecision(False,'NO_JOINT_PORTFOLIO_FLOOR_PROJECTION',fb,None,math.inf)
        pf=float(pf)
        inc=max(0.0,fb-pf)
        if pf+EPS>=fb:
            return PortfolioEconomicDeficitPriorityDecision(True,'OPEN_DEFICIT_BUT_OWNED_REPAIR_COVERS_INCREMENTAL_DAMAGE',fb,pf,0.0)
        return PortfolioEconomicDeficitPriorityDecision(False,'OPEN_ECONOMIC_DEFICIT_REPAIR_PRIORITY',fb,pf,inc)
