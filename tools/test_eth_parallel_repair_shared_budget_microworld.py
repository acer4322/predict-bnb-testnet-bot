from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from tools.allocation_ledger_v2 import SharedParentDebtAllocationLedgerV2
from tools.eth_repair_modular.parallel_repair_execution_budget import (
    ParallelRepairExecutionContext,
    SameParentAggregateParallelRepairPolicyV1,
)

EPS=1e-9
PARENT=1
DEBT=2.1649963710093214
ATTACHED=1.6666666666666667
ASK=0.47
CEILING=0.6124560577773237
FLOOR0=-0.8390312285187402
FLOOR_AFTER_ATTACHED=0.04430210481459351


def ctx(**kw):
    base=dict(
        t=1788450027539,seconds_left=272.461,parent_id=PARENT,parent_side='DOWN',
        same_parent_debt=DEBT,epoch_attached_debt=ATTACHED,live_ask=ASK,
        economic_ceiling=CEILING,floor_before=FLOOR0,
        floor_after_epoch_slice_at_live_ask=FLOOR_AFTER_ATTACHED,passive_live=True,
        payment_progress_since_epoch=False,active_already_owned=False,hard_confirmed=False,
        passive_reserved_qty=0.02,other_same_parent_reserved_qty=0.0,
    )
    base.update(kw)
    return ParallelRepairExecutionContext(**base)


def run():
    checks=[]
    pol=SameParentAggregateParallelRepairPolicyV1()
    d=pol.evaluate(ctx())
    checks.append(('policy_allows_min_legal_when_unreserved_capacity_sufficient', d.allow_active_parallel_child and abs(d.physical_qty-(1/ASK))<1e-9))
    checks.append(('policy_tracks_reservation_and_capacity', abs(d.reserved_qty-.02)<EPS and abs(d.unreserved_repair_capacity-(DEBT-.02))<EPS))
    checks.append(('policy_floor_lower_bound_non_damaging', d.conservative_floor_lower_bound is not None and d.conservative_floor_lower_bound>=FLOOR0-EPS))

    blocked=pol.evaluate(ctx(passive_reserved_qty=.50))
    checks.append(('large_passive_reservation_blocks_active', not blocked.allow_active_parallel_child and blocked.reason=='INSUFFICIENT_UNRESERVED_PARENT_CAPACITY'))
    blocked2=pol.evaluate(ctx(passive_reserved_qty=.02,other_same_parent_reserved_qty=.50))
    checks.append(('other_sibling_reservation_also_blocks_active', not blocked2.allow_active_parallel_child and blocked2.reason=='INSUFFICIENT_UNRESERVED_PARENT_CAPACITY'))
    released=pol.evaluate(ctx(passive_live=False,passive_reserved_qty=0.0))
    checks.append(('released_passive_restores_capacity', released.allow_active_parallel_child and released.unreserved_repair_capacity>=released.physical_qty-EPS))

    led=SharedParentDebtAllocationLedgerV2()
    a=led.allocate_cumulative('ACTIVE',PARENT,d.physical_qty,DEBT)
    p=led.allocate_cumulative('PASSIVE',PARENT,0.50,DEBT)
    st=led.describe_parent(PARENT)
    checks.append(('active_first_repair_never_exceeds_parent_debt', st['repairPaid']<=DEBT+EPS))
    checks.append(('active_first_physical_conservation', abs((a.fill_increment+p.fill_increment)-(a.repair_increment+a.overflow_increment+p.repair_increment+p.overflow_increment))<1e-9))
    checks.append(('unexpected_late_passive_excess_becomes_explicit_overflow', p.overflow_increment>0 and st['remainingDebt']<=EPS))

    led=SharedParentDebtAllocationLedgerV2()
    p=led.allocate_cumulative('PASSIVE',PARENT,0.80,DEBT)
    a=led.allocate_cumulative('ACTIVE',PARENT,d.physical_qty,DEBT)
    st=led.describe_parent(PARENT)
    checks.append(('passive_first_repair_never_exceeds_parent_debt', st['repairPaid']<=DEBT+EPS))
    checks.append(('passive_first_active_excess_is_explicit_overflow', a.overflow_increment>0 and st['remainingDebt']<=EPS))

    led=SharedParentDebtAllocationLedgerV2()
    seq=[('PASSIVE',0.4),('ACTIVE',0.7),('PASSIVE',1.0),('ACTIVE',1.4),('ACTIVE',d.physical_qty)]
    total_phys=0.0; total_rep=0.0; total_ov=0.0
    for key,cum in seq:
        r=led.allocate_cumulative(key,PARENT,cum,DEBT)
        if r is not None:
            total_phys+=r.fill_increment; total_rep+=r.repair_increment; total_ov+=r.overflow_increment
    st=led.describe_parent(PARENT)
    checks.append(('interleaved_exact_conservation', abs(total_phys-total_rep-total_ov)<1e-9))
    checks.append(('interleaved_repair_capped_once', abs(st['repairPaid']-DEBT)<1e-9))
    before=len(led.allocations)
    dup=led.allocate_cumulative('ACTIVE',PARENT,d.physical_qty,DEBT)
    checks.append(('duplicate_fill_idempotent', dup is None and len(led.allocations)==before))

    d2=pol.evaluate(ctx(payment_progress_since_epoch=True))
    checks.append(('payment_progress_suppresses_new_active_submit', not d2.allow_active_parallel_child and d2.reason=='PAYMENT_PROGRESS_REASSESS_SHARED_BUDGET'))
    d3=pol.evaluate(ctx(active_already_owned=True))
    checks.append(('active_ownership_suppresses_second_active', not d3.allow_active_parallel_child and d3.reason=='ACTIVE_ALREADY_OWNED'))
    d4=pol.evaluate(ctx(seconds_left=170.0))
    checks.append(('late_speculative_active_fence_preserved', not d4.allow_active_parallel_child and d4.reason=='LATE_NO_NEW_ACTIVE_EXPOSURE'))

    failed=[name for name,ok in checks if not ok]
    print({'passed':len(checks)-len(failed),'total':len(checks),'failed':failed,'decision':'PASS_SHARED_BUDGET_MICROWORLD' if not failed else 'FAIL_SHARED_BUDGET_MICROWORLD','policyDecision':d,'blockedDecision':blocked})
    if failed: raise SystemExit(1)

if __name__=='__main__': run()
