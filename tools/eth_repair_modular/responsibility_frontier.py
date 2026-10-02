from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

EPS=1e-9

@dataclass(frozen=True)
class RepairResponsibilityFrontierInput:
    current_parent_side: Optional[str]
    current_parent_debt: float
    overflow_debt_up: float
    overflow_debt_down: float

@dataclass(frozen=True)
class RepairResponsibilityFrontierDecision:
    debt_up: float
    debt_down: float
    current_parent_bound: bool
    reason: str

class AuthoritativeRepairResponsibilityFrontierV2:
    """Expand Transition input frontier, not an accounting ledger.

    V1 Transition policy is unchanged: Repair debt owns a side before Expand.
    This module only ensures that an ordinary live current Repair parent is
    visible to that policy, in addition to overflow-born Repair debt.

    Amounts are used only as positive ownership/debt evidence here. Allocation
    and payment accounting remain exclusively owned by AllocationLedger V2.
    """
    name='authoritative_current_parent_plus_overflow_repair_frontier_v2'

    def evaluate(self, x: RepairResponsibilityFrontierInput) -> RepairResponsibilityFrontierDecision:
        up=max(0.0,float(x.overflow_debt_up or 0.0));dn=max(0.0,float(x.overflow_debt_down or 0.0))
        side=str(x.current_parent_side).upper() if x.current_parent_side is not None else None
        cur=max(0.0,float(x.current_parent_debt or 0.0))
        bound=False
        if side=='UP' and cur>EPS:
            up=max(up,cur);bound=True
        elif side=='DOWN' and cur>EPS:
            dn=max(dn,cur);bound=True
        return RepairResponsibilityFrontierDecision(up,dn,bound,'CURRENT_PARENT_BOUND' if bound else 'OVERFLOW_ONLY')
