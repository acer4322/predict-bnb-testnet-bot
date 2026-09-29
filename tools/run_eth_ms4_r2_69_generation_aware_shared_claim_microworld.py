from __future__ import annotations
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.eth_repair_modular.generation_aware_allocation_ledger import GenerationAwareSharedParentDebtAllocationLedgerV3
from tools.allocation_ledger_v2 import EPS

def row(name,ok,**kw):return {'case':name,'ok':bool(ok),**kw}

def main():
    rows=[]
    # 1: Exact GPT6 1946784 conflict anatomy. Existing parent debt after multiple risk births.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3(); pid=1
    L.register_carrier('UP_A',pid,4.041859202213719); L.register_carrier('UP_B',pid,4.041859202213719)
    a=L.allocate_cumulative('UP_A',pid,2.0833333333333335,4.041859202213719)
    # residual equals the observed 1.9585258688803855
    b=L.allocate_cumulative('UP_B',pid,2.0,4.041859202213719)
    rows.append(row('gpt6_1946784_sibling_residual_crossing',
        abs(a.debt_after-1.9585258688803855)<1e-9 and
        abs(b.repair_increment-1.9585258688803855)<1e-9 and
        abs(b.overflow_increment-0.04147413111961451)<1e-9 and b.debt_after<=EPS,
        first=a.__dict__,second=b.__dict__,parent=L.describe_parent(pid)))

    # 2: New same-parent generation risk debt is attached exactly once, never resets paid debt.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();L.register_carrier('P',2,2.0)
    x=L.allocate_cumulative('P',2,1.25,2.0); before=L.describe_parent(2)
    at=L.attach_generation_debt(2,'RISK_X',1.5);dup=L.attach_generation_debt(2,'RISK_X',1.5);after=L.describe_parent(2)
    rows.append(row('generation_debt_attach_after_partial_payment',
        abs(before['remainingDebt']-.75)<EPS and at.applied and abs(after['remainingDebt']-2.25)<EPS and
        abs(after['initialDebt']-3.5)<EPS and not dup.applied and dup.reason=='IDEMPOTENT_ALREADY_ATTACHED',
        before=before,attach=at.__dict__,duplicate=dup.__dict__,after=after))

    # 3: Siblings can pay debt added after they were registered.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();L.register_carrier('P',3,1.0);L.register_carrier('A',3,1.0)
    L.allocate_cumulative('P',3,.8,1.0);L.attach_generation_debt(3,'NEW_RISK',1.4)
    a=L.allocate_cumulative('A',3,1.0,1.0);st=L.describe_parent(3)
    rows.append(row('old_sibling_can_pay_new_attached_debt',abs(a.repair_increment-1.0)<EPS and abs(st['remainingDebt']-.6)<EPS,result=a.__dict__,parent=st))

    # 4: Partial cumulative fill is idempotent under attached debt.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();L.register_carrier('A',4,1.0);L.attach_generation_debt(4,'R',.5)
    a=L.allocate_cumulative('A',4,.4,1.0);z=L.allocate_cumulative('A',4,.4,1.0);b=L.allocate_cumulative('A',4,1.1,1.0)
    rows.append(row('partial_cumulative_idempotence_with_attached_debt',z is None and abs(a.repair_increment-.4)<EPS and abs(b.fill_increment-.7)<EPS and abs(L.remaining(4)-.4)<EPS,parent=L.describe_parent(4)))

    # 5: Once shared debt is zero, sibling fill is pure overflow; no Repair double ownership.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();L.register_carrier('P',5,1.0);L.register_carrier('A',5,1.0)
    L.allocate_cumulative('P',5,1.0,1.0);a=L.allocate_cumulative('A',5,.75,1.0)
    rows.append(row('post_completion_sibling_is_pure_overflow',a.repair_increment<=EPS and abs(a.overflow_increment-.75)<EPS and L.remaining(5)<=EPS,result=a.__dict__))

    # 6: Fill ordering changes which carrier pays Repair, but aggregate conservation is invariant.
    def ordered(first,second):
        X=GenerationAwareSharedParentDebtAllocationLedgerV3();X.register_carrier('A',6,1.5);X.register_carrier('B',6,1.5)
        ra=X.allocate_cumulative(first,6,1.0,1.5);rb=X.allocate_cumulative(second,6,1.0,1.5)
        return X,ra,rb
    L1,a1,b1=ordered('A','B');L2,a2,b2=ordered('B','A')
    rows.append(row('sibling_fill_order_conserves_aggregate',
        abs(L1.describe_parent(6)['repairPaid']-1.5)<EPS and abs(L2.describe_parent(6)['repairPaid']-1.5)<EPS and
        abs(L1.describe_parent(6)['overflowBorn']-.5)<EPS and abs(L2.describe_parent(6)['overflowBorn']-.5)<EPS,
        orderAB=L1.describe_parent(6),orderBA=L2.describe_parent(6)))

    # 7: Generation attachment itself never creates overflow or payment.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();L.register_carrier('P',7,1.0);at=L.attach_generation_debt(7,'G2',2.0);st=L.describe_parent(7)
    rows.append(row('debt_attachment_is_not_payment_or_overflow',at.applied and abs(st['repairPaid'])<EPS and abs(st['overflowBorn'])<EPS and abs(st['remainingDebt']-3.0)<EPS,parent=st))

    # 8: Zero/negative attach cannot mint debt.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();L.register_carrier('P',8,1.0);a=L.attach_generation_debt(8,'ZERO',0);b=L.attach_generation_debt(8,'NEG',-1)
    rows.append(row('nonpositive_attach_cannot_mint',not a.applied and not b.applied and abs(L.remaining(8)-1.0)<EPS,a=a.__dict__,b=b.__dict__))

    # 9: Parent must exist before debt attachment; no ghost responsibility.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();a=L.attach_generation_debt(9,'G',1.0)
    rows.append(row('no_ghost_parent_on_attachment',not a.applied and a.reason=='PARENT_NOT_REGISTERED' and L.describe_parent(9) is None,result=a.__dict__))

    # 10: Physical conservation for all allocations in a mixed path.
    L=GenerationAwareSharedParentDebtAllocationLedgerV3();L.register_carrier('P',10,1.2);L.register_carrier('A',10,1.2)
    L.allocate_cumulative('P',10,.7,1.2);L.attach_generation_debt(10,'R2',.9);L.allocate_cumulative('A',10,1.0,1.2);L.allocate_cumulative('P',10,1.5,1.2)
    cons=all(abs(x.fill_increment-x.repair_increment-x.overflow_increment)<EPS for x in L.allocations)
    st=L.describe_parent(10)
    rows.append(row('mixed_path_physical_conservation',cons and st['repairPaid']<=st['initialDebt']+EPS and st['remainingDebt']>=-EPS,parent=st,allocations=[x.__dict__ for x in L.allocations]))

    out={'version':'MS4_R2_69_GENERATION_AWARE_SHARED_CLAIM_MICROWORLD_V1','researchOnly':True,
         'passed':sum(x['ok'] for x in rows),'total':len(rows),'functionalPass':all(x['ok'] for x in rows),'rows':rows,
         'boundary':['existing GenerationAwareSharedParentDebtAllocationLedgerV3 reused','no HFT behavior','confirmed fill Repair-first overflow-second','new debt attach idempotent','no pending/live carrier treated as payment','no Target runtime input']}
    op=Path('data/research/r4_v0/p0_provenance_v1/MS4_R269_GENERATION_AWARE_SHARED_CLAIM_MICROWORLD_RESULT_20260906.json')
    op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':out['functionalPass'],'passed':out['passed'],'total':out['total']},ensure_ascii=False))
if __name__=='__main__':main()
