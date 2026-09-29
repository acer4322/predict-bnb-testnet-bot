from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

EPS = 1e-9

@dataclass(frozen=True)
class CompletionContext:
    t: int
    parent_id: Optional[int]
    parent_side: Optional[str]
    up_shares: float
    down_shares: float
    floor: float
    parent_unresolved: bool = False

    @property
    def share_gap(self) -> float:
        return abs(float(self.up_shares) - float(self.down_shares))

    @property
    def share_gap_settled(self) -> bool:
        side = self.parent_side
        if side not in ('UP','DOWN'):
            return True
        opp = 'DOWN' if side == 'UP' else 'UP'
        vals = {'UP': float(self.up_shares), 'DOWN': float(self.down_shares)}
        return vals[side] >= vals[opp] - EPS

@dataclass(frozen=True)
class CompletionDecision:
    share_repair_settled: bool
    management_complete: bool
    close_share_parent: bool
    open_economic_deficit: bool
    reason: str

@dataclass(frozen=True)
class OwnershipContext:
    t: int
    seconds_left: float
    has_thesis: bool
    p_expand: float
    signal_side: Optional[str]
    recoverable: bool

@dataclass(frozen=True)
class OwnershipDecision:
    create_thesis: bool
    side: Optional[str]
    reason: str

@dataclass(frozen=True)
class HandoffContext:
    t: int
    side: Optional[str]
    recoverability_observed: bool
    recoverable: bool

@dataclass(frozen=True)
class HandoffDecision:
    allow_active_handoff: bool
    reason: str

@dataclass(frozen=True)
class GenerationContext:
    generation_authorized: bool
    debt_increment: float
    paid_increment: float
    responsibility_count: int
    physical_expand_live: bool = False
    payment_progress_observed: bool = False
    equivalent_expand_owned: bool = False

@dataclass(frozen=True)
class GenerationDecision:
    unlocked: bool
    allow_new_responsibility: bool
    reason: str

@dataclass(frozen=True)
class SchedulerContext:
    t: int
    seconds_left: float
    responsibility_live: bool
    receipt_advanced: bool
    after_kind: Optional[str] = None
    equivalent_child_live: bool = False

@dataclass(frozen=True)
class SchedulerDecision:
    reevaluate_management: bool
    event_clock_authority: bool
    reason: str

@dataclass(frozen=True)
class RepairExecutionContext:
    t: int
    seconds_left: float
    parent_id: Optional[int]
    parent_side: Optional[str]
    overflow_born_parent: bool
    armed: bool
    churn_count: int
    payment_progress_since_arm: bool
    active_already_owned: bool
    hard_confirmed: bool
    floor: float
    manager_debt: float
    live_ask: Optional[float]
    legal_physical_qty: Optional[float]

@dataclass(frozen=True)
class RepairExecutionDecision:
    allow_active_handoff: bool
    physical_qty: float
    reason: str
