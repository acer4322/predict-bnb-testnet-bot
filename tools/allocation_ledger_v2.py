from __future__ import annotations
from dataclasses import dataclass, field

EPS=1e-9

@dataclass
class ParentAllocationState:
    parent_id:int
    initial_debt:float
    remaining_debt:float
    repair_paid:float=0.0
    overflow_born:float=0.0
    carriers:set[str]=field(default_factory=set)

@dataclass(frozen=True)
class AllocationResult:
    carrier_key:str
    parent_id:int
    fill_increment:float
    debt_before:float
    repair_increment:float
    overflow_increment:float
    debt_after:float
    transition_overflow_total:float

class SharedParentDebtAllocationLedgerV2:
    """Allocation authority for sibling physical carriers of one Repair responsibility.

    Manager debt is shared at parent scope. Physical carriers may be larger than
    remaining debt. Confirmed fill pays parent debt first; excess contributes to
    one parent transition overflow bucket. Cumulative carrier observations are
    idempotent.
    """
    name='shared_parent_debt_repair_first_allocation_v2'
    def __init__(self):
        self.parents:dict[int,ParentAllocationState]={}
        self.carrier_seen:dict[str,float]={}
        self.carrier_parent:dict[str,int]={}
        self.transition_overflow:dict[int,float]={}
        self.allocations:list[AllocationResult]=[]

    def register_carrier(self,carrier_key:str,parent_id:int,manager_debt:float)->ParentAllocationState:
        pid=int(parent_id); debt=max(0.0,float(manager_debt))
        st=self.parents.get(pid)
        if st is None:
            st=ParentAllocationState(pid,debt,debt)
            self.parents[pid]=st
        # Later siblings must never reset/increase already-consumed parent debt.
        st.carriers.add(str(carrier_key));self.carrier_parent[str(carrier_key)]=pid
        self.carrier_seen.setdefault(str(carrier_key),0.0)
        return st

    def remaining(self,parent_id:int,fallback:float|None=None)->float:
        st=self.parents.get(int(parent_id))
        if st is not None:return max(0.0,float(st.remaining_debt))
        return max(0.0,float(fallback or 0.0))

    def allocate_cumulative(self,carrier_key:str,parent_id:int,cumulative_fill:float,manager_debt:float)->AllocationResult|None:
        key=str(carrier_key);pid=int(parent_id);cur=max(0.0,float(cumulative_fill));old=float(self.carrier_seen.get(key,0.0))
        if cur<=old+EPS:return None
        st=self.register_carrier(key,pid,manager_debt)
        inc=cur-old;before=max(0.0,float(st.remaining_debt));repair=min(inc,before);overflow=max(0.0,inc-repair)
        st.remaining_debt=max(0.0,before-repair);st.repair_paid+=repair;st.overflow_born+=overflow
        self.transition_overflow[pid]=float(self.transition_overflow.get(pid,0.0))+overflow
        self.carrier_seen[key]=cur
        out=AllocationResult(key,pid,inc,before,repair,overflow,st.remaining_debt,self.transition_overflow[pid])
        self.allocations.append(out);return out

    def describe_parent(self,parent_id:int):
        st=self.parents.get(int(parent_id))
        if st is None:return None
        return {'parentId':st.parent_id,'initialDebt':st.initial_debt,'remainingDebt':st.remaining_debt,'repairPaid':st.repair_paid,'overflowBorn':st.overflow_born,'carriers':sorted(st.carriers),'transitionOverflow':self.transition_overflow.get(st.parent_id,0.0)}
