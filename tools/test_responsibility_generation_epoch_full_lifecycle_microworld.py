from __future__ import annotations
import json
from pathlib import Path
from tools.eth_repair_modular.responsibility_generation_epoch import ResponsibilityGenerationEpochState


def row(name, cond, **extra):
    return {"case": name, "ok": bool(cond), **extra}


def main():
    rows=[]
    s=ResponsibilityGenerationEpochState(parent_id=1,parent_side="UP")
    ep=s.start_new_parent_responsibility(initial_debt=2.0,parent_fill_now=0.4,churn_now=3,paid_total_now=0.4)
    o=s.observe(parent_fill_now=0.4,churn_now=3,paid_total_now=0.4)
    rows.append(row("new_parent_starts_epoch", ep==1 and s.armed and s.parent_id==1 and s.attached_debt==2.0, state=o))
    rows.append(row("prebirth_churn_not_reused", o["postEpochChurn"]==0 and not s.hard_active_evidence(parent_fill_now=.4,churn_now=3,paid_total_now=.4,required_disconnects=1), state=o))
    rows.append(row("postbirth_churn_can_arm_evidence", s.hard_active_evidence(parent_fill_now=.4,churn_now=4,paid_total_now=.4,required_disconnects=1)))
    rows.append(row("payment_progress_blocks_escalation", not s.hard_active_evidence(parent_fill_now=.5,churn_now=5,paid_total_now=.5,required_disconnects=1), state=s.observe(parent_fill_now=.5,churn_now=5,paid_total_now=.5)))
    duplicate_rejected=False
    try:
        s.start_new_parent_responsibility(initial_debt=1.0,parent_fill_now=.5,churn_now=5,paid_total_now=.5)
    except ValueError:
        duplicate_rejected=True
    rows.append(row("duplicate_new_parent_birth_cannot_reset_evidence", duplicate_rejected and s.epoch==1))
    ep2=s.attach_existing_parent_responsibility(added_debt=.7,parent_fill_now=.5,churn_now=5,paid_total_now=.5)
    o2=s.observe(parent_fill_now=.5,churn_now=5,paid_total_now=.5)
    rows.append(row("same_parent_attachment_increments_epoch", ep2==2 and s.parent_id==1 and s.parent_side=="UP" and s.attached_debt==.7, state=o2))
    rows.append(row("attachment_rebases_old_evidence", o2["postEpochChurn"]==0 and not o2["paymentProgress"]))
    s.active_owned=True
    rows.append(row("active_ownership_blocks_escalation", not s.hard_active_evidence(parent_fill_now=.5,churn_now=6,paid_total_now=.5,required_disconnects=1)))
    s.active_owned=False
    rows.append(row("new_epoch_postchurn_reachable_after_release", s.hard_active_evidence(parent_fill_now=.5,churn_now=6,paid_total_now=.5,required_disconnects=1)))
    bad_debt=False
    z=ResponsibilityGenerationEpochState(parent_id=2,parent_side="DOWN")
    try:
        z.start_new_parent_responsibility(initial_debt=0.0,parent_fill_now=0,churn_now=0,paid_total_now=0)
    except ValueError:
        bad_debt=True
    rows.append(row("zero_debt_cannot_birth_epoch", bad_debt and z.epoch==0 and not z.armed))
    out={
        "version":"RESPONSIBILITY_GENERATION_EPOCH_FULL_LIFECYCLE_MICROWORLD_V1",
        "date":"2026-09-04",
        "passed":sum(int(x["ok"]) for x in rows),
        "total":len(rows),
        "functionalPass":all(x["ok"] for x in rows),
        "rows":rows,
        "boundary":[
            "execution epoch starts at authoritative Repair parent birth",
            "same physical parent keeps identity across later responsibility generations",
            "pre-generation fill/churn/payment evidence cannot be reused",
            "payment progress and Active ownership block new escalation",
            "no execution/router/price/qty mutation in this microworld"
        ]
    }
    outp=Path("data/research/r4_v0/behavior_alignment_v1/RESPONSIBILITY_GENERATION_EPOCH_FULL_LIFECYCLE_MICROWORLD_V1_RESULT_20260904.json")
    outp.parent.mkdir(parents=True,exist_ok=True)
    outp.write_text(json.dumps(out,indent=2),encoding="utf-8")
    print(json.dumps({"ok":out["functionalPass"],"passed":out["passed"],"total":out["total"]}))

if __name__=="__main__":
    main()
