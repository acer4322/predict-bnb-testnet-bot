from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eth_repair_modular.residual_multipayment import (
    ResidualCompositeMultipaymentContext as Context,
    ResidualCompositeMultipaymentRouterPolicyV1 as Policy,
)


OUT = Path(
    "data/research/r4_v0/behavior_alignment_v1/"
    "RESIDUAL_COMPOSITE_MULTIPAYMENT_ROUTER_MICROWORLD_RESULT_20260904.json"
)


def main() -> None:
    base = Context(
        t=1788429664653,
        seconds_left=235.347,
        responsibility_id=4,
        responsibility_side="DOWN",
        authorized_role="REPAIR",
        physical_truth_role="REPAIR",
        prior_confirmed_payments=1,
        authoritative_residual_debt=0.5270413609466456,
        candidate_physical_qty=1.7857142857142856,
        candidate_repair_allocation=0.5270413609466456,
        candidate_overflow_allocation=1.25867292476764,
        shared_remaining_budget=1.7857142857142856,
        ledger_snapshot_current=True,
        pending_sibling_reconciliation=False,
        same_parent_live_carrier=False,
        active_already_owned=False,
        venue_legal=True,
        recursive_current_coordinate_recoverable=True,
    )
    cases = [
        ("1912961_support", base, True, "ALLOW_ONE_RESIDUAL_COMPOSITE_MULTIPAYMENT"),
        ("wrong_authorized_role", replace(base, authorized_role="EXPAND"), False, "AUTHORIZED_ROLE_NOT_REPAIR"),
        ("truth_role_mismatch", replace(base, physical_truth_role="EXPAND"), False, "PHYSICAL_TRUTH_ROLE_NOT_REPAIR"),
        ("no_prior_payment", replace(base, prior_confirmed_payments=0), False, "NO_PRIOR_CONFIRMED_PAYMENT"),
        ("late_overflow", replace(base, seconds_left=180.0), False, "LATE_OVERFLOW_REMAINS_BLOCKED"),
        ("pending_sibling", replace(base, pending_sibling_reconciliation=True), False, "UNRECONCILED_LEDGER_STATE"),
        ("duplicate_live", replace(base, same_parent_live_carrier=True), False, "DUPLICATE_SAME_PARENT_EXECUTION_OWNERSHIP"),
        ("nonconservation", replace(base, candidate_overflow_allocation=1.0), False, "PHYSICAL_ALLOCATION_NONCONSERVATION"),
        ("partial_residual_payment", replace(base, candidate_repair_allocation=0.4, candidate_overflow_allocation=1.3857142857142857), False, "CARRIER_MUST_PAY_ALL_CURRENT_RESIDUAL_FIRST"),
        ("no_overflow", replace(base, candidate_physical_qty=0.5270413609466456, candidate_overflow_allocation=0.0), False, "NOT_A_RESIDUAL_COMPOSITE_CARRIER"),
        ("budget_exceeded", replace(base, shared_remaining_budget=1.7), False, "SHARED_BUDGET_EXCEEDED"),
        ("recovery_unproven", replace(base, recursive_current_coordinate_recoverable=False), False, "RECURSIVE_RECOVERY_NOT_PROVEN_AT_CURRENT_COORDINATE"),
    ]
    policy = Policy()
    rows = []
    for name, context, expected_allow, expected_reason in cases:
        decision = policy.evaluate(context)
        rows.append(
            {
                "case": name,
                "expectedAllow": expected_allow,
                "actualAllow": decision.allow,
                "expectedReason": expected_reason,
                "actualReason": decision.reason,
                "pass": decision.allow == expected_allow and decision.reason == expected_reason,
            }
        )
    gates = {
        "allCasesMatch": all(row["pass"] for row in rows),
        "supportAllowsOnePhysicalVenueMinCarrier": rows[0]["actualAllow"],
        "truthRoleMismatchRejected": rows[2]["pass"],
        "lateOverflowRejected": rows[4]["pass"],
        "unreconciledOrDuplicateRejected": rows[5]["pass"] and rows[6]["pass"],
        "conservationAndBudgetControlsReject": rows[7]["pass"] and rows[10]["pass"],
        "recursiveRecoveryRequired": rows[11]["pass"],
    }
    output = {
        "version": "RESIDUAL_COMPOSITE_MULTIPAYMENT_ROUTER_MICROWORLD_RESULT",
        "date": "2026-09-04",
        "researchOnly": True,
        "runtimeAuthority": False,
        "gates": gates,
        "rows": rows,
        "decision": "PASS_TO_ONE_MARKET_BEHAVIOR_SMOKE" if all(gates.values()) else "REJECT_OR_DIAGNOSE_ROUTER_POLICY",
        "boundary": [
            "pure Router policy micro-world only",
            "AllocationLedger V2 remains external and frozen",
            "no HFT behavior mutation",
            "no Target runtime input",
            "no tuning",
            "no 8781",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(gates.values()) else 1)


if __name__ == "__main__":
    main()
