from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

EPS=1e-9

@dataclass
class ResponsibilityParent:
    parent_id:int
    objective_family:str
    side:str
    born_at_ms:int
    initial_debt:float
    repair_paid:float=0.0
    active_owner_key:Optional[str]=None
    passive_reserved:float=0.0
    active_reserved:float=0.0
    retired:bool=False
    metadata:dict=field(default_factory=dict)
    @property
    def remaining_debt(self)->float:
        return max(0.0,float(self.initial_debt)-float(self.repair_paid))
    @property
    def live(self)->bool:
        return (not self.retired) and self.remaining_debt>EPS

@dataclass(frozen=True)
class BirthDecision:
    allow:bool
    reason:str

@dataclass(frozen=True)
class PaymentResult:
    parent_id:int
    physical_qty:float
    repair_paid:float
    overflow:float
    remaining_debt:float
    completed:bool

class MultiParentResponsibilityPoolV1:
    """Research-only structural kernel for concurrent responsibility parents.

    The pool intentionally does NOT learn or hard-code a phase schedule such as 5->3->1.
    A caller supplies the current strict-past birth capacity. Capacity constrains only NEW
    responsibility birth; shrinking capacity never deletes existing debt. Existing-debt
    service is separate from speculative birth authority.
    """
    name='multi_parent_responsibility_pool_v1'
    def __init__(self):
        self.parents:Dict[int,ResponsibilityParent]={}
        self.next_parent_id=1
        self.objective_to_live_parent:Dict[Tuple[str,str,str],int]={}

    def live_parents(self):
        return [p for p in self.parents.values() if p.live]

    def live_count(self)->int:
        return len(self.live_parents())

    def total_remaining_debt(self)->float:
        return sum(p.remaining_debt for p in self.live_parents())

    def birth_decision(self, *, objective_family:str, side:str, objective_key:str,
                       debt:float, birth_capacity:int, allow_new_speculative_birth:bool=True)->BirthDecision:
        if not allow_new_speculative_birth:
            return BirthDecision(False,'NEW_SPECULATIVE_BIRTH_DISABLED')
        if debt<=EPS:
            return BirthDecision(False,'NO_POSITIVE_DEBT')
        if side not in ('UP','DOWN'):
            return BirthDecision(False,'INVALID_SIDE')
        cap=max(0,int(birth_capacity))
        k=(str(objective_family),str(side),str(objective_key))
        pid=self.objective_to_live_parent.get(k)
        if pid is not None and self.parents.get(pid) is not None and self.parents[pid].live:
            return BirthDecision(False,'DUPLICATE_LIVE_OBJECTIVE')
        if self.live_count()>=cap:
            return BirthDecision(False,'PHASE_BIRTH_CAPACITY_REACHED')
        return BirthDecision(True,'ALLOW_NEW_PARENT')

    def birth(self, *, objective_family:str, side:str, objective_key:str,
              debt:float, born_at_ms:int, birth_capacity:int,
              allow_new_speculative_birth:bool=True, metadata:Optional[dict]=None)->ResponsibilityParent:
        d=self.birth_decision(objective_family=objective_family,side=side,objective_key=objective_key,
                              debt=debt,birth_capacity=birth_capacity,
                              allow_new_speculative_birth=allow_new_speculative_birth)
        if not d.allow:
            raise ValueError(d.reason)
        pid=self.next_parent_id; self.next_parent_id+=1
        p=ResponsibilityParent(pid,str(objective_family),str(side),int(born_at_ms),float(debt),metadata=dict(metadata or {}))
        p.metadata['objectiveKey']=str(objective_key)
        self.parents[pid]=p
        self.objective_to_live_parent[(p.objective_family,p.side,str(objective_key))]=pid
        return p

    def attach_generation_debt(self,parent_id:int,additional_debt:float)->None:
        p=self.parents[int(parent_id)]
        if p.retired: raise ValueError('PARENT_RETIRED')
        if additional_debt<=EPS: return
        p.initial_debt+=float(additional_debt)

    def reserve(self,parent_id:int,*,passive_qty:float=0.0,active_qty:float=0.0)->None:
        p=self.parents[int(parent_id)]
        if not p.live: raise ValueError('PARENT_NOT_LIVE')
        pq=max(0.0,float(passive_qty)); aq=max(0.0,float(active_qty))
        if pq+aq>p.remaining_debt+EPS: raise ValueError('PARENT_CAPACITY_OVERRESERVED')
        p.passive_reserved=pq; p.active_reserved=aq

    def release_reservation(self,parent_id:int,*,passive:bool=False,active:bool=False)->None:
        p=self.parents[int(parent_id)]
        if passive:p.passive_reserved=0.0
        if active:p.active_reserved=0.0

    def apply_confirmed_fill(self,parent_id:int,physical_qty:float)->PaymentResult:
        p=self.parents[int(parent_id)]
        if p.retired: raise ValueError('PARENT_RETIRED')
        q=max(0.0,float(physical_qty)); pay=min(q,p.remaining_debt); overflow=max(0.0,q-pay)
        p.repair_paid+=pay
        # confirmed physical fill consumes reservations before any future arbitration
        consumed=q
        take=min(p.active_reserved,consumed); p.active_reserved-=take; consumed-=take
        take=min(p.passive_reserved,consumed); p.passive_reserved-=take
        completed=p.remaining_debt<=EPS
        if completed:
            p.retired=True; p.passive_reserved=0.0; p.active_reserved=0.0; p.active_owner_key=None
            ok=str(p.metadata.get('objectiveKey',''))
            self.objective_to_live_parent.pop((p.objective_family,p.side,ok),None)
        return PaymentResult(p.parent_id,q,pay,overflow,p.remaining_debt,completed)

    def may_service_existing_debt(self,parent_id:int)->bool:
        p=self.parents.get(int(parent_id)); return bool(p and p.live)

    def snapshot(self):
        return {pid:{'family':p.objective_family,'side':p.side,'initialDebt':p.initial_debt,
                    'repairPaid':p.repair_paid,'remainingDebt':p.remaining_debt,
                    'passiveReserved':p.passive_reserved,'activeReserved':p.active_reserved,
                    'retired':p.retired} for pid,p in sorted(self.parents.items())}
