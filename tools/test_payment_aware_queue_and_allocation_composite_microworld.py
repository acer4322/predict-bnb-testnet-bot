from __future__ import annotations
from tools.eth_repair_modular.payment_aware_queue_lease import (
    PaymentAwareQueueLeaseContext, PaymentAwareRepairQueueLeasePolicyV1,
)
from tools.eth_repair_modular.parallel_repair_execution_budget import ParallelRepairExecutionContext
from tools.eth_repair_modular.allocation_aware_parallel_repair_execution_budget import AllocationAwareCompositeParallelRepairPolicyV2


def qctx(**kw):
    d=dict(objective_role='REPAIR',parent_id=1,age_ms=5000,base_ttl_ms=5000,parent_paid_qty=0.0,actual_filled_qty=0.0,terminal_confirmed=False,queue_progress_events=2)
    d.update(kw); return PaymentAwareQueueLeaseContext(**d)

def pctx(**kw):
    d=dict(t=1,seconds_left=250.0,parent_id=1,parent_side='DOWN',same_parent_debt=1.923076923076923,epoch_attached_debt=1.923076923076923,live_ask=0.49,economic_ceiling=0.50,floor_before=-1.0,floor_after_epoch_slice_at_live_ask=-0.0192307692307692,passive_live=False,payment_progress_since_epoch=False,active_already_owned=False,hard_confirmed=False,passive_reserved_qty=0.0,other_same_parent_reserved_qty=0.0)
    d.update(kw); return ParallelRepairExecutionContext(**d)

def main():
    qp=PaymentAwareRepairQueueLeasePolicyV1(); ap=AllocationAwareCompositeParallelRepairPolicyV2(); checks=[]
    def ck(name,ok): checks.append((name,bool(ok)))
    ck('queue_zero_payment_at_base_ttl_revokes',qp.evaluate(qctx()).revoke_progress_extension)
    ck('queue_before_ttl_keeps',not qp.evaluate(qctx(age_ms=4999)).revoke_progress_extension)
    ck('queue_payment_keeps',not qp.evaluate(qctx(parent_paid_qty=.1)).revoke_progress_extension)
    ck('queue_fill_keeps',not qp.evaluate(qctx(actual_filled_qty=.1)).revoke_progress_extension)
    ck('queue_nonrepair_keeps',not qp.evaluate(qctx(objective_role='EXPAND')).revoke_progress_extension)
    ck('queue_no_progress_no_special_revoke',not qp.evaluate(qctx(queue_progress_events=0)).revoke_progress_extension)
    a=ap.evaluate(pctx()); ck('composite_min_legal_allowed',a.allow_active_parallel_child and a.reason=='ALLOW_MIN_LEGAL_ACTIVE_COMPOSITE_OVERFLOW' and a.physical_qty>1.923)
    ck('composite_sibling_reservation_blocks',not ap.evaluate(pctx(passive_reserved_qty=.1)).allow_active_parallel_child)
    ck('composite_payment_progress_blocks',not ap.evaluate(pctx(payment_progress_since_epoch=True)).allow_active_parallel_child)
    ck('composite_above_ceiling_blocks',not ap.evaluate(pctx(live_ask=.51)).allow_active_parallel_child)
    ck('composite_late_blocks',not ap.evaluate(pctx(seconds_left=180)).allow_active_parallel_child)
    ck('composite_epoch_must_own_debt',not ap.evaluate(pctx(epoch_attached_debt=1.0)).allow_active_parallel_child)
    ck('composite_floor_damage_blocks',not ap.evaluate(pctx(floor_before=-.01,floor_after_epoch_slice_at_live_ask=-.005)).allow_active_parallel_child)
    bad=[n for n,ok in checks if not ok]
    print({'cases':len(checks),'passed':len(checks)-len(bad),'failed':bad})
    raise SystemExit(1 if bad else 0)

if __name__=='__main__': main()
