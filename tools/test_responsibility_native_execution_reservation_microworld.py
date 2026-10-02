from __future__ import annotations
import json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.allocation_ledger_v2 import SharedParentDebtAllocationLedgerV2
from tools.eth_repair_modular.responsibility_execution_reservation import ResponsibilityExecutionReservationLedgerV1

EPS=1e-9


def ck(name, cond, detail=None):
    return {"case":name,"ok":bool(cond),"detail":detail}


def main():
    rows=[]

    # Case 1: Active child can reserve directly from economic responsibility
    # before any passive carrier exists.
    a=SharedParentDebtAllocationLedgerV2(); r=ResponsibilityExecutionReservationLedgerV1(); pid=1; debt=2.5
    a.register_carrier("ACTIVE_1",pid,debt)
    ok=r.reserve("ACTIVE_1",pid,"ACTIVE_REPAIR",1.5,a.remaining(pid,debt))
    rows.append(ck("active_before_passive_can_reserve",ok and abs(r.reserved_total(pid)-1.5)<=EPS,r.describe_parent(pid,a.remaining(pid,debt))))

    # Case 2: a later passive sibling cannot double-spend the already-reserved quantity.
    bad=r.reserve("PASSIVE_TOO_BIG",pid,"PASSIVE_REPAIR",1.1,a.remaining(pid,debt))
    good=r.reserve("PASSIVE_1",pid,"PASSIVE_REPAIR",1.0,a.remaining(pid,debt))
    rows.append(ck("later_passive_respects_active_reservation",(not bad) and good and abs(r.reserved_total(pid)-2.5)<=EPS,r.describe_parent(pid,a.remaining(pid,debt))))

    # Case 3: confirmed Active fill is allocated economically by AllocationLedger V2,
    # while the reservation ledger only releases the corresponding commitment.
    ar=a.allocate_cumulative("ACTIVE_1",pid,1.5,debt)
    rel=r.observe_cumulative_fill("ACTIVE_1",1.5)
    rows.append(ck("active_fill_commits_debt_and_releases_reservation",
        ar is not None and abs(ar.repair_increment-1.5)<=EPS and abs(a.remaining(pid)-1.0)<=EPS and abs(rel-1.5)<=EPS and abs(r.reserved_total(pid)-1.0)<=EPS,
        {"allocation": ar.__dict__ if ar else None,"reservation":r.describe_parent(pid,a.remaining(pid))}))

    # Case 4: after Active fill, the passive sibling already owns exactly the remaining debt;
    # a third sibling must be blocked until capacity is released.
    third=r.reserve("PASSIVE_2",pid,"PASSIVE_REPAIR",0.01,a.remaining(pid))
    rows.append(ck("post_fill_other_sibling_cannot_overreserve",not third,r.describe_parent(pid,a.remaining(pid))))

    # Case 5: passive terminal/cancel releases unused reservation and capacity becomes reusable.
    released=r.release_terminal("PASSIVE_1","CANCEL_CONFIRMED")
    reused=r.reserve("PASSIVE_2",pid,"PASSIVE_REPAIR",1.0,a.remaining(pid))
    rows.append(ck("cancel_releases_unused_reservation",abs(released-1.0)<=EPS and reused and abs(r.reserved_total(pid)-1.0)<=EPS,r.describe_parent(pid,a.remaining(pid))))

    # Case 6: late fill of a cancelled carrier is economically visible to AllocationLedger,
    # but reservation accounting never creates negative capacity or double releases.
    late=a.allocate_cumulative("PASSIVE_1",pid,0.4,debt)
    late_rel=r.observe_cumulative_fill("PASSIVE_1",0.4)
    desc=r.describe_parent(pid,a.remaining(pid))
    # Existing PASSIVE_2 reservation is now larger than authoritative debt due to late fill;
    # this must be detectable so runtime can cancel/resize sibling before new commitment.
    rows.append(ck("late_fill_after_cancel_detects_reservation_over_debt",
        late is not None and abs(late.repair_increment-0.4)<=EPS and abs(late_rel)<=EPS and desc["reservationOverDebt"]>0.399999,
        {"allocation":late.__dict__ if late else None,"reservation":desc}))

    # Case 7: parent-terminal release clears all prospective child commitments.
    pr=r.release_parent_terminal(pid,"RESPONSIBILITY_COMPLETE")
    rows.append(ck("parent_completion_releases_all_children",pr>0 and r.reserved_total(pid)<=EPS,r.describe_parent(pid,a.remaining(pid))))

    # Case 8: independent parent budgets never cross-spend.
    r2=ResponsibilityExecutionReservationLedgerV1()
    p1=r2.reserve("A",1,"ACTIVE_REPAIR",1.0,1.0)
    p2=r2.reserve("B",2,"PASSIVE_REPAIR",2.0,2.0)
    cross=r2.reserve("C",1,"PASSIVE_REPAIR",0.1,1.0)
    rows.append(ck("parent_budgets_are_isolated",p1 and p2 and not cross and abs(r2.reserved_total(1)-1.0)<=EPS and abs(r2.reserved_total(2)-2.0)<=EPS,
        {"p1":r2.describe_parent(1,1.0),"p2":r2.describe_parent(2,2.0)}))

    # Case 9: duplicate reserve calls are idempotent, not cumulative.
    r3=ResponsibilityExecutionReservationLedgerV1(); x1=r3.reserve("X",7,"ACTIVE_REPAIR",0.8,1.5); x2=r3.reserve("X",7,"ACTIVE_REPAIR",0.8,1.5)
    rows.append(ck("duplicate_reserve_is_idempotent",x1 and x2 and abs(r3.reserved_total(7)-0.8)<=EPS,r3.describe_parent(7,1.5)))

    # Case 10: reservation cannot exceed authoritative remaining debt.
    r4=ResponsibilityExecutionReservationLedgerV1(); z=r4.reserve("Z",9,"ACTIVE_REPAIR",1.01,1.0)
    rows.append(ck("reservation_never_exceeds_authoritative_debt",not z and r4.reserved_total(9)<=EPS,r4.describe_parent(9,1.0)))

    passed=sum(x["ok"] for x in rows)
    out={
        "version":"RESPONSIBILITY_NATIVE_EXECUTION_RESERVATION_MICROWORLD_V1",
        "date":"2026-09-04",
        "passed":passed,
        "total":len(rows),
        "functionalPass":passed==len(rows),
        "rows":rows,
        "boundary":[
            "reservation ledger is prospective commitment accounting only",
            "AllocationLedger V2 remains sole confirmed-fill Repair/overflow authority",
            "Active child may reserve before passive carrier exists",
            "sibling reservations share one parent debt budget",
            "cancel/terminal releases unused reservation",
            "late fill after cancel is detected as reservation-over-debt and requires runtime reconciliation",
            "no price/qty/timing policy in microworld"
        ]
    }
    path=Path('data/research/r4_v0/behavior_alignment_v1/RESPONSIBILITY_NATIVE_EXECUTION_RESERVATION_MICROWORLD_V1_RESULT_20260904.json')
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False))
    raise SystemExit(0 if out['functionalPass'] else 1)

if __name__=='__main__': main()
