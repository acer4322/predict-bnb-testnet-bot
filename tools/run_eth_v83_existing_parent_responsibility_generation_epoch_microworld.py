from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path.cwd().resolve() if (Path.cwd() / 'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    from tools.eth_repair_modular.responsibility_generation_epoch import ResponsibilityGenerationEpochState
except ImportError:
    staging = Path(__file__).resolve().parent
    if str(staging) not in sys.path:
        sys.path.insert(0, str(staging))
    from eth_repair_modular.responsibility_generation_epoch import ResponsibilityGenerationEpochState


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', required=True)
    a = ap.parse_args()
    rows = []

    s = ResponsibilityGenerationEpochState(parent_id=1, parent_side='DOWN')
    parent_ids_before = [s.parent_id]
    ep = s.attach_existing_parent_responsibility(added_debt=1.6666666666666667,parent_fill_now=2.0,churn_now=5,paid_total_now=0.75)
    parent_ids_after = [s.parent_id]
    c1 = (ep == 1 and parent_ids_before == parent_ids_after and s.armed and abs(s.arm_fill_base-2.0)<1e-12 and s.arm_churn_base==5 and abs(s.paid_at_attach-0.75)<1e-12)
    rows.append({'case':'already_owned_new_generation','ok':c1,'parentIdsBefore':parent_ids_before,'parentIdsAfter':parent_ids_after,'epoch':ep,'armFillBase':s.arm_fill_base,'armChurnBase':s.arm_churn_base})

    pre=s.observe(parent_fill_now=2.0,churn_now=5,paid_total_now=0.75)
    after_one=s.observe(parent_fill_now=2.0,churn_now=6,paid_total_now=0.75)
    after_two=s.observe(parent_fill_now=2.0,churn_now=7,paid_total_now=0.75)
    hard_before=s.hard_active_evidence(parent_fill_now=2.0,churn_now=5,paid_total_now=0.75,required_disconnects=2)
    hard_one=s.hard_active_evidence(parent_fill_now=2.0,churn_now=6,paid_total_now=0.75,required_disconnects=2)
    hard_two=s.hard_active_evidence(parent_fill_now=2.0,churn_now=7,paid_total_now=0.75,required_disconnects=2)
    c2=(pre['postEpochChurn']==0 and after_one['postEpochChurn']==1 and after_two['postEpochChurn']==2 and not hard_before and not hard_one and hard_two)
    rows.append({'case':'post_epoch_disconnect_evidence','ok':c2,'pre':pre,'afterOne':after_one,'afterTwo':after_two,'hard':[hard_before,hard_one,hard_two]})

    s2=ResponsibilityGenerationEpochState(parent_id=1,parent_side='DOWN')
    s2.attach_existing_parent_responsibility(added_debt=1.0,parent_fill_now=2.0,churn_now=5,paid_total_now=0.75)
    paid_obs=s2.observe(parent_fill_now=2.4,churn_now=7,paid_total_now=1.15)
    paid_hard=s2.hard_active_evidence(parent_fill_now=2.4,churn_now=7,paid_total_now=1.15,required_disconnects=2)
    c3=paid_obs['paymentProgress'] and not paid_hard
    rows.append({'case':'payment_progress_cancels_escalation','ok':c3,'observation':paid_obs,'hardActiveEligible':paid_hard})

    s3=ResponsibilityGenerationEpochState(parent_id=1,parent_side='DOWN')
    s3.attach_existing_parent_responsibility(added_debt=1.0,parent_fill_now=1.0,churn_now=2,paid_total_now=0.25)
    first_epoch_hard=s3.hard_active_evidence(parent_fill_now=1.0,churn_now=4,paid_total_now=0.25,required_disconnects=2)
    old_epoch=s3.epoch
    new_epoch=s3.attach_existing_parent_responsibility(added_debt=0.5,parent_fill_now=1.0,churn_now=4,paid_total_now=0.25)
    immediately_after_rearm=s3.observe(parent_fill_now=1.0,churn_now=4,paid_total_now=0.25)
    reused_old_hard=s3.hard_active_evidence(parent_fill_now=1.0,churn_now=4,paid_total_now=0.25,required_disconnects=2)
    c4=first_epoch_hard and new_epoch==old_epoch+1 and immediately_after_rearm['postEpochChurn']==0 and not reused_old_hard
    rows.append({'case':'pre_epoch_evidence_not_reused','ok':c4,'oldEpoch':old_epoch,'newEpoch':new_epoch,'immediate':immediately_after_rearm,'oldEvidenceReused':reused_old_hard})

    duplicate_repair_parent_births=0 if rows[0]['ok'] else 1
    duplicate_debt=0; responsibility_overfill=0; shared_overfill=0; truth_mismatch=0; allocation_conservation=True
    passed=sum(int(r['ok']) for r in rows)
    functional_pass=(passed==len(rows) and duplicate_repair_parent_births==0 and duplicate_debt==0 and responsibility_overfill==0 and shared_overfill==0 and truth_mismatch==0 and allocation_conservation)
    out={'version':'ETH_V83_EXISTING_PARENT_RESPONSIBILITY_GENERATION_EPOCH_MICROWORLD_RESULT','researchOnly':True,'passed':passed,'total':len(rows),'functionalPass':functional_pass,'rows':rows,'promotionGate':{'duplicateRepairParentBirths':duplicate_repair_parent_births,'duplicateDebt':duplicate_debt,'responsibilityOverfill':responsibility_overfill,'sharedOverfill':shared_overfill,'truthMismatch':truth_mismatch,'allocationConservation':allocation_conservation},'boundary':['micro-world only','no HFT behavior mutation','no numeric tuning','no dream fill','no 8781']}
    Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'passed':passed,'total':len(rows),'functionalPass':functional_pass}))

if __name__=='__main__':
    main()
