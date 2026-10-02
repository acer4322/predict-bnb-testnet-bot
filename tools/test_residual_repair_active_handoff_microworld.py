from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path.cwd().resolve() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eth_repair_modular.residual_active_handoff import (
    ResidualRepairActiveHandoffContext,
    ResidualRepairActiveHandoffPolicyV1,
)


def main() -> None:
    policy = ResidualRepairActiveHandoffPolicyV1()
    base = ResidualRepairActiveHandoffContext(
        t=1788429664653,
        seconds_left=235.347,
        responsibility_id=4,
        responsibility_side="DOWN",
        prior_confirmed_payment_qty=1.6129032258064517,
        authoritative_residual_debt=0.5270413609466456,
        physical_truth_role="REPAIR",
        passive_release_confirmed=True,
        passive_live=False,
        same_parent_unresolved_carriers=0,
        active_already_owned=False,
        live_ask=0.58,
        legal_physical_qty=1.7241379310344827,
        candidate_repair_allocation=0.5270413609466456,
        candidate_overflow_allocation=1.197096570087837,
        recursive_current_coordinate_recoverable=True,
    )
    cases = [
        ("support", base, True, "ALLOW_RESIDUAL_REPAIR_ACTIVE_HANDOFF"),
        ("no_prior_payment", replace(base, prior_confirmed_payment_qty=0.0), False, "NO_PRIOR_CONFIRMED_REPAIR_PAYMENT"),
        ("debt_zero", replace(base, authoritative_residual_debt=0.0, candidate_repair_allocation=0.0, candidate_overflow_allocation=base.legal_physical_qty), False, "NO_RESIDUAL_REPAIR_DEBT"),
        ("truth_role_expand", replace(base, physical_truth_role="EXPAND"), False, "PHYSICAL_TRUTH_ROLE_NOT_REPAIR"),
        ("passive_still_live", replace(base, passive_live=True), False, "PASSIVE_CARRIER_STILL_LIVE"),
        ("release_unconfirmed", replace(base, passive_release_confirmed=False), False, "PASSIVE_RELEASE_NOT_CONFIRMED"),
        ("same_parent_unresolved", replace(base, same_parent_unresolved_carriers=1), False, "SAME_PARENT_CARRIER_STILL_UNRESOLVED"),
        ("active_owned", replace(base, active_already_owned=True), False, "ACTIVE_ALREADY_OWNED"),
        ("illegal_qty", replace(base, legal_physical_qty=13.0, candidate_repair_allocation=base.authoritative_residual_debt, candidate_overflow_allocation=13.0-base.authoritative_residual_debt), False, "ILLEGAL_PHYSICAL_SLICE"),
        ("bad_repair_allocation", replace(base, candidate_repair_allocation=0.7, candidate_overflow_allocation=base.legal_physical_qty-0.7), False, "INVALID_REPAIR_FIRST_ALLOCATION"),
        ("nonconserving", replace(base, candidate_overflow_allocation=0.5), False, "PHYSICAL_ALLOCATION_NONCONSERVING"),
        ("late_overflow", replace(base, seconds_left=175.0), False, "LATE_BOUNDARY_FROZEN_FOR_THIS_MODULE"),
        ("unrecoverable_overflow", replace(base, recursive_current_coordinate_recoverable=False), False, "OVERFLOW_RECOVERY_NOT_PROVEN"),
        ("pure_repair_before_180", replace(base, legal_physical_qty=0.4, candidate_repair_allocation=0.4, candidate_overflow_allocation=0.0, recursive_current_coordinate_recoverable=False), True, "ALLOW_RESIDUAL_REPAIR_ACTIVE_HANDOFF"),
    ]
    rows = []
    passed = 0
    for name, ctx, expected_allow, expected_reason in cases:
        decision = policy.evaluate(ctx)
        ok = decision.allow == expected_allow and decision.reason == expected_reason
        passed += int(ok)
        rows.append({"case": name, "ok": ok, "expectedAllow": expected_allow, "expectedReason": expected_reason, "decision": decision.__dict__})
    gates = {
        "allCasesMatch": passed == len(cases),
        "supportAllows": rows[0]["decision"]["allow"],
        "truthRoleMismatchRejected": next(r for r in rows if r["case"] == "truth_role_expand")["ok"],
        "liveOrUnresolvedOwnershipRejected": all(next(r for r in rows if r["case"] == n)["ok"] for n in ("passive_still_live", "same_parent_unresolved", "active_owned")),
        "lateBoundaryPreserved": next(r for r in rows if r["case"] == "late_overflow")["ok"],
        "unrecoverableOverflowRejected": next(r for r in rows if r["case"] == "unrecoverable_overflow")["ok"],
        "allocationConservationEnforced": next(r for r in rows if r["case"] == "nonconserving")["ok"],
    }
    out = {
        "version": "RESIDUAL_REPAIR_ACTIVE_HANDOFF_MICROWORLD_V1",
        "date": "2026-09-04",
        "researchOnly": True,
        "runtimeAuthority": False,
        "module": policy.name,
        "passed": passed,
        "total": len(cases),
        "gates": gates,
        "decision": "PASS_TO_ONE_MARKET_BEHAVIOR_SMOKE" if all(gates.values()) else "REJECT_OR_DIAGNOSE_MICROWORLD",
        "rows": rows,
        "boundary": [
            "single execution-module semantics only",
            "persistent residual Repair identity preserved",
            "physical carrier remains REPAIR while residual debt exists",
            "Repair-first / overflow-second allocation is downstream AllocationLedger authority",
            "no Target runtime input",
            "<=180s new-overflow boundary remains frozen",
            "no HFT behavior mutation",
            "no 8781",
        ],
    }
    path = Path("data/research/r4_v0/behavior_alignment_v1/RESIDUAL_REPAIR_ACTIVE_HANDOFF_MICROWORLD_V1_RESULT_20260904.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"decision": out["decision"], "passed": passed, "total": len(cases), "gates": gates}, ensure_ascii=False))


if __name__ == "__main__":
    main()
