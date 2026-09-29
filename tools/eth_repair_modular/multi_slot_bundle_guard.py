from __future__ import annotations
from dataclasses import dataclass
from typing import Tuple
import math

EPS=1e-9

@dataclass(frozen=True)
class PendingExpandLeg:
    qty: float
    price: float

@dataclass(frozen=True)
class OwnedRepairLeg:
    qty: float
    price: float

@dataclass(frozen=True)
class MultiSlotBundleRecoverabilityContext:
    thesis_side: str
    floor_before: float
    up_qty: float
    down_qty: float
    cost: float
    pending_expand_legs: Tuple[PendingExpandLeg,...]
    owned_repair_legs: Tuple[OwnedRepairLeg,...]
    repair_bid: float | None
    max_future_venue_qty: float = 12.0

@dataclass(frozen=True)
class MultiSlotBundleRecoverabilityDecision:
    recoverable: bool
    reason: str
    thesis_side: str
    repair_side: str | None
    pending_expand_qty: float
    pending_expand_cost: float
    hyp_floor_after_all_pending_expand: float
    projected_floor_after_owned_repair: float
    repair_gap_after_all_pending_expand: float
    owned_repair_qty: float
    owned_repair_floor_gain: float
    repair_room_after_owned: float
    economic_repair_ceiling: float | None
    admissible_future_repair_price: float | None
    future_need_qty: float | None
    future_legal_qty: float | None
    future_required_qty: float | None

class MultiSlotBundleRecoverabilityPolicyV1:
    """Conservative all-pending-fill guard for same-objective Maker siblings.

    Current realized inventory/cost is authoritative. Every unresolved sibling
    Expand leg supplied in the context is assumed to fill. Existing opposite-side
    Repair reservations contribute only their deterministic floor gain q*(1-p).
    If that is insufficient, at least one additional legal Repair carrier must fit
    inside the post-expand quantity gap and max venue size.
    """
    name='multi_slot_bundle_recoverability_v1'

    def evaluate(self,ctx:MultiSlotBundleRecoverabilityContext)->MultiSlotBundleRecoverabilityDecision:
        side=str(ctx.thesis_side).upper()
        if side not in ('UP','DOWN'):
            return MultiSlotBundleRecoverabilityDecision(False,'INVALID_THESIS_SIDE',side,None,0.0,0.0,float(ctx.floor_before),float(ctx.floor_before),0.0,0.0,0.0,0.0,None,None,None,None,None)
        repair='DOWN' if side=='UP' else 'UP'
        ex_qty=0.0;ex_cost=0.0
        for leg in ctx.pending_expand_legs:
            q=max(0.0,float(leg.qty));p=float(leg.price)
            if q<=EPS or not math.isfinite(q) or not math.isfinite(p) or not(EPS<p<1.0-EPS):
                return MultiSlotBundleRecoverabilityDecision(False,'INVALID_PENDING_EXPAND_LEG',side,repair,ex_qty,ex_cost,float(ctx.floor_before),float(ctx.floor_before),0.0,0.0,0.0,0.0,None,None,None,None,None)
            ex_qty+=q;ex_cost+=q*p
        u=float(ctx.up_qty)+(ex_qty if side=='UP' else 0.0)
        d=float(ctx.down_qty)+(ex_qty if side=='DOWN' else 0.0)
        c=float(ctx.cost)+ex_cost
        hfloor=min(u,d)-c
        thesis_qty=u if side=='UP' else d
        repair_qty=d if repair=='DOWN' else u
        hgap=max(0.0,thesis_qty-repair_qty)
        owned=0.0;gain=0.0
        for leg in ctx.owned_repair_legs:
            q=max(0.0,float(leg.qty));p=float(leg.price)
            if q<=EPS: continue
            owned+=q
            if math.isfinite(p) and EPS<p<1.0-EPS: gain+=q*(1.0-p)
        projected=hfloor+gain
        room=max(0.0,hgap-owned)
        ceiling=(thesis_qty-c)/hgap if hgap>EPS else None
        rbid=float(ctx.repair_bid) if ctx.repair_bid is not None else None
        admissible=min(rbid,float(ceiling)) if rbid is not None and ceiling is not None and math.isfinite(rbid) and math.isfinite(float(ceiling)) else None
        if projected>=-EPS:
            return MultiSlotBundleRecoverabilityDecision(True,'EXISTING_REPAIR_RESERVATION_COVERS_BUNDLE',side,repair,ex_qty,ex_cost,hfloor,projected,hgap,owned,gain,room,ceiling,admissible,0.0,0.0,0.0)
        if admissible is None or not(EPS<admissible<1.0-EPS):
            return MultiSlotBundleRecoverabilityDecision(False,'NO_ADMISSIBLE_FUTURE_REPAIR_PRICE',side,repair,ex_qty,ex_cost,hfloor,projected,hgap,owned,gain,room,ceiling,admissible,None,None,None)
        need=max(0.0,-projected)/(1.0-admissible)
        legal=1.0/admissible
        req=max(need,legal)
        venue_ok=req<=float(ctx.max_future_venue_qty)+EPS
        recoverable=req<=room+EPS and venue_ok
        reason='PASS' if recoverable else ('FUTURE_REPAIR_QTY_EXCEEDS_VENUE' if not venue_ok else 'FUTURE_REPAIR_QTY_EXCEEDS_ROOM')
        return MultiSlotBundleRecoverabilityDecision(recoverable,reason,side,repair,ex_qty,ex_cost,hfloor,projected,hgap,owned,gain,room,ceiling,admissible,need,legal,req)
