from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence

EPS=1e-9

@dataclass(frozen=True)
class CarrierLadderContext:
    parent_id: int
    authoritative_debt: float
    existing_reserved_qty: float
    candidate_prices: Sequence[float]
    max_new_carriers: int = 3
    max_venue_qty: float = 12.0

@dataclass(frozen=True)
class CarrierReservationPlan:
    price: float
    qty: float
    ordinal: int

@dataclass(frozen=True)
class CarrierLadderDecision:
    feasible: bool
    plans: tuple[CarrierReservationPlan,...]
    total_new_reserved_qty: float
    total_reserved_after: float
    available_before: float
    reason: str

class QuotaPreservingCarrierLadderPolicyV1:
    """Builds multiple venue-legal physical carrier reservations inside one parent quota.

    It never creates debt and never changes allocation accounting. Each carrier uses
    the venue-min physical quantity implied by its own price (1/price). Plans stop
    before aggregate unresolved reservation would exceed authoritative parent debt.
    """
    name='quota_preserving_carrier_ladder_v1'

    def evaluate(self,ctx:CarrierLadderContext)->CarrierLadderDecision:
        debt=max(0.0,float(ctx.authoritative_debt)); reserved=max(0.0,float(ctx.existing_reserved_qty))
        available=max(0.0,debt-reserved); left=available; plans=[]
        inherited_over=max(0.0,reserved-debt)
        if inherited_over>EPS:
            return CarrierLadderDecision(False,tuple(),0.0,reserved,available,'EXISTING_OCCUPANCY_ABOVE_CURRENT_DEBT_WAIT_TERMINAL')
        seen=set()
        for raw in ctx.candidate_prices:
            if len(plans)>=max(0,int(ctx.max_new_carriers)): break
            p=float(raw)
            if p<=EPS or p>=1.0+EPS: continue
            k=round(p,10)
            if k in seen: continue
            seen.add(k)
            q=1.0/p
            if q<=EPS or q>float(ctx.max_venue_qty)+EPS: continue
            if q<=left+EPS:
                plans.append(CarrierReservationPlan(p,q,len(plans)+1)); left=max(0.0,left-q)
        total=sum(x.qty for x in plans)
        after=reserved+total
        if after>debt+1e-8:
            raise AssertionError('carrier ladder over-reserved parent debt')
        if len(plans)>=2: reason='MULTI_CARRIER_QUOTA_FEASIBLE'
        elif len(plans)==1: reason='ONLY_ONE_VENUE_LEGAL_CARRIER_FITS_PARENT_QUOTA'
        else: reason='NO_VENUE_LEGAL_CARRIER_FITS_PARENT_QUOTA'
        return CarrierLadderDecision(bool(plans),tuple(plans),total,after,available,reason)
