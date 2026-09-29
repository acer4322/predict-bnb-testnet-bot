from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional

EPS = 1e-9

@dataclass(frozen=True)
class FrontierDecision:
    allow_management: bool
    reason: str
    clock: int
    observed_fill_qty: float
    allocated_fill_qty: float
    unreconciled_fill_qty: float

class ExecutionStateFrontier:
    """Deterministic hard-precedence execution-state frontier.

    Management may only evaluate a clock after all venue-confirmed material fills
    known at that clock have been committed through the economic allocation
    ledger. This module owns no strategy preference and never chooses side/role.
    """
    name = "execution_state_frontier_v1"

    def __init__(self) -> None:
        self._observed: Dict[int, float] = {}
        self._allocated: Dict[int, float] = {}

    def observe_material_fill(self, clock: int, qty: float) -> None:
        q = max(0.0, float(qty))
        if q <= EPS:
            return
        c = int(clock)
        self._observed[c] = self._observed.get(c, 0.0) + q

    def commit_allocation(self, clock: int, qty: float) -> None:
        q = max(0.0, float(qty))
        if q <= EPS:
            return
        c = int(clock)
        self._allocated[c] = self._allocated.get(c, 0.0) + q

    def decision(self, clock: int) -> FrontierDecision:
        c = int(clock)
        obs = float(self._observed.get(c, 0.0))
        alloc = float(self._allocated.get(c, 0.0))
        rem = max(0.0, obs - alloc)
        if rem > EPS:
            return FrontierDecision(False, "UNRECONCILED_MATERIAL_FILL", c, obs, alloc, rem)
        return FrontierDecision(True, "FRONTIER_RECONCILED", c, obs, alloc, 0.0)

    def clear_before(self, clock: int) -> None:
        cutoff = int(clock)
        for d in (self._observed, self._allocated):
            for c in list(d):
                if c < cutoff:
                    d.pop(c, None)

    def snapshot(self, clock: Optional[int] = None) -> dict:
        if clock is not None:
            d = self.decision(int(clock))
            return {
                "name": self.name,
                "clock": d.clock,
                "observedFillQty": d.observed_fill_qty,
                "allocatedFillQty": d.allocated_fill_qty,
                "unreconciledFillQty": d.unreconciled_fill_qty,
                "allowManagement": d.allow_management,
                "reason": d.reason,
            }
        clocks = sorted(set(self._observed) | set(self._allocated))
        return {"name": self.name, "clocks": [self.snapshot(c) for c in clocks]}
