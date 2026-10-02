from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Tuple

EPS = 1e-9

@dataclass(frozen=True)
class ParallelCycleSlotState:
    slot_id: int
    born_phase: float
    responsibility_live: bool
    expand_authority_live: bool
    repair_debt: float
    passive_progress_observed: bool = False
    active_repair_reachable: bool = False
    repair_priority: float = 0.0

@dataclass(frozen=True)
class ParallelCycleCapacityContext:
    normalized_phase: float
    slots: Tuple[ParallelCycleSlotState, ...]
    candidate_new_cycle_recoverable: bool = False

@dataclass(frozen=True)
class ParallelCycleSlotDecision:
    slot_id: int
    mode: str
    request_active_repair: bool
    reason: str

@dataclass(frozen=True)
class ParallelCycleCapacityDecision:
    new_cycle_capacity: int
    active_expand_slots: int
    allow_new_cycle_birth: bool
    slot_decisions: Tuple[ParallelCycleSlotDecision, ...]
    reason: str

class PhaseAdaptiveParallelCycleCapacityPolicy:
    """Management-only capacity coordinator.

    `phase_capacity_points` is a development envelope, not Target runtime data.
    Each pair is (normalized_phase_start, max actively-expanding cycle slots).
    Existing live responsibilities are never deleted when the envelope shrinks;
    excess slots become DRAIN_REPAIR_ONLY. Active Repair is merely requested when
    such a drain slot has no observed passive progress and an active route is
    independently reachable.
    """
    def __init__(self, phase_capacity_points=((0.0,4),(0.5,3),(0.9,2))):
        pts=tuple((float(p),int(c)) for p,c in phase_capacity_points)
        if not pts or pts[0][0] > EPS: raise ValueError('capacity points must start at phase 0')
        if any(not (0.0-EPS <= p < 1.0+EPS) or c < 0 for p,c in pts): raise ValueError('invalid capacity point')
        if any(pts[i][0] >= pts[i+1][0] for i in range(len(pts)-1)): raise ValueError('phase points must increase')
        self.points=pts

    def capacity_at(self, phase: float) -> int:
        p=min(1.0,max(0.0,float(phase))); cap=self.points[0][1]
        for start,c in self.points:
            if p+EPS >= start: cap=c
            else: break
        return int(cap)

    def evaluate(self, ctx: ParallelCycleCapacityContext) -> ParallelCycleCapacityDecision:
        cap=self.capacity_at(ctx.normalized_phase)
        live=[s for s in ctx.slots if s.responsibility_live]
        expanding=[s for s in live if s.expand_authority_live]
        # Preserve the most useful expansion slots. Higher repair priority is drained first;
        # ties drain older slots first. This only changes future Expand authority, never debt.
        ranked=sorted(expanding,key=lambda s:(float(s.repair_priority),-float(s.born_phase),-int(s.slot_id)))
        keep_ids={s.slot_id for s in ranked[:cap]}
        decisions=[]
        for s in sorted(live,key=lambda x:x.slot_id):
            if s.expand_authority_live and s.slot_id not in keep_ids:
                req=bool(s.repair_debt>EPS and (not s.passive_progress_observed) and s.active_repair_reachable)
                decisions.append(ParallelCycleSlotDecision(s.slot_id,'DRAIN_REPAIR_ONLY',req,'PHASE_CAPACITY_DRAIN'))
            elif s.expand_authority_live:
                decisions.append(ParallelCycleSlotDecision(s.slot_id,'EXPAND_AND_REPAIR',False,'WITHIN_PHASE_CAPACITY'))
            else:
                req=bool(s.repair_debt>EPS and (not s.passive_progress_observed) and s.active_repair_reachable)
                decisions.append(ParallelCycleSlotDecision(s.slot_id,'DRAIN_REPAIR_ONLY',req,'ALREADY_REPAIR_ONLY'))
        active_after=sum(d.mode=='EXPAND_AND_REPAIR' for d in decisions)
        allow=bool(ctx.candidate_new_cycle_recoverable and active_after < cap)
        return ParallelCycleCapacityDecision(cap,active_after,allow,tuple(decisions),'ALLOW_RECOVERABLE_BIRTH' if allow else ('CAPACITY_AVAILABLE_BUT_CANDIDATE_NOT_RECOVERABLE' if active_after<cap else 'PHASE_NEW_CYCLE_CAPACITY_FULL'))
