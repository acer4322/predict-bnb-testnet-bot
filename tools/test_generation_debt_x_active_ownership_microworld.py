from __future__ import annotations
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
from tools.eth_repair_modular.generation_scoped_active_ownership import GenerationActiveOwnershipContext,GenerationScopedActiveOwnershipPolicyV1

def main():
 c=[]
 def ck(n,x):c.append((n,bool(x)))
 l=GenerationAwareSharedParentDebtAllocationLedgerV3();l.register_carrier('OLD',1,1.923076923076923);a=l.allocate_cumulative('OLD',1,1.6666666666666667,1.923076923076923)
 ck('old_payment_applied',a is not None and abs(l.remaining(1)-.25641025641025617)<1e-8)
 z=l.attach_generation_debt(1,'UP_6',2.5)
 ck('generation_adds_without_reset_paid',z.applied and abs(z.remaining_debt_after-2.7564102564102564)<1e-8 and abs(l.parents[1].repair_paid-1.6666666666666667)<1e-8)
 z2=l.attach_generation_debt(1,'UP_6',2.5);ck('generation_attach_idempotent',not z2.applied and abs(l.parents[1].remaining_debt-2.7564102564102564)<1e-8)
 ck('initial_debt_aggregate',abs(l.parents[1].initial_debt-4.423076923076923)<1e-8)
 ar=l.allocate_cumulative('NEW',1,1.0,2.7564102564102564);ck('new_fill_pays_aggregate_remaining',ar is not None and abs(ar.repair_increment-1.0)<1e-9 and abs(l.remaining(1)-1.7564102564102564)<1e-8)
 missing=GenerationAwareSharedParentDebtAllocationLedgerV3().attach_generation_debt(9,'X',1.0);ck('missing_parent_does_not_invent_base',not missing.applied and missing.reason=='PARENT_NOT_REGISTERED')
 p=GenerationScopedActiveOwnershipPolicyV1()
 def ctx(**kw):
  d=dict(parent_id=1,new_epoch=2,old_active_key='DOWN_5',old_active_live=False,old_active_terminal_confirmed=True,old_active_actual_filled=1.6666666666666667,paid_at_new_epoch=1.6666666666666667);d.update(kw);return GenerationActiveOwnershipContext(**d)
 ck('terminal_paid_old_active_archives',p.evaluate(ctx()).allow_archive_old_ownership)
 ck('live_old_active_blocks',not p.evaluate(ctx(old_active_live=True)).allow_archive_old_ownership)
 ck('not_terminal_blocks',not p.evaluate(ctx(old_active_terminal_confirmed=False)).allow_archive_old_ownership)
 ck('unaccounted_fill_blocks',not p.evaluate(ctx(paid_at_new_epoch=1.0)).allow_archive_old_ownership)
 ck('no_old_owner_no_archive',not p.evaluate(ctx(old_active_key=None)).allow_archive_old_ownership)
 bad=[n for n,x in c if not x];print({'cases':len(c),'passed':len(c)-len(bad),'failed':bad});raise SystemExit(1 if bad else 0)
if __name__=='__main__':main()
