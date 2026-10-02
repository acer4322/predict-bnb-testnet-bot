"""Four-channel route economics descriptor, without granting trading authority.

Same-quantity comparisons isolate the paid price premium, not the causal value
of certainty or continuation. Neither quoted alternative is assumed to fill.
"""
import math
from .hft244_pair_route_legality_v1 import crossing_owners

EPS=1e-9
ROLE_PURPOSE={'ECONOMIC_CORE':'REPAIR','SATELLITE_REPAIR':'REPAIR',
              'SATELLITE_EXPAND':'ADD','PROBE_CORE':'SEED'}


def payoff(side,price,qty):
    if side not in ('UP','DOWN') or not all(math.isfinite(x) for x in (price,qty)):
        raise ValueError('invalid payoff input')
    if not 0<price<1 or qty<=0:raise ValueError('invalid price/quantity')
    return {s:(qty if s==side else 0.)-qty*price for s in ('UP','DOWN')}


def describe(frame,side,role,price,qty):
    """Pure pre-decision descriptor; does not access future receipts or Target."""
    purpose=ROLE_PURPOSE.get(role,'UNKNOWN')
    passive=payoff(side,price,qty);opp='DOWN' if side=='UP' else 'UP'
    owners=frame['owners'];lots=frame['fifo'][opp]
    held=sum(q for q,p in lots)
    average=sum(q*p for q,p in lots)/held if held>EPS else None
    reserved=sum(o['remaining'] for o in owners if o['side']==side)
    ask=frame['qv'][side].get('ask')
    before={s:frame['inventory'][s]-frame['cost'] for s in ('UP','DOWN')}
    out=dict(t=frame['t'],side=side,policyRole=role,purpose=purpose,
        purposeSource='FROZEN_POLICY_ROLE_NOT_MANAGER_RESPONSIBILITY',
        passive=dict(channel='PASSIVE_'+purpose,price=price,qty=qty,quotedCost=price*qty,
                     conditionalFillPayoff=passive,potentialCrossOwners=crossing_owners(side,price,owners)),
        before=before,oppositeFifoQty=held,oppositeFifoAverage=average,
        sameSidePendingQty=reserved,physicalOccupied=len(owners),
        availablePassiveSlots=max(0,4-len(owners)),
        managerDebt=None,authorizedAddQty=None,authority='NOT_ESTABLISHED_FOR_ACTIVE_ROUTE',
        expectedFill=None,expectedContinuationValue=None,counterfactualValueIdentified=False)
    if ask is None or not math.isfinite(ask) or not 0<ask<1:
        out['active']=None;out['stopReason']='NO_VALID_CURRENT_ASK';return out
    active=payoff(side,ask,qty);premium=qty*(ask-price)
    delta={s:active[s]-passive[s] for s in ('UP','DOWN')}
    assert all(abs(delta[s]+premium)<1e-8 for s in delta)
    projection={s:before[s]+active[s] for s in before}
    out['active']=dict(channel='ACTIVE_'+purpose,price=ask,qty=qty,quotedCost=ask*qty,
        conditionalFillPayoff=active,conditionalFillFloorChange=min(projection.values())-min(before.values()),
        premiumOverPassive=premium,conditionalPayoffDifference=delta,
        potentialCrossOwners=crossing_owners(side,ask,owners),
        inheritedPairPricePass=average is None or average+ask<=1.0000001,
        withinOriginalQuotedTicket=ask*qty<=price*qty+EPS,
        fifoMatchedQtyIfOnlyThisFills=min(qty,held),
        fifoUnmatchedQtyIfOnlyThisFills=max(0.,qty-held),
        responsibilityAllocation='UNIDENTIFIED_NOT_EQUIVALENT_TO_FIFO_MATCH',
        status='QUOTED_ROUTE_COUNTEROPTION_NOT_AUTHORIZED_NATIVE_BUNDLE')
    return out
