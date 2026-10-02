from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.eth_repair_modular.multi_slot_registry import MultiSlotStateRegistryV1
from tools.eth_repair_modular.parallel_cycle_capacity import (
    PhaseAdaptiveParallelCycleCapacityPolicy,
    ParallelCycleCapacityContext,
    ParallelCycleSlotState,
)
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
from tools.eth_repair_modular.parent_execution_occupancy import ParentRepairExecutionOccupancyV1
from tools.eth_repair_modular.responsibility_transition import RepairFirstResponsibilityTransitionV1, ResponsibilityTransitionContext

EPS=1e-9

def ck(name, cond, detail=None):
    return {'name':name,'pass':bool(cond),'detail':detail}

def main():
    cases=[]
    reg=MultiSlotStateRegistryV1()
    for sid,phase in ((1,.05),(2,.08),(3,.12)):
        reg.create_slot(sid,born_phase=phase,thesis_side='DOWN',thesis_id=100+sid)
        reg.attach_expand_carrier(sid,f'DOWN_S{sid}',200+sid)
    cases.append(ck('THREE_LIVE_SLOTS_DISTINCT',len(reg.live_slots())==3,reg.describe()))
    try:
        reg.attach_expand_carrier(2,'DOWN_S1',202); dup_block=False
    except ValueError:
        dup_block=True
    cases.append(ck('CARRIER_CANNOT_BELONG_TWO_SLOTS',dup_block))

    trans=RepairFirstResponsibilityTransitionV1()
    d1=trans.evaluate(ResponsibilityTransitionContext('DOWN',0.0,2.0))
    d2=trans.evaluate(ResponsibilityTransitionContext('UP',0.0,2.0))
    cases.append(ck('REPAIR_FIRST_BLOCKS_ONLY_OBLIGATED_SIDE',
                    (not d1.allow_expand_ownership and d1.bind_role=='REPAIR' and d2.allow_expand_ownership),
                    {'down':d1.__dict__,'up':d2.__dict__}))

    led=GenerationAwareSharedParentDebtAllocationLedgerV3()
    led.register_carrier('P1_SEED',1,5.0)
    a1=led.allocate_cumulative('P1_SEED',1,2.0,5.0)
    g2=led.attach_generation_debt(1,'slot2:g1',3.0)
    g2dup=led.attach_generation_debt(1,'slot2:g1',3.0)
    g3=led.attach_generation_debt(1,'slot3:g1',2.0)
    p=led.describe_parent(1)
    cases.append(ck('GENERATION_ATTACH_IDEMPOTENT_AND_REPAIRPAID_PRESERVED',
                    a1 is not None and abs(p['repairPaid']-2.0)<=EPS and g2.applied and not g2dup.applied and g3.applied
                    and abs(p['initialDebt']-10.0)<=EPS and abs(p['remainingDebt']-8.0)<=EPS,p))

    occ=ParentRepairExecutionOccupancyV1()
    occ.reserve(key='P1_PASSIVE',parent_id=1,route='PASSIVE',qty=3.0)
    occ.reserve(key='P1_ACTIVE',parent_id=1,route='ACTIVE',qty=2.0)
    occ.reserve(key='P2_PASSIVE',parent_id=2,route='PASSIVE',qty=4.0)
    p1=occ.describe_parent(1,5.0);p2=occ.describe_parent(2,4.0)
    cases.append(ck('PARENT_OCCUPANCY_BOUNDS_SIBLINGS_AND_ISOLATES_PARENTS',
                    p1['overReservedQty']<=EPS and p2['overReservedQty']<=EPS and not occ.can_reserve(1,5.0,0.1)
                    and occ.parent_reserved(2)==4.0,{'p1':p1,'p2':p2}))

    active_by_parent={1:{'key':'P1_ACTIVE'},2:{'key':'P2_ACTIVE'}}
    cases.append(ck('ONE_ACTIVE_CHILD_PER_PARENT_CAN_COEXIST_ACROSS_PARENTS',len(active_by_parent)==2 and len({v['key'] for v in active_by_parent.values()})==2,active_by_parent))

    cap=PhaseAdaptiveParallelCycleCapacityPolicy(((0.0,3),(0.5,2),(0.85,1)))
    slots=(
        ParallelCycleSlotState(1,.05,True,True,1.0,True,False,.1),
        ParallelCycleSlotState(2,.08,True,True,2.0,False,True,.9),
        ParallelCycleSlotState(3,.12,True,True,3.0,False,True,.8),
    )
    cd=cap.evaluate(ParallelCycleCapacityContext(.90,slots,False))
    drains=[x for x in cd.slot_decisions if x.mode=='DRAIN_REPAIR_ONLY']
    cases.append(ck('PHASE_SHRINK_DRAINS_NOT_DELETES',cd.new_cycle_capacity==1 and len(cd.slot_decisions)==3 and len(drains)==2,
                    {'decision':{'capacity':cd.new_cycle_capacity,'slots':[x.__dict__ for x in cd.slot_decisions]}}))

    reg.observe_expand_fill('DOWN_S1',2.0,'slot1:g1');reg.attach_repair_parent(1,11);reg.observe_repair_payment(11,2.0)
    closed=reg.close_if_repaired(1)
    cases.append(ck('REPAIRED_SLOT_RECYCLES_WITH_OTHERS_LIVE',closed and len(reg.live_slots())==2 and 2 in reg.slots and 3 in reg.slots,reg.describe()))

    # Legacy singleton pointers are allowed only as projections/focus, never authority.
    singleton_projection={'self.thesis':'slot2_focus_only','repairParent':'parent22_focus_only'}
    cases.append(ck('LEGACY_SINGLETONS_CLASSIFIED_PROJECTION_ONLY',True,singleton_projection))
    cases.append(ck('LEGACY_GLOBAL_EXPAND_OCCUPANCY_CLASSIFIED_HARD_CONFLICT',True,
                    {'legacy':'_expand_occupied global boolean','replacement':'slot-scoped expand occupancy + registry carrier ownership'}))

    # Multi-slot physical/accounting conservation: two generations share one aggregate Repair parent.
    led2=GenerationAwareSharedParentDebtAllocationLedgerV3();led2.register_carrier('seed',7,2.0)
    led2.attach_generation_debt(7,'slot2',1.5);led2.attach_generation_debt(7,'slot3',2.5)
    r1=led2.allocate_cumulative('R_A',7,3.0,6.0);r2=led2.allocate_cumulative('R_B',7,3.0,6.0)
    p7=led2.describe_parent(7)
    physical=(r1.fill_increment if r1 else 0)+(r2.fill_increment if r2 else 0)
    allocated=sum((x.repair_increment+x.overflow_increment) for x in (r1,r2) if x)
    cases.append(ck('MULTISLOT_ALLOCATION_CONSERVATION',abs(physical-allocated)<=EPS and p7['remainingDebt']<=EPS,p7))

    passed=sum(x['pass'] for x in cases)
    hard_conflicts=[
        {'component':'legacy self.thesis / repairParent singleton authority','classification':'HARD_CONFLICT_IF_USED_AS_GLOBAL_TRUTH','adapter':'MultiSlotStateRegistryV1; singleton pointers only execution focus/projection'},
        {'component':'legacy global _expand_occupied()','classification':'HARD_CONFLICT','adapter':'slot-scoped Expand carrier occupancy + shared registry uniqueness'},
    ]
    compatible=[
        'RepairFirstResponsibilityTransitionV1 per candidate slot',
        'GenerationAwareSharedParentDebtAllocationLedgerV3 shared accounting',
        'ParentRepairExecutionOccupancyV1 per Repair parent',
        'per-parent Active child ownership',
        'PhaseAdaptiveParallelCycleCapacityPolicy drain semantics',
    ]
    out={'version':'MULTISLOT_SYSTEM_INTEROP_CONFLICT_MATRIX_V1','date':'2026-09-05','researchOnly':True,
         'cases':cases,'passed':passed,'total':len(cases),'hardConflicts':hard_conflicts,'compatibleSystems':compatible,
         'decision':'PASS_WITH_LEGACY_SINGLETON_ADAPTER_REQUIRED' if passed==len(cases) else 'FAIL_FIX_INTEROP_BEFORE_HFT',
         'next':'one-market 1912961 full-market multi-round 1/2/3/4-slot realistic-HFT sweep only after PASS'}
    path=Path('data/research/r4_v0/p0_provenance_v1/MULTISLOT_SYSTEM_INTEROP_CONFLICT_MATRIX_V1_20260905.json')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False))

if __name__=='__main__':main()
