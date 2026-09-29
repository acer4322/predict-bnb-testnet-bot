from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence

EPS=1e-9

@dataclass(frozen=True)
class InitialRepairQuotaPartitionContext:
    parent_id:int
    authoritative_debt:float
    original_price:float
    original_qty:float
    visible_candidate_prices:Sequence[float]
    economic_ceiling:float|None=None
    max_children:int=2
    max_venue_qty:float=12.0

@dataclass(frozen=True)
class InitialRepairChildPlan:
    price:float
    qty:float
    ordinal:int

@dataclass(frozen=True)
class InitialRepairQuotaPartitionDecision:
    partition:bool
    plans:tuple[InitialRepairChildPlan,...]
    total_reserved_qty:float
    residual_unreserved_debt:float
    reason:str

class InitialRepairQuotaPartitionPolicyV1:
    """Partition one monopolizing first passive Repair carrier into same-parent Maker options.

    This policy changes physical execution geometry only. It never creates debt, never changes
    Repair role/objective identity, and never requires a fixed tick spacing. Candidate prices
    must come from strict-past visible passive prices (plus the original authorized price).
    Each child uses venue-min qty=1/price; total child reservation stays <= authoritative debt.
    """
    name='initial_repair_quota_partition_v1'

    def evaluate(self,ctx:InitialRepairQuotaPartitionContext)->InitialRepairQuotaPartitionDecision:
        debt=max(0.0,float(ctx.authoritative_debt)); op=float(ctx.original_price); oq=max(0.0,float(ctx.original_qty))
        if debt<=EPS or op<=EPS or op>=1.0:
            return InitialRepairQuotaPartitionDecision(False,tuple(),0.0,debt,'INVALID_PARENT_DEBT_OR_ORIGINAL_PRICE')
        # Only replace the serializing geometry we are studying: the first child is trying to
        # reserve essentially the whole parent debt. Do not mutate already-partitioned sizing.
        if oq < debt-1e-7:
            return InitialRepairQuotaPartitionDecision(False,tuple(),0.0,debt,'ORIGINAL_CARRIER_DOES_NOT_MONOPOLIZE_PARENT')
        ceiling=None if ctx.economic_ceiling is None else float(ctx.economic_ceiling)
        vals=[op]
        vals.extend(float(x) for x in ctx.visible_candidate_prices)
        # Prefer execution options nearest the already-authorized price; this creates a local
        # option set around OUR's desired price rather than importing Target tick spacing.
        uniq=[];seen=set()
        for p in sorted(vals,key=lambda x:(abs(float(x)-op),-float(x))):
            p=float(p)
            if p<=EPS or p>=1.0: continue
            if ceiling is not None and p>ceiling+EPS: continue
            k=round(p,10)
            if k in seen: continue
            seen.add(k);uniq.append(p)
        plans=[];left=debt
        for p in uniq:
            if len(plans)>=max(0,int(ctx.max_children)): break
            q=1.0/p
            if q<=EPS or q>float(ctx.max_venue_qty)+EPS: continue
            if q<=left+EPS:
                plans.append(InitialRepairChildPlan(p,q,len(plans)+1));left=max(0.0,left-q)
        total=sum(x.qty for x in plans)
        if total>debt+1e-8:
            raise AssertionError('initial Repair partition over-reserved authoritative debt')
        if len(plans)>=2:
            return InitialRepairQuotaPartitionDecision(True,tuple(plans),total,left,'PARTITION_TWO_VISIBLE_VENUE_LEGAL_CHILDREN')
        if len(plans)==1:
            return InitialRepairQuotaPartitionDecision(False,tuple(plans),total,left,'ONLY_ONE_CHILD_FITS_PARENT_DEBT')
        return InitialRepairQuotaPartitionDecision(False,tuple(),0.0,debt,'NO_VENUE_LEGAL_CHILD_FITS_PARENT_DEBT')
