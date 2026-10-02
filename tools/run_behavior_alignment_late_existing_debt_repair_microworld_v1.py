from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.eth_repair_modular.late_existing_debt_repair import (
    LateExistingDebtRepairContext,
    LateExistingDebtRepairFencePolicyV1,
)

OUTPUT = (
    ROOT
    / "data/research/r4_v0/behavior_alignment_v1/"
    "LATE_EXISTING_DEBT_REPAIR_FENCE_MICROWORLD_V1_RESULT_20260904.json"
)


def run() -> dict:
    policy = LateExistingDebtRepairFencePolicyV1()
    valid = LateExistingDebtRepairContext(
        seconds_left=120.0,
        responsibility_id=7,
        responsibility_role="REPAIR",
        authoritative_existing_debt=2.5,
        candidate_physical_qty=1.5,
        candidate_repair_allocation=1.5,
        candidate_overflow=0.0,
        shared_remaining_budget=2.5,
        venue_legal=True,
        economic_repair_price_legal=True,
        reduces_existing_downside=True,
        ledger_snapshot_current=True,
        pending_sibling_fill_reconciliation=False,
        active_already_owned=False,
        would_birth_new_responsibility=False,
    )
    cases = [
        ("valid_late_existing_debt_payment", valid, True, False, "ALLOW_LATE_EXISTING_DEBT_PAYMENT_ONLY"),
        ("exact_180_boundary_is_late", replace(valid, seconds_left=180.0), True, False, "ALLOW_LATE_EXISTING_DEBT_PAYMENT_ONLY"),
        ("pre_180_delegates_default", replace(valid, seconds_left=180.001), False, True, "NOT_LATE_DELEGATE_TO_DEFAULT_ROUTER"),
        ("expand_role_blocked", replace(valid, responsibility_role="EXPAND"), False, False, "LATE_NON_REPAIR_REMAINS_BLOCKED"),
        ("missing_responsibility_blocked", replace(valid, responsibility_id=None), False, False, "NO_AUTHORITATIVE_REPAIR_RESPONSIBILITY"),
        ("negative_overflow_blocked", replace(valid, candidate_overflow=-0.1), False, False, "NEGATIVE_REPAIR_QUANTITY"),
        ("nonfinite_qty_blocked", replace(valid, candidate_physical_qty=float("inf")), False, False, "NONFINITE_REPAIR_QUANTITY"),
        ("zero_debt_blocked", replace(valid, authoritative_existing_debt=0.0), False, False, "NO_EXISTING_REPAIR_DEBT"),
        ("stale_ledger_blocked", replace(valid, ledger_snapshot_current=False), False, False, "UNRECONCILED_SHARED_LEDGER_STATE"),
        ("pending_sibling_fill_blocked", replace(valid, pending_sibling_fill_reconciliation=True), False, False, "UNRECONCILED_SHARED_LEDGER_STATE"),
        ("duplicate_active_owner_blocked", replace(valid, active_already_owned=True), False, False, "ACTIVE_ALREADY_OWNED"),
        ("allocation_nonconservation_blocked", replace(valid, candidate_repair_allocation=1.4), False, False, "PHYSICAL_ALLOCATION_NONCONSERVATION"),
        ("repair_over_debt_blocked", replace(valid, authoritative_existing_debt=1.0), False, False, "REPAIR_ALLOCATION_EXCEEDS_EXISTING_DEBT"),
        ("shared_budget_exceeded_blocked", replace(valid, shared_remaining_budget=1.0), False, False, "SHARED_RESPONSIBILITY_BUDGET_EXCEEDED"),
        (
            "physical_overflow_blocked",
            replace(
                valid,
                authoritative_existing_debt=1.0,
                candidate_physical_qty=1.5,
                candidate_repair_allocation=1.0,
                candidate_overflow=0.5,
            ),
            False,
            False,
            "LATE_OVERFLOW_OR_NEW_EXPOSURE_BLOCKED",
        ),
        ("new_responsibility_birth_blocked", replace(valid, would_birth_new_responsibility=True), False, False, "LATE_OVERFLOW_OR_NEW_EXPOSURE_BLOCKED"),
        ("venue_illegal_blocked", replace(valid, venue_legal=False), False, False, "VENUE_ILLEGAL_REPAIR_CARRIER"),
        ("price_illegal_blocked", replace(valid, economic_repair_price_legal=False), False, False, "REPAIR_PRICE_OUTSIDE_INHERITED_ENVELOPE"),
        ("non_reducing_payment_blocked", replace(valid, reduces_existing_downside=False), False, False, "DOES_NOT_REDUCE_EXISTING_DOWNSIDE"),
        (
            "reconciled_sibling_partial_fill_uses_updated_debt",
            replace(
                valid,
                authoritative_existing_debt=1.0,
                candidate_physical_qty=1.0,
                candidate_repair_allocation=1.0,
                shared_remaining_budget=1.0,
            ),
            True,
            False,
            "ALLOW_LATE_EXISTING_DEBT_PAYMENT_ONLY",
        ),
    ]
    rows = []
    for name, context, expected_allow, expected_delegate, expected_reason in cases:
        decision = policy.evaluate(context)
        passed = (
            decision.allow_existing_debt_payment == expected_allow
            and decision.delegate_to_default_router == expected_delegate
            and decision.reason == expected_reason
        )
        rows.append(
            {
                "case": name,
                "pass": passed,
                "context": asdict(context),
                "decision": asdict(decision),
                "expected": {
                    "allowExistingDebtPayment": expected_allow,
                    "delegateToDefaultRouter": expected_delegate,
                    "reason": expected_reason,
                },
            }
        )
    passed = sum(bool(row["pass"]) for row in rows)
    result = {
        "version": "LATE_EXISTING_DEBT_REPAIR_FENCE_MICROWORLD_V1",
        "date": "2026-09-04",
        "researchOnly": True,
        "runtimeAuthority": False,
        "behaviorChanged": False,
        "primaryModule": "RepairExecutionRouter",
        "decision": "PASS_MICROWORLD_AUTHORIZE_ONE_MARKET_SHADOW" if passed == len(rows) else "REJECT_MICROWORLD",
        "summary": {"passed": passed, "total": len(rows)},
        "cases": rows,
        "invariants": {
            "lateSpeculativeExposureFenceUnchanged": True,
            "overflowAllowedAtOrBelow180": False,
            "newResponsibilityBirthAllowedAtOrBelow180": False,
            "staleSiblingFillCanDoubleSpend": False,
            "sharedBudgetCanBeExceeded": False,
            "targetRuntimeInput": False,
        },
        "boundary": [
            "pure policy micro-world only",
            "no runtime integration",
            "no HFT",
            "no Target future input",
            "no threshold/qty/price/delay tuning",
            "no 8781",
        ],
    }
    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    output = run()
    print(json.dumps({"decision": output["decision"], "summary": output["summary"], "output": str(OUTPUT)}, indent=2))
    raise SystemExit(0 if output["summary"]["passed"] == output["summary"]["total"] else 1)
